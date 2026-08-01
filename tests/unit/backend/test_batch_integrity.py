"""Regression tests for batch jobs telling the truth about what they did.

Generation and sending both used to stop at the first rate-limit hit, record a
single skipped/failed entry, and then finish "done" with a full progress bar —
every remaining contact or recipient vanished without a trace.
"""
import asyncio
import itertools
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
    # Progress counts drafts, not skips: a skipped contact is not "done", and
    # a full bar on a run that wrote 3 of 6 emails overstates it.
    assert finished["progress_current"] == result["generated"] == 3
    assert result["skipped_count"] == 3


def test_a_run_that_skipped_everyone_reports_no_progress(gen_env):
    """The bar used to fill to 100% and read "3/3 done" for a run that drafted
    nothing at all, under the title "Drafts ready"."""
    db, limiter, service = gen_env
    ids = _contacts(db, 3)
    for cid in ids:
        db.update_contact(cid, {"status": "archived"})

    payload = {"contact_ids": ids, "email_type": "application",
               "use_template_only": True}
    job = db.create_job("generation", payload)
    service._run(job["id"], payload)

    import json
    finished = db.get_job(job["id"])
    result = json.loads(finished["result"])
    assert result["generated"] == 0 and len(result["skipped"]) == 3
    assert finished["progress_current"] == 0          # nothing was done
    assert finished["progress_total"] == 3
    assert db.list_emails() == []


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

    def __init__(self, result=None, sent_folder=None):
        self.calls = []
        self.result = result            # override the per-send verdict
        self.sent_folder = sent_folder  # what find_delivered_message reports
        self.lookups = []

    def send_email(self, email, from_email, resume_path=None):
        self.calls.append({
            "email_id": email["id"], "resume_path": resume_path,
            # Captured so the threading block is observable. Without these the
            # whole `if is_follow_up and original_email_id:` stanza could be
            # deleted and every test stayed green — a follow-up would then
            # arrive as a brand-new thread with no quoted context.
            "reply_to_message_id": email.get("reply_to_message_id"),
            "reply_to_thread_id": email.get("reply_to_thread_id"),
            "subject": email.get("subject"),
        })
        if self.result is not None:
            return {**self.result, "email_id": email["id"]}
        return {"success": True, "email_id": email["id"],
                "gmail_message_id": "m", "gmail_thread_id": "t"}

    def find_delivered_message(self, to_email, subject, sent_after=None):
        self.lookups.append((to_email, subject, sent_after))
        return self.sent_folder

    def get_thread_context(self, gmail_message_id):
        return {"message_id": f"<{gmail_message_id}@mail.gmail.com>",
                "thread_id": "t"}


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
    # No DNS in unit tests. Fixture addresses use the reserved .invalid TLD,
    # which correctly has no mail server, so the real deliverability check
    # would refuse every one of them.
    monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: True)
    main._send_lock.acquire()           # _send_batch_job releases it in finally
    yield sender
    if main._send_lock.locked():        # only if the job never ran
        main._send_lock.release()


_draft_seq = itertools.count()


def _drafts(n, email_type="application"):
    """n drafts, one per recipient — a batch never legitimately holds two
    first-contact emails to the same address."""
    ids = []
    for _ in range(n):
        seq = next(_draft_seq)
        contact = main.db.create_contact(name=f"ZZ {seq}", email=f"zz{seq}@example.com")
        ids.append(main.db.create_email(contact_id=contact["id"], email_type=email_type,
                                        subject="Hi", body="Hello", status="draft")["id"])
    return ids


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

    def _follow_up_of(email_type):
        # One contact each: two follow-ups to the same person are refused now,
        # and that is a different guard from this one.
        contact = main.db.create_contact(name=f"ZZ FU {email_type}",
                                         email=f"zzfu-{email_type}@example.com")
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


# ---------- send idempotency ----------

def _lost_response_sender(monkeypatch, sent_folder=None):
    sender = _FakeSender(result={"success": False, "delivery_unknown": True,
                                 "error": "timed out reading response from Gmail"},
                         sent_folder=sent_folder)
    monkeypatch.setattr(main, "email_sender", sender)
    return sender


