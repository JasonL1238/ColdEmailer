"""Reply flags must be verifiable, and never presented as fact until they are.

The app inherited 115 has_response=1 rows from a checker that counted bounces,
auto-replies and our own messages in the thread; 113 of them share a 16-minute
response_at window — the wall clock of the old polling loop, not reply times.
Those flags drove the headline reply rate, a per-email "replied <date>" chip,
114 contact chips, and (most damaging) they switched the follow-up pipeline off.
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import pytest

import main
from db import (Database, _batch_stamped_reply_ids,
                repair_contact_reply_status, repair_unverified_legacy_replies)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


# ---------- the flags are marked unverified, not fact ----------

def test_a_legacy_reply_flag_is_not_reported_as_a_verified_reply(db):
    contact = db.create_contact(email="legacy@example.com", status="replied")
    legacy = db.create_email(contact_id=contact["id"], status="sent",
                             gmail_message_id="g1", sent_at="2026-03-11T17:29:00",
                             has_response=True, response_at="2026-03-12T16:26:02")

    row = db.get_email(legacy["id"])
    assert row["has_response"] == 1
    assert row["reply_unverified"] == 1          # the UI must not print the date
    assert row["contact_has_replied"] == 0       # and it must not gate follow-ups


def test_a_checked_reply_is_reported_as_verified(db):
    contact = db.create_contact(email="real@example.com")
    row = db.create_email(contact_id=contact["id"], status="sent",
                          gmail_message_id="g2", has_response=True,
                          response_at="2026-03-12T16:26:02",
                          response_verified_at="2026-07-29T10:00:00")

    fetched = db.get_email(row["id"])
    assert fetched["reply_unverified"] == 0
    assert fetched["contact_has_replied"] == 1


def test_the_batch_stamp_signature_is_detectable(db):
    """A genuine Gmail internalDate does not put dozens of replies in one
    minute; the old checker's `datetime.now()` did."""
    contact = db.create_contact(email="c@example.com")
    batch = [db.create_email(contact_id=contact["id"], status="sent",
                             gmail_message_id=f"b{i}", has_response=True,
                             response_at=f"2026-03-12T16:26:{i:02d}")["id"]
             for i in range(4)]
    lone = db.create_email(contact_id=contact["id"], status="sent",
                           gmail_message_id="lone", has_response=True,
                           response_at="2026-04-02T09:13:44")

    flagged = set(_batch_stamped_reply_ids(db))
    assert flagged == set(batch)
    assert lone["id"] not in flagged


def test_repair_reports_the_unverified_flags_once(db):
    contact = db.create_contact(email="c@example.com")
    for i in range(3):
        db.create_email(contact_id=contact["id"], status="sent",
                        gmail_message_id=f"g{i}", has_response=True,
                        response_at=f"2026-03-12T16:26:{i:02d}")

    assert repair_unverified_legacy_replies(db) == 3
    events = [e for e in db.recent_events(limit=10) if e["event"] == "repair"]
    assert len(events) == 1
    assert "unverified" in events[0]["detail"]
    assert "batch timestamp" in events[0]["detail"]
    assert "Re-verify replies" in events[0]["detail"]
    # idempotent: a restart must not spam the feed
    repair_unverified_legacy_replies(db)
    assert len([e for e in db.recent_events(limit=10) if e["event"] == "repair"]) == 1
    # and it makes no Gmail calls, so it can never clear a flag by mistake
    assert db.query_one("SELECT COUNT(*) AS n FROM emails WHERE has_response=1")["n"] == 3


def test_contact_status_repair_no_longer_spreads_unverified_flags(db):
    """This repair used to propagate the false positives from emails onto the
    contact chips, which is where 114 green "Replied" badges came from."""
    unverified = db.create_contact(email="a@example.com", status="sent")
    verified = db.create_contact(email="b@example.com", status="sent")
    db.create_email(contact_id=unverified["id"], status="sent", has_response=True,
                    response_at="2026-03-12T16:26:02")
    db.create_email(contact_id=verified["id"], status="sent", has_response=True,
                    response_at="2026-03-12T16:26:02",
                    response_verified_at="2026-07-29T10:00:00")

    repair_contact_reply_status(db)

    assert db.get_contact(unverified["id"])["status"] == "sent"
    assert db.get_contact(verified["id"])["status"] == "replied"


def test_list_contacts_separates_verified_from_unverified_replies(db):
    contact = db.create_contact(email="a@example.com", status="replied")
    db.create_email(contact_id=contact["id"], status="sent", has_response=True,
                    response_at="2026-03-12T16:26:02")

    row = next(c for c in db.list_contacts() if c["id"] == contact["id"])
    assert row["verified_reply_count"] == 0
    assert row["unverified_reply_count"] == 1


# ---------- the correction is reachable ----------

class _FakeChecker:
    """Scripted stand-in for ResponseChecker: {gmail_message_id: (verdict, when)}."""

    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.checked = []

    def __call__(self, service, own_address=None):
        return self

    def check_response(self, gmail_message_id, gmail_thread_id=None):
        self.checked.append(gmail_message_id)
        return self.verdicts.get(gmail_message_id, (False, None))


