"""Regression tests for data-loss and quota-integrity fixes."""
import os
import tempfile

import pytest

from db import Database, now_iso
from main import _csv_safe
from rate_limiter import RateLimiter


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


class TestCsvFormulaInjection:
    @pytest.mark.parametrize("payload", [
        '=cmd|\'/C calc\'!A0',
        '+HYPERLINK("http://evil.example")',
        '-2+3+cmd|\' /C calc\'!A0',
        '@SUM(1+1)',
    ])
    def test_prefixes_dangerous_leading_characters(self, payload):
        assert _csv_safe(payload).startswith("'")

    def test_leaves_ordinary_values_untouched(self):
        assert _csv_safe("Jane Smith") == "Jane Smith"
        assert _csv_safe("jane@acme.com") == "jane@acme.com"

    def test_handles_none_and_numbers(self):
        assert _csv_safe(None) == ""
        assert _csv_safe(42) == "42"


class TestPersistentDailyLimits:
    """The daily send cap must survive a process restart — an in-memory
    counter would let a reload reset it and blow past the limit."""

    def test_send_cap_counts_rows_already_in_the_database(self, db, monkeypatch):
        monkeypatch.setenv("MAX_EMAILS_PER_DAY", "2")
        contact = db.create_contact(email="a@b.com")
        for _ in range(2):
            db.create_email(contact_id=contact["id"], status="sent",
                            sent_at=now_iso())

        limiter = RateLimiter(db)
        can, err = limiter.can_send_email()
        assert can is False
        assert "Daily limit" in err

    def test_fresh_limiter_sees_existing_sends(self, db, monkeypatch):
        monkeypatch.setenv("MAX_EMAILS_PER_DAY", "5")
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="sent", sent_at=now_iso())

        # Simulating a server restart: brand new limiter, no in-memory state
        assert RateLimiter(db).sent_today() == 1

    def test_yesterdays_sends_do_not_count(self, db):
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="sent",
                        sent_at="2020-01-01T09:00:00")
        assert RateLimiter(db).sent_today() == 0

    def test_falls_back_to_memory_without_a_database(self):
        limiter = RateLimiter(None)
        assert limiter.sent_today() == 0
        limiter.record_email_sent()
        assert limiter.sent_today() == 1

    def test_survives_a_broken_database_handle(self):
        class Broken:
            def query_one(self, *_):
                raise RuntimeError("db gone")

        limiter = RateLimiter(Broken())
        assert limiter.sent_today() == 0        # falls back, does not raise
        assert limiter.can_send_email()[0] is True

    def test_burst_limit_reports_when_the_window_reopens(self, db, monkeypatch):
        """A batch the user asked for waits out the per-minute window instead
        of dropping the rest of the queue, so it needs to know how long."""
        monkeypatch.setenv("MAX_EMAIL_GENERATIONS_PER_MINUTE", "1")
        limiter = RateLimiter(db)
        assert limiter.generation_retry_after() is None    # nothing blocking
        limiter.record_email_generation()
        wait = limiter.generation_retry_after()
        assert wait is not None and 0 < wait <= 61

    def test_daily_cap_is_never_worth_waiting_for(self, db, monkeypatch):
        monkeypatch.setenv("MAX_EMAIL_GENERATIONS_PER_DAY", "1")
        monkeypatch.setenv("MAX_EMAIL_GENERATIONS_PER_MINUTE", "1")
        # Quota is spent per generation *event*, not per surviving email row.
        db.log_event("email", "e1", "generated", "someone")
        limiter = RateLimiter(db)
        limiter.record_email_generation()

        assert limiter.can_generate_email()[0] is False
        assert limiter.generation_retry_after() is None    # waiting cannot help

    def test_generation_quota_counts_rewrites_and_survives_draft_deletion(self, db):
        """Rewrites update a row instead of inserting one, and deleting a draft
        used to refund quota — so a row-based count could be walked past
        indefinitely by rewriting and deleting."""
        contact = db.create_contact(email="a@b.com")
        email = db.create_email(contact_id=contact["id"])
        db.log_event("email", email["id"], "generated", "a@b.com")
        db.log_event("email", email["id"], "regenerated", "a@b.com")
        db.log_event("email", email["id"], "follow_up_generated", "a@b.com")

        limiter = RateLimiter(db)
        assert limiter.generated_today() == 3

        db.delete_email(email["id"])
        assert limiter.generated_today() == 3   # deletion does not refund

    def test_burst_limit_still_applies(self, db, monkeypatch):
        monkeypatch.setenv("MAX_EMAIL_GENERATIONS_PER_MINUTE", "2")
        limiter = RateLimiter(db)
        limiter.record_email_generation()
        limiter.record_email_generation()
        can, err = limiter.can_generate_email()
        assert can is False
        assert "per minute" in err


class TestSentHistoryProtection:
    """Deleting a contact cascades to their emails — the API guards contacts
    with sent history, so the underlying count query must be accurate."""

    def test_counts_only_sent_emails(self, db):
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="sent")
        db.create_email(contact_id=contact["id"], status="draft")

        sent = db.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE contact_id=? AND status='sent'",
            (contact["id"],))["n"]
        assert sent == 1

    def test_contact_with_only_drafts_is_not_protected(self, db):
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="draft")

        sent = db.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE contact_id=? AND status='sent'",
            (contact["id"],))["n"]
        assert sent == 0
