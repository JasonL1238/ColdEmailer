"""Regression tests for batch jobs telling the truth about what they did.

Generation and sending both used to stop at the first rate-limit hit, record a
single skipped/failed entry, and then finish "done" with a full progress bar —
every remaining contact or recipient vanished without a trace.
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import HTTPException

import main
from db import Database
from email_composer import EmailComposer
from enrichment import EnrichmentService
from generation import GenerationService
from models import BulkStatus, SendRequest
from rate_limiter import RateLimiter
from resume_service import ResumeService


# ---------- generation ----------

@pytest.fixture
def gen_env(monkeypatch):
    """Real composer/db, template-only mode: no LLM and no network."""
    monkeypatch.setenv("MAX_EMAIL_GENERATIONS_PER_DAY", "3")
    monkeypatch.setenv("MAX_EMAIL_GENERATIONS_PER_MINUTE", "50")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "test.db"))
        limiter = RateLimiter(db)
        service = GenerationService(db, EmailComposer(db, ResumeService(db)),
                                    EnrichmentService(), limiter)
        yield db, limiter, service


def _contacts(db, n):
    return [db.create_contact(name=f"Person {i}", email=f"p{i}@acme.com")["id"]
            for i in range(n)]


def test_daily_cap_records_every_contact_it_never_touched(gen_env):
    db, limiter, service = gen_env
    ids = _contacts(db, 6)

    payload = {"contact_ids": ids, "email_type": "application",
               "use_template_only": True}
    job = db.create_job("generation", payload)
    service._run(job["id"], payload)

    finished = db.get_job(job["id"])
    import json
    result = json.loads(finished["result"])

    assert finished["status"] == "done"
    assert result["generated"] == 3                       # the daily cap
    assert len(result["skipped"]) == 3                    # NOT one entry
    # every contact is accounted for exactly once
    reported = set(result["email_ids"])
    assert len(reported) == 3
    assert {s["contact_id"] for s in result["skipped"]} == set(ids[3:])
    assert all("Daily limit" in s["reason"] for s in result["skipped"])
    assert all(s["name"] for s in result["skipped"])       # names, not bare ids
    # the bar only reaches 100% because nothing was silently dropped
    assert finished["progress_current"] == len(ids) == result["total"]


def test_per_minute_cap_waits_for_the_window_instead_of_dropping_the_batch(gen_env):
    db, limiter, service = gen_env
    ids = _contacts(db, 5)
    limiter.daily_limits["generations_per_day"] = 500
    limiter.daily_limits["generations_per_minute"] = 2

    real_retry = limiter.generation_retry_after
    waits = []

    def rolled_window():
        secs = real_retry()
        if secs is None:
            return None
        waits.append(secs)
        limiter.email_generations.clear()   # pretend the 60s window rolled
        return 0.01

    limiter.generation_retry_after = rolled_window

    payload = {"contact_ids": ids, "email_type": "application",
               "use_template_only": True}
    job = db.create_job("generation", payload)
    service._run(job["id"], payload)

    import json
    result = json.loads(db.get_job(job["id"])["result"])
    assert waits                      # the burst limit really was hit
    assert result["generated"] == 5   # and nobody was dropped for it
    assert result["skipped"] == []


def test_custom_type_without_ai_skips_instead_of_writing_the_wrong_email(gen_env,
                                                                        monkeypatch):
    monkeypatch.setattr("email_composer.get_cloud_llm_provider", lambda: None)
    db, limiter, service = gen_env
    ids = _contacts(db, 2)

    payload = {"contact_ids": ids, "email_type": "custom",
               "custom_instructions": "Ask about their summer research program.",
               "use_template_only": True}
    job = db.create_job("generation", payload)
    service._run(job["id"], payload)

    import json
    result = json.loads(db.get_job(job["id"])["result"])
    assert result["generated"] == 0
    assert db.list_emails() == []
    assert {s["contact_id"] for s in result["skipped"]} == set(ids)
    assert all("need AI" in s["reason"] for s in result["skipped"])


# ---------- sending ----------

class _FakeSender:
    send_delay = 0

    def __init__(self):
        self.calls = []

    def send_email(self, email, from_email, resume_path=None):
        self.calls.append({"email_id": email["id"], "resume_path": resume_path})
        return {"success": True, "email_id": email["id"],
                "gmail_message_id": "m", "gmail_thread_id": "t"}


class _CappedLimiter:
    def __init__(self, cap):
        self.cap = cap
        self.sent = 0

    def can_send_email(self):
        if self.sent >= self.cap:
            return False, f"Daily limit reached: {self.cap} emails per day."
        return True, ""

    def record_email_sent(self):
        self.sent += 1


@pytest.fixture
def send_env(monkeypatch):
    sender = _FakeSender()
    monkeypatch.setattr(main, "email_sender", sender)
    main._send_lock.acquire()           # _send_batch_job releases it in finally
    yield sender
    if main._send_lock.locked():        # only if the job never ran
        main._send_lock.release()


def _drafts(n, email_type="application"):
    contact = main.db.create_contact(name="ZZ", email="zz@example.com")
    return [main.db.create_email(contact_id=contact["id"], email_type=email_type,
                                 subject="Hi", body="Hello", status="draft")["id"]
            for _ in range(n)]


def test_send_batch_reports_every_recipient_the_daily_cap_blocked(send_env,
                                                                  monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(3))
    ids = _drafts(6)
    job = main.db.create_job("send", {"email_ids": ids})

    main._send_batch_job(job["id"], ids, None, False, "me@example.com")

    import json
    finished = main.db.get_job(job["id"])
    result = json.loads(finished["result"])
    assert finished["status"] == "done"
    assert result["sent"] == 3
    assert result["failed"] == 3                  # NOT one
    assert len(result["results"]) == len(ids)     # every id has a verdict
    unsent = [r for r in result["results"] if not r["success"]]
    assert {r["email_id"] for r in unsent} == set(ids[3:])
    assert all("Daily limit" in r["error"] for r in unsent)
    # un-attempted emails stay as drafts, ready to retry
    assert all(main.db.get_email(eid)["status"] == "draft" for eid in ids[3:])
    assert finished["progress_current"] == len(ids)


def test_sales_emails_never_get_the_resume_stapled_to_them(send_env, monkeypatch):
    """Sales bodies are written on the premise that nothing is attached."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: "/tmp/zz-resume.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})

    sales = _drafts(1, email_type="sales")
    application = _drafts(1, email_type="application")
    ids = sales + application
    job = main.db.create_job("send", {"email_ids": ids})

    main._send_batch_job(job["id"], ids, None, True, "me@example.com")

    by_id = {c["email_id"]: c["resume_path"] for c in send_env.calls}
    assert by_id[sales[0]] is None
    assert by_id[application[0]] == "/tmp/zz-resume.pdf"