def _run_batch(ids, confirm_resend=False):
    # _send_batch_job releases the send lock in its finally, so re-take it for
    # every run the test drives directly.
    if not main._send_lock.locked():
        main._send_lock.acquire()
    job = main.db.create_job("send", {"email_ids": ids})
    main._send_batch_job(job["id"], ids, None, False, "me@example.com", confirm_resend)
    import json
    return json.loads(main.db.get_job(job["id"])["result"])


def test_a_lost_send_response_marks_the_row_instead_of_looking_retryable(send_env,
                                                                        monkeypatch):
    """Gmail may already have queued the message, so nothing may present the
    row as a clean draft to retry."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    _lost_response_sender(monkeypatch)
    eid = _drafts(1)[0]

    result = _run_batch([eid])

    assert result["sent"] == 0 and result["failed"] == 1
    assert result["results"][0]["delivery_unknown"] is True
    row = main.db.get_email(eid)
    assert row["send_attempted_at"]
    assert "timed out" in row["send_attempt_error"]
    # not delivered either — the record must not claim a send we can't confirm
    assert row["gmail_message_id"] is None and row["sent_at"] is None


def test_a_second_batch_will_not_re_send_an_unconfirmed_row(send_env, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    _lost_response_sender(monkeypatch)
    eid = _drafts(1)[0]
    _run_batch([eid])

    good = _FakeSender()
    monkeypatch.setattr(main, "email_sender", good)
    result = _run_batch([eid])

    assert good.calls == []                       # nothing handed to Gmail twice
    assert result["sent"] == 0 and result["failed"] == 1
    assert result["results"][0]["delivery_unknown"] is True
    assert "Check Gmail" in result["results"][0]["error"]


def test_an_unconfirmed_row_found_in_the_sent_folder_is_recorded_not_re_sent(send_env,
                                                                            monkeypatch):
    """The app already holds gmail.readonly, so "did it land?" is answerable."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    _lost_response_sender(monkeypatch)
    eid = _drafts(1)[0]
    _run_batch([eid])

    reconciler = _FakeSender(sent_folder={"gmail_message_id": "found1",
                                          "gmail_thread_id": "foundthread"})
    monkeypatch.setattr(main, "email_sender", reconciler)
    result = _run_batch([eid], confirm_resend=True)

    assert reconciler.calls == []                 # not sent again
    assert reconciler.lookups                     # the Sent folder was consulted
    row = main.db.get_email(eid)
    assert row["status"] == "sent" and row["gmail_message_id"] == "found1"
    assert row["send_attempted_at"] is None
    assert result["results"][0]["retryable"] is False