class _FakeGmail:
    service = object()
    send_delay = 0

    def authenticate(self):
        return self.service


def _reply_env(monkeypatch, verdicts):
    checker = _FakeChecker(verdicts)
    monkeypatch.setattr(main, "email_sender", _FakeGmail())
    monkeypatch.setattr(main, "ResponseChecker", checker)
    return checker


def _legacy_row(subject="ZZTEST legacy"):
    contact = main.db.create_contact(name="ZZ Legacy", email="zzlegacy@example.invalid",
                                     status="replied")
    email = main.db.create_email(contact_id=contact["id"], status="sent",
                                 subject=subject, body="hello",
                                 sent_at="2026-03-11T17:29:00",
                                 gmail_message_id=f"zzg-{contact['id'][:8]}",
                                 has_response=True,
                                 response_at="2026-03-12T16:26:02")
    return contact, email


def test_a_default_reply_check_re_examines_unverified_flags(monkeypatch):
    """With the old `has_response=0` filter these rows could never be looked at
    again, so the `cleared` counter and its toast were dead code."""
    contact, email = _legacy_row()
    checker = _reply_env(monkeypatch, {})     # Gmail says: no real reply there

    data = main.check_replies(recheck=False)

    assert email["gmail_message_id"] in checker.checked
    assert data["cleared"] >= 1
    row = main.db.get_email(email["id"])
    assert row["has_response"] == 0 and row["response_at"] is None
    assert main.db.get_contact(contact["id"])["status"] == "sent"
    main.db.delete_contact(contact["id"])


def test_a_confirmed_legacy_flag_becomes_verified_with_the_real_reply_date(monkeypatch):
    contact, email = _legacy_row()
    when = datetime(2026, 3, 14, 8, 15, 0)
    _reply_env(monkeypatch, {email["gmail_message_id"]: (True, when)})

    data = main.check_replies(recheck=False)

    assert data["confirmed"] >= 1
    row = main.db.get_email(email["id"])
    assert row["response_at"] == when.isoformat(timespec="seconds")
    assert row["response_verified_at"]
    assert row["reply_unverified"] == 0
    main.db.delete_contact(contact["id"])


def test_a_failed_check_never_clears_an_unverified_flag(monkeypatch):
    contact, email = _legacy_row()
    _reply_env(monkeypatch, {email["gmail_message_id"]: (None, None)})

    data = main.check_replies(recheck=False)

    assert data["failed_checks"] >= 1
    row = main.db.get_email(email["id"])
    assert row["has_response"] == 1 and row["reply_unverified"] == 1
    main.db.delete_contact(contact["id"])


def test_a_new_reply_is_recorded_as_verified(monkeypatch):
    contact = main.db.create_contact(name="ZZ New", email="zznew@example.invalid")
    email = main.db.create_email(contact_id=contact["id"], status="sent",
                                 subject="ZZTEST note", body="hello",
                                 sent_at="2026-07-01T09:00:00",
                                 gmail_message_id="zzg-new")
    when = datetime.now() - timedelta(days=1)
    _reply_env(monkeypatch, {"zzg-new": (True, when)})

    data = main.check_replies(recheck=False)

    assert data["new_replies"] >= 1
    row = main.db.get_email(email["id"])
    assert row["reply_unverified"] == 0 and row["contact_has_replied"] == 1
    assert main.db.get_contact(contact["id"])["status"] == "replied"
    main.db.delete_contact(contact["id"])


# ---------- nothing downstream states an unverified flag as fact ----------

def test_the_dashboard_keeps_unverified_flags_out_of_the_reply_rate():
    contact, email = _legacy_row()
    data = asyncio.run(main.dashboard())

    assert data["counts"]["replied_unverified"] >= 1
    verified = main.db.query_one(
        "SELECT COUNT(*) AS n FROM emails WHERE has_response=1 "
        "AND response_verified_at IS NOT NULL")["n"]
    assert data["counts"]["replied"] == verified
    # the timeline must not plot a fabricated reply day either
    assert all(day["replies"] == 0 for day in data["timeline"]
               if day["day"] == "2026-03-12")
    main.db.delete_contact(contact["id"])


def test_an_unverified_flag_does_not_block_a_follow_up(monkeypatch):
    """The user's own history refutes these flags — every follow-up they sent
    went to a contact marked "replied" a week earlier. A 409 here (and the
    matching "this person already replied" chip) removed the app's core
    recurring action for ~90% of the pipeline."""
    class _Composer:
        def compose_follow_up(self, contact, company, original):
            return {"subject": f"Re: {original['subject']}", "body": "just following up",
                    "used_template_fallback": True, "fallback_reason": "llm_unavailable"}

    monkeypatch.setattr(main, "composer", _Composer())
    contact, email = _legacy_row(subject="ZZTEST unverified original")

    follow_up = asyncio.run(main.generate_follow_up(email["id"]))

    assert follow_up["is_follow_up"] == 1
    main.db.delete_contact(contact["id"])