def test_an_email_gmail_already_delivered_is_never_sent_twice(send_env, monkeypatch):
    """Legacy rows kept a Gmail message id while still labelled approved, so
    status alone said "sendable" for mail the recipient already had."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Delivered", email="zzdel@example.com")
    delivered = main.db.create_email(contact_id=contact["id"], subject="Hi",
                                     body="Hello", status="approved",
                                     sent_at="2026-02-17T18:17:16",
                                     gmail_message_id="19c6de4ca8e06b84")
    fresh = _drafts(1)[0]
    ids = [delivered["id"], fresh]
    job = main.db.create_job("send", {"email_ids": ids})

    main._send_batch_job(job["id"], ids, None, False, "me@example.com")

    import json
    result = json.loads(main.db.get_job(job["id"])["result"])
    assert [c["email_id"] for c in send_env.calls] == [fresh]
    assert result["sent"] == 1 and result["failed"] == 1
    verdict = next(r for r in result["results"] if r["email_id"] == delivered["id"])
    assert verdict["error"] == "already sent"
    # reply tracking still points at the message Gmail actually delivered
    assert main.db.get_email(delivered["id"])["gmail_message_id"] == "19c6de4ca8e06b84"


def test_send_request_refuses_an_already_delivered_email(monkeypatch):
    contact = main.db.create_contact(name="ZZ Dup", email="zzdup@example.com")
    delivered = main.db.create_email(contact_id=contact["id"], subject="Hi",
                                     body="Hello", status="approved",
                                     sent_at="2026-02-17T18:17:16",
                                     gmail_message_id="19c6de4ca8e06b85")

    with pytest.raises(HTTPException) as exc:
        main.send_emails(SendRequest(email_ids=[delivered["id"]]))
    assert exc.value.status_code == 400
    assert not main._send_lock.locked()


def test_a_delivered_email_cannot_be_restored_to_drafts():
    """Restore + Send was two clicks from a second copy to a real contact."""
    contact = main.db.create_contact(name="ZZ Trashed", email="zztrash@example.com")
    delivered = main.db.create_email(contact_id=contact["id"], subject="Hi",
                                     body="Hello", status="trashed",
                                     gmail_message_id="19d0938d88cb062e")

    result = asyncio.run(main.bulk_email_status(
        BulkStatus(email_ids=[delivered["id"]], status="draft")))

    assert result["updated"] == 0
    assert main.db.get_email(delivered["id"])["status"] == "trashed"


def test_follow_up_to_a_sales_email_never_gets_the_resume_stapled(send_env,
                                                                  monkeypatch):
    """follow_up is not a key in EMAIL_TYPES, so the sales guard used to be
    skipped entirely and the default resume went out with the follow-up."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: "/tmp/zz-resume.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})
    contact = main.db.create_contact(name="ZZ FU", email="zzfu@example.com")

    def _follow_up_of(email_type):
        original = main.db.create_email(contact_id=contact["id"], status="sent",
                                        email_type=email_type, subject="Hi",
                                        body="Hello", resume_id=None)
        return main.db.create_email(contact_id=contact["id"], status="draft",
                                    email_type="follow_up", is_follow_up=True,
                                    original_email_id=original["id"],
                                    subject="Re: Hi", body="Checking in")["id"]

    sales_fu = _follow_up_of("sales")
    application_fu = _follow_up_of("application")
    ids = [sales_fu, application_fu]
    job = main.db.create_job("send", {"email_ids": ids})

    main._send_batch_job(job["id"], ids, None, True, "me@example.com")

    by_id = {c["email_id"]: c["resume_path"] for c in send_env.calls}
    assert by_id[sales_fu] is None
    assert by_id[application_fu] == "/tmp/zz-resume.pdf"