def test_an_explicit_confirmation_retries_a_row_gmail_never_took(send_env, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    _lost_response_sender(monkeypatch)
    eid = _drafts(1)[0]
    _run_batch([eid])

    good = _FakeSender()
    monkeypatch.setattr(main, "email_sender", good)
    result = _run_batch([eid], confirm_resend=True)

    assert [c["email_id"] for c in good.calls] == [eid]
    assert result["sent"] == 1
    row = main.db.get_email(eid)
    assert row["status"] == "sent" and row["send_attempted_at"] is None


def test_a_refused_send_stays_an_ordinary_retryable_failure(send_env, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    monkeypatch.setattr(main, "email_sender", _FakeSender(
        result={"success": False, "delivery_unknown": False,
                "error": "Invalid recipient address"}))
    eid = _drafts(1)[0]

    result = _run_batch([eid])

    assert not result["results"][0].get("delivery_unknown")
    assert main.db.get_email(eid)["send_attempted_at"] is None


def test_the_row_is_marked_before_gmail_sees_the_message(send_env, monkeypatch):
    """send_attempted_at was written only *after* an indeterminate reply came
    back, so between "Gmail accepted the POST" and the status update the row was
    an ordinary clean draft — and the send thread is a daemon thread under
    uvicorn --reload."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    snapshots = {}

    class _Snapshotting(_FakeSender):
        def send_email(self, email, from_email, resume_path=None):
            # exactly the instant Gmail holds the message
            snapshots[email["id"]] = main.db.get_email(email["id"])
            return super().send_email(email, from_email, resume_path)

    monkeypatch.setattr(main, "email_sender", _Snapshotting())
    eid = _drafts(1)[0]

    _run_batch([eid])

    assert snapshots[eid]["send_attempted_at"]          # the write-ahead marker
    # and a confirmed success clears it again, so the row is not left "unknown"
    row = main.db.get_email(eid)
    assert row["status"] == "sent" and row["send_attempted_at"] is None


def test_a_process_death_mid_send_does_not_re_arm_the_message(send_env, monkeypatch):
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))

    class _Dies(_FakeSender):
        def send_email(self, email, from_email, resume_path=None):
            self.calls.append({"email_id": email["id"], "resume_path": resume_path})
            raise KeyboardInterrupt("uvicorn --reload killed the worker")

    dying = _Dies()
    monkeypatch.setattr(main, "email_sender", dying)
    eid = _drafts(1)[0]
    if not main._send_lock.locked():
        main._send_lock.acquire()
    job = main.db.create_job("send", {"email_ids": [eid]})
    with pytest.raises(KeyboardInterrupt):
        main._send_batch_job(job["id"], [eid], None, False, "me@example.com")

    # the job row must not be left 'running' — that 409s every later send
    assert main.db.get_job(job["id"])["status"] == "failed"
    row = main.db.get_email(eid)
    assert row["send_attempted_at"], "the row still looks like a clean draft"

    # the next batch asks the Sent folder instead of sending a second copy
    good = _FakeSender()
    monkeypatch.setattr(main, "email_sender", good)
    result = _run_batch([eid])
    assert good.calls == []
    assert good.lookups
    assert result["results"][0]["delivery_unknown"] is True


def test_an_older_copy_of_the_same_subject_is_not_proof_this_row_landed(send_env,
                                                                       monkeypatch):
    """Template subjects are deterministic ("Re: {subject}"), so the Sent-folder
    search matches an older message to the same person. Accepting it marked a row
    sent with today's timestamp, copied the old message's ids onto it (two rows
    sharing one gmail_message_id, so one reply marks both), burned a daily-cap
    slot, and blocked the email from ever actually being sent."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Same Subject",
                                     email="zzsamesubj@example.com")
    older = main.db.create_email(contact_id=contact["id"], status="sent",
                                 subject="Internship inquiry at Acme", body="Hello",
                                 sent_at="2026-03-01T09:00:00",
                                 gmail_message_id="ZZ-OLD-ID",
                                 gmail_thread_id="ZZ-OLD-THREAD")
    pending = main.db.create_email(contact_id=contact["id"], status="draft",
                                   subject="Internship inquiry at Acme", body="Hello")
    # exactly the state the delivery_unknown branch writes
    main.db.update_email(pending["id"], {"send_attempted_at": "2026-07-29T16:40:00",
                                        "send_attempt_error": "timed out"})

    reconciler = _FakeSender(sent_folder={"gmail_message_id": "ZZ-OLD-ID",
                                          "gmail_thread_id": "ZZ-OLD-THREAD"})
    monkeypatch.setattr(main, "email_sender", reconciler)
    result = _run_batch([pending["id"]])

    row = main.db.get_email(pending["id"])
    assert row["status"] == "draft" and row["gmail_message_id"] is None
    assert result["results"][0]["delivery_unknown"] is True
    # the attempt time is handed down so the lookup can bound itself
    assert reconciler.lookups[0][2] == "2026-07-29T16:40:00"
    # the older row keeps its own identity
    assert main.db.get_email(older["id"])["gmail_message_id"] == "ZZ-OLD-ID"

    # and an explicit confirmation still sends it for real
    result = _run_batch([pending["id"]], confirm_resend=True)
    assert [c["email_id"] for c in reconciler.calls] == [pending["id"]]
    assert result["sent"] == 1


# ---------- the body's attachment claim ----------

def _claiming_draft(resume_id=None, body=None):
    contact = main.db.create_contact(name="ZZ Claim",
                                     email=f"zzclaim{next(_draft_seq)}@example.com")
    return main.db.create_email(
        contact_id=contact["id"], status="draft", resume_id=resume_id,
        subject="Internship inquiry at Acme",
        body=body or ("Hi there,\n\nI'd love to contribute at Acme. My resume is "
                      "attached for convenience.\n\nThanks so much,\nJason"))


def test_a_draft_that_promises_an_attachment_says_so_on_the_row():
    claiming = _claiming_draft()
    plain = main.db.create_email(
        contact_id=main.db.create_contact(email="zzplain@example.com")["id"],
        status="draft", subject="Hi", body="Nothing is attached to this one.")

    assert main.db.get_email(claiming["id"])["claims_attachment"] == 1
    assert main.db.get_email(plain["id"])["claims_attachment"] == 0


def test_sending_with_the_attachment_switched_off_is_refused(monkeypatch):
    """The body was written around a real PDF. Unticking "Attach resume" made the
    recipient's copy say a resume is attached with nothing attached, with no
    warning anywhere — while deleting that same resume is refused with a 409."""
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})
    draft = _claiming_draft(resume_id="r-one")

    with pytest.raises(HTTPException) as exc:
        main.send_emails(SendRequest(email_ids=[draft["id"]], attach_resume=False))
    assert exc.value.status_code == 400
    assert "promise an attachment" in exc.value.detail
    assert "nothing would be attached" in exc.value.detail
    assert not main._send_lock.locked()


def test_stapling_a_different_resume_to_every_draft_is_refused(monkeypatch):
    """"Attach X to all" substitutes a PDF the email was never written around —
    exactly what DELETE /api/resumes/{id} refuses with a 409."""
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})
    draft = _claiming_draft(resume_id="r-one")

    with pytest.raises(HTTPException) as exc:
        main.send_emails(SendRequest(email_ids=[draft["id"]], resume_id="r-two"))
    assert exc.value.status_code == 400
    assert "different resume" in exc.value.detail


def test_a_draft_written_around_the_default_resume_is_protected_too(monkeypatch):
    """Every pre-existing row carries resume_id NULL, so a guard keyed on the
    column alone missed the drafts written around the default."""
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})
    draft = _claiming_draft(resume_id=None)

    with pytest.raises(HTTPException) as exc:
        main.send_emails(SendRequest(email_ids=[draft["id"]], resume_id="r-two"))
    assert exc.value.status_code == 400


def test_an_explicit_confirmation_lets_the_mismatch_through(monkeypatch):
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})
    draft = _claiming_draft(resume_id="r-one")

    with pytest.raises(HTTPException) as exc:
        main.send_emails(SendRequest(email_ids=[draft["id"]], attach_resume=False,
                                     confirm_attachment_change=True))
    # past the attachment guard: it now fails on Gmail setup, not on the claim
    assert "attachment" not in exc.value.detail
    assert not main._send_lock.locked()


def test_matching_attachments_are_not_flagged(monkeypatch):
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "r-default"})
    bound = main.db.get_email(_claiming_draft(resume_id="r-one")["id"])

    assert main._attachment_claim_conflict(bound, True, None) is None
    assert main._attachment_claim_conflict(bound, True, "r-one") is None
    assert main._attachment_claim_conflict(bound, False, None)
    # a body that promises nothing is never blocked
    plain = main.db.get_email(_claiming_draft(
        body="Hi,\n\nQuick idea for your team.\n\nThanks,")["id"])
    assert main._attachment_claim_conflict(plain, False, None) is None


def test_deleting_the_default_resume_protects_drafts_that_promise_it(monkeypatch):
    resume = main.db.create_resume("ZZTEST Default", "zz.pdf", "/tmp/zz.pdf",
                                   "text", is_default=True)
    claiming = _claiming_draft(resume_id=None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.delete_resume(resume["id"], force=False))
    assert exc.value.status_code == 409
    assert "unsent draft" in exc.value.detail

    main.db.delete_email(claiming["id"])
    main.db.delete_resume(resume["id"])


def test_one_batch_never_sends_two_first_contact_emails_to_one_address(send_env,
                                                                      monkeypatch):
    """A cancelled generation racing its retry left two identical drafts for the
    same contact, and "Select all" arms both."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Twin", email="zztwin@example.com")
    ids = [main.db.create_email(contact_id=contact["id"], subject="Hi",
                                body="Hello", status="draft")["id"]
           for _ in range(2)]

    result = _run_batch(ids)

    assert len(send_env.calls) == 1
    assert result["sent"] == 1 and result["failed"] == 1
    skipped = next(r for r in result["results"] if not r["success"])
    assert "already went out in this batch" in skipped["error"]
    assert main.db.get_email(ids[1])["status"] == "draft"


def test_a_follow_up_still_goes_out_after_its_first_contact_email(send_env, monkeypatch):
    """The in-batch guard is about duplicate *first* contacts; a follow-up to
    the same person is exactly what the app is for."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Pair", email="zzpair@example.com")
    first = main.db.create_email(contact_id=contact["id"], subject="Hi",
                                 body="Hello", status="draft")
    follow_up = main.db.create_email(contact_id=contact["id"], status="draft",
                                     email_type="follow_up", is_follow_up=True,
                                     original_email_id=first["id"],
                                     subject="Re: Hi", body="Checking in")

    _run_batch([first["id"], follow_up["id"]])

    assert {c["email_id"] for c in send_env.calls} == {first["id"], follow_up["id"]}