def test_follow_up_with_no_surviving_original_gets_no_attachment(send_env,
                                                                 monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: "/tmp/zz-resume.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})
    contact = main.db.create_contact(name="ZZ Orphan FU", email="zzofu@example.com")
    orphan = main.db.create_email(contact_id=contact["id"], status="draft",
                                  email_type="follow_up", is_follow_up=True,
                                  subject="Re: Hi", body="Checking in")["id"]
    job = main.db.create_job("send", {"email_ids": [orphan]})

    main._send_batch_job(job["id"], [orphan], None, True, "me@example.com")

    assert send_env.calls[0]["resume_path"] is None


# ---------- rewriting a follow-up ----------

class _RecordingComposer:
    def __init__(self):
        self.calls = []

    def compose(self, contact, company, **kwargs):
        self.calls.append(("compose", kwargs.get("email_type")))
        return {"subject": "Internship inquiry at Acme", "body": "cold first contact",
                "used_template_fallback": True, "fallback_reason": "llm_unavailable"}

    def compose_follow_up(self, contact, company, original):
        self.calls.append(("compose_follow_up", original["id"]))
        return {"subject": f"Re: {original['subject']}", "body": "just following up",
                "used_template_fallback": True, "fallback_reason": "llm_unavailable"}


def test_rewriting_a_follow_up_stays_a_follow_up(monkeypatch):
    recorder = _RecordingComposer()
    monkeypatch.setattr(main, "composer", recorder)
    contact = main.db.create_contact(name="ZZ Jane", email="zzjane@example.com")
    original = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="Internship inquiry at Acme", body="hi")
    follow_up = main.db.create_email(contact_id=contact["id"], status="draft",
                                     email_type="follow_up", is_follow_up=True,
                                     original_email_id=original["id"],
                                     subject="Re: Internship inquiry at Acme",
                                     body="checking in")

    result = asyncio.run(main.regenerate_email(follow_up["id"]))

    assert recorder.calls == [("compose_follow_up", original["id"])]
    assert result["subject"].startswith("Re:")
    assert result["email_type"] == "follow_up"


def test_rewriting_an_orphaned_follow_up_is_refused(monkeypatch):
    monkeypatch.setattr(main, "composer", _RecordingComposer())
    contact = main.db.create_contact(name="ZZ Orphan", email="zzorphan@example.com")
    follow_up = main.db.create_email(contact_id=contact["id"], status="draft",
                                     email_type="follow_up", is_follow_up=True,
                                     subject="Re: something", body="checking in")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.regenerate_email(follow_up["id"]))
    assert exc.value.status_code == 400


def test_a_second_follow_up_request_is_refused(monkeypatch):
    """The Sent tab offers "Draft follow-up" forever; without a guard, two
    clicks put two near-identical follow-ups in Drafts and "Select all"
    delivers both to the same person."""
    monkeypatch.setattr(main, "composer", _RecordingComposer())
    contact = main.db.create_contact(name="ZZ Twice", email="zztwice@example.com")
    original = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="Internship inquiry at Acme",
                                    body="hi", sent_at="2020-01-01T00:00:00")

    first = asyncio.run(main.generate_follow_up(original["id"]))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.generate_follow_up(original["id"]))
    assert exc.value.status_code == 409
    assert "already been drafted" in exc.value.detail
    follow_ups = [e for e in main.db.list_emails()
                  if e["original_email_id"] == original["id"]]
    assert [e["id"] for e in follow_ups] == [first["id"]]
    # and the email row says so, so the button can stop offering it
    assert main.db.get_email(original["id"])["has_follow_up"] == 1