def test_one_batch_never_sends_two_follow_ups_to_one_person(send_env, monkeypatch):
    """Follow-ups were exempt from the in-batch recipient guard on both sides,
    so two "I wanted to follow up on my note" messages went to the same human
    three seconds apart, reported as a fully successful send."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Twice FU", email="zztwicefu@example.com")
    ids = []
    for n in (1, 2):
        original = main.db.create_email(contact_id=contact["id"], status="sent",
                                        subject=f"Note {n}", body="Hello",
                                        sent_at=f"2026-0{n}-01T09:00:00")
        ids.append(main.db.create_email(
            contact_id=contact["id"], status="draft", email_type="follow_up",
            is_follow_up=True, original_email_id=original["id"],
            subject=f"Re: Note {n}", body="Checking in")["id"])

    result = _run_batch(ids)

    assert len(send_env.calls) == 1
    assert result["sent"] == 1 and result["failed"] == 1
    skipped = next(r for r in result["results"] if not r["success"])
    assert "follow-up to this recipient already went out" in skipped["error"]
    assert main.db.get_email(ids[1])["status"] == "draft"


def test_a_follow_up_is_refused_once_that_person_has_replied(send_env, monkeypatch):
    """Drafting and sending are days apart: the batch job runs Monday, the
    reply check confirms an answer Tuesday, and "Select all" on Tuesday
    afternoon still had every reason to send. Both drafting routes refuse in
    this state; this is the last gate before real mail, and it was the only one
    that never asked. The email would thread into the very conversation they
    answered, asking whether they are still interested."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Replied Send",
                                     email="zzrepliedsend@example.com")
    original = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="Quick question", body="hi",
                                    sent_at="2026-07-01T09:00:00",
                                    gmail_message_id="gm-orig",
                                    has_response=True,
                                    response_at="2026-07-03T09:00:00",
                                    response_verified_at="2026-07-03T09:05:00")
    follow_up = main.db.create_email(contact_id=contact["id"], status="draft",
                                     email_type="follow_up", is_follow_up=True,
                                     follow_up_step=1,
                                     original_email_id=original["id"],
                                     subject="Re: Quick question",
                                     body="still interested?")

    result = _run_batch([follow_up["id"]])

    assert send_env.calls == []
    assert result["sent"] == 0 and result["failed"] == 1
    assert "replied since this follow-up was drafted" in result["results"][0]["error"]
    assert main.db.get_email(follow_up["id"])["status"] == "draft"


def test_a_first_contact_email_is_not_blocked_by_an_old_reply(send_env, monkeypatch):
    """The reply gate is about follow-ups. A fresh first-contact email to
    someone who answered a different pitch months ago is legitimate."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Old Reply", email="zzoldreply@example.com")
    main.db.create_email(contact_id=contact["id"], status="sent", subject="Older",
                         body="hi", sent_at="2026-01-01T09:00:00", has_response=True,
                         response_at="2026-01-02T09:00:00",
                         response_verified_at="2026-01-02T09:05:00")
    fresh = main.db.create_email(contact_id=contact["id"], status="draft",
                                 subject="New role", body="hello")

    result = _run_batch([fresh["id"]])

    assert result["sent"] == 1
    assert [c["email_id"] for c in send_env.calls] == [fresh["id"]]


def test_a_follow_up_is_threaded_onto_the_original_conversation(send_env, monkeypatch):
    """Deleting the whole threading block left 810 tests green, and a follow-up
    with a "Re:" subject, no In-Reply-To and no thread id starts a brand-new
    conversation with no quoted context — which is a spam pattern to filters
    and to the recipient alike."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    contact = main.db.create_contact(name="ZZ Thread", email="zzthread@example.com")
    original = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="Intro", body="hi",
                                    sent_at="2026-07-01T09:00:00",
                                    gmail_message_id="gm-thread")
    follow_up = main.db.create_email(contact_id=contact["id"], status="draft",
                                     email_type="follow_up", is_follow_up=True,
                                     follow_up_step=1,
                                     original_email_id=original["id"],
                                     subject="Re: Intro", body="following up")

    _run_batch([follow_up["id"]])

    call = next(c for c in send_env.calls if c["email_id"] == follow_up["id"])
    # the RFC Message-ID header read back from Gmail, not the API id
    assert call["reply_to_message_id"] == "<gm-thread@mail.gmail.com>"
    assert call["reply_to_thread_id"] == "t"
    # and the thread id is backfilled onto the original, which had none
    assert main.db.get_email(original["id"])["gmail_thread_id"] == "t"


def test_an_unconfirmed_first_copy_still_blocks_the_duplicate(send_env, monkeypatch):
    """The in-batch guard recorded the recipient only on success. A first copy
    whose delivery Gmail never confirmed may well have been queued and
    delivered — leaving the guard unarmed while a duplicate went out for
    real."""
    monkeypatch.setattr(main, "rate_limiter", _CappedLimiter(10))
    monkeypatch.setattr(send_env, "result", {
        "success": False, "delivery_unknown": True,
        "error": "read timeout after handing the message to Gmail"})
    contact = main.db.create_contact(name="ZZ Unconf", email="zzunconf@example.com")
    original = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="Note", body="hi",
                                    sent_at="2026-07-01T09:00:00")
    ids = [main.db.create_email(contact_id=contact["id"], status="draft",
                                email_type="follow_up", is_follow_up=True,
                                original_email_id=original["id"],
                                subject=f"Re: Note {n}", body="following up")["id"]
           for n in (1, 2)]

    result = _run_batch(ids)

    # exactly one attempt reached the sender; the second was refused by the guard
    assert len(send_env.calls) == 1
    assert result["sent"] == 0 and result["failed"] == 2
    second = next(r for r in result["results"]
                  if r.get("email_id") == ids[1] and not r["success"])
    assert "follow-up to this recipient already went out" in second["error"]


# ---------- follow-ups are per person ----------

def test_a_second_follow_up_to_the_same_person_is_refused(monkeypatch):
    """A contact with several sent first-contact emails was offered one
    follow-up per email — four enabled "Draft follow-up" buttons on one real
    address in the user's own data."""
    monkeypatch.setattr(main, "composer", _RecordingComposer())
    contact = main.db.create_contact(name="ZZ Multi", email="zzmulti@example.com")
    first = main.db.create_email(contact_id=contact["id"], status="sent",
                                 subject="Internship inquiry at Acme", body="hi",
                                 sent_at="2020-01-01T00:00:00")
    second = main.db.create_email(contact_id=contact["id"], status="sent",
                                  subject="Internship inquiry at Acme", body="hi",
                                  sent_at="2020-02-01T00:00:00")

    asyncio.run(main.generate_follow_up(first["id"]))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.generate_follow_up(second["id"]))
    assert exc.value.status_code == 409
    assert "already been drafted" in exc.value.detail
    follow_ups = [e for e in main.db.list_emails()
                  if e["contact_id"] == contact["id"] and e["is_follow_up"]]
    assert len(follow_ups) == 1
    # ...and every one of that contact's rows says so, so the UI stops offering it
    assert main.db.get_email(first["id"])["has_follow_up"] == 1
    assert main.db.get_email(second["id"])["has_follow_up"] == 1


def test_a_trashed_follow_up_frees_the_person_up_again(monkeypatch):
    monkeypatch.setattr(main, "composer", _RecordingComposer())
    contact = main.db.create_contact(name="ZZ Retry", email="zzretryfu@example.com")
    original = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="Only note", body="hi",
                                    sent_at="2020-01-01T00:00:00")
    first = asyncio.run(main.generate_follow_up(original["id"]))
    main.db.update_email(first["id"], {"status": "trashed"})

    again = asyncio.run(main.generate_follow_up(original["id"]))
    assert again["is_follow_up"] == 1


# ---------- company delete ----------

def test_deleting_a_company_refuses_to_erase_sent_history():
    """Every other delete path protects delivered mail; the cascade through
    companies -> contacts -> emails used to walk straight through it."""
    company = main.db.create_company("ZZTEST Cascade Co")
    contact = main.db.create_contact(company_id=company["id"], name="ZZ Cascade",
                                     email="zzcascade@example.com")
    delivered = main.db.create_email(contact_id=contact["id"], status="sent",
                                     subject="Hi", body="Hello",
                                     sent_at="2026-03-01T09:00:00",
                                     gmail_message_id="zzmsg1", has_response=True)

    # force is passed explicitly: calling the handler directly bypasses FastAPI,
    # so the Query(False) default arrives as a Query object (truthy).
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.delete_company(company["id"], force=False))

    assert exc.value.status_code == 409
    assert "1 sent email" in exc.value.detail and "1 reply" in exc.value.detail
    assert "archive" in exc.value.detail
    assert main.db.get_company(company["id"]) is not None
    assert main.db.get_email(delivered["id"]) is not None

    # an explicit confirmation still works, mirroring the contact guard
    assert asyncio.run(main.delete_company(company["id"], force=True))["success"]
    assert main.db.get_company(company["id"]) is None
    assert main.db.get_email(delivered["id"]) is None


def test_deleting_a_company_with_only_drafts_is_not_blocked():
    company = main.db.create_company("ZZTEST Drafts Only Co")
    contact = main.db.create_contact(company_id=company["id"], name="ZZ Draft",
                                     email="zzdraftonly@example.com")
    main.db.create_email(contact_id=contact["id"], status="draft",
                         subject="Hi", body="Hello")

    assert asyncio.run(main.delete_company(company["id"], force=False))["success"] is True
    assert main.db.get_company(company["id"]) is None


# ---------- rewriting a follow-up ----------

class _RecordingComposer:
    def __init__(self):
        self.calls = []
        self.follow_up_kwargs = []

    def compose(self, contact, company, **kwargs):
        self.calls.append(("compose", kwargs.get("email_type")))
        return {"subject": "Internship inquiry at Acme", "body": "cold first contact",
                "used_template_fallback": True, "fallback_reason": "llm_unavailable"}

    def compose_follow_up(self, contact, company, original, **kwargs):
        self.calls.append(("compose_follow_up", original["id"]))
        self.follow_up_kwargs.append(kwargs)
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


def test_no_follow_up_when_the_person_replied_to_a_different_email(monkeypatch):
    """A follow-up is written on the premise of silence ("still very
    interested"). The reply often sits on a sibling email, so the row-level
    has_response flag says nothing about whether they wrote back."""
    monkeypatch.setattr(main, "composer", _RecordingComposer())
    contact = main.db.create_contact(name="ZZ Replied", email="zzreplied@example.com")
    answered = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="First note", body="hi",
                                    sent_at="2020-01-01T00:00:00", has_response=True,
                                    response_verified_at="2020-01-03T00:00:00")
    silent = main.db.create_email(contact_id=contact["id"], status="sent",
                                  subject="Second note", body="hi",
                                  sent_at="2020-02-01T00:00:00")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.generate_follow_up(silent["id"]))
    assert exc.value.status_code == 409
    assert "already replied" in exc.value.detail
    assert [e for e in main.db.list_emails()
            if e["contact_id"] == contact["id"] and e["is_follow_up"]] == []

    # and the row the UI reads from says so, so the button can stop offering it
    assert main.db.get_email(silent["id"])["contact_has_replied"] == 1
    assert main.db.get_email(silent["id"])["has_response"] == 0
    assert main.db.get_email(answered["id"])["contact_has_replied"] == 1


def test_a_follow_up_is_still_offered_to_someone_who_never_replied(monkeypatch):
    monkeypatch.setattr(main, "composer", _RecordingComposer())
    contact = main.db.create_contact(name="ZZ Quiet", email="zzquiet@example.com")
    original = main.db.create_email(contact_id=contact["id"], status="sent",
                                    subject="Only note", body="hi",
                                    sent_at="2020-01-01T00:00:00")

    follow_up = asyncio.run(main.generate_follow_up(original["id"]))

    assert follow_up["is_follow_up"] == 1
    assert main.db.get_email(original["id"])["contact_has_replied"] == 0
