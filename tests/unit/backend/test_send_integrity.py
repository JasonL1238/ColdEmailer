"""Send-path integrity: no duplicate first-contact emails, follow-ups thread
onto the original conversation, and headers can't be injected."""
import base64
import email as email_lib
import os
import tempfile
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from db import Database
from email_composer import EmailComposer
from email_sender import EmailSender, _refused_by_gmail, _sanitize_header
from enrichment import EnrichmentService
from generation import GenerationBusy, GenerationService
from rate_limiter import RateLimiter
from resume_service import ResumeService


def _sender_with_capture():
    sender = EmailSender(credentials_path="/nonexistent", token_path="/nonexistent")
    captured = {}
    service = MagicMock()

    def send(userId, body):
        captured["body"] = body
        result = MagicMock()
        result.execute.return_value = {"id": "sent1", "threadId": "thread1"}
        return result

    service.users.return_value.messages.return_value.send.side_effect = send
    sender.service = service
    return sender, captured


def _parsed(captured):
    return email_lib.message_from_bytes(
        base64.urlsafe_b64decode(captured["body"]["raw"]))


class TestHeaderSanitization:
    @pytest.mark.parametrize("raw,expect_absent", [
        ("Hello\nBcc: evil@x.com", "Bcc:"),
        ("Hello\r\nBcc: evil@x.com", "Bcc:"),
    ])
    def test_newlines_cannot_inject_headers(self, raw, expect_absent):
        assert "\n" not in _sanitize_header(raw)
        assert "\r" not in _sanitize_header(raw)

    def test_ordinary_subject_is_untouched(self):
        assert _sanitize_header("Internship inquiry at Acme") == "Internship inquiry at Acme"

    def test_handles_none(self):
        assert _sanitize_header(None) == ""

    def test_injected_subject_does_not_create_a_bcc_header(self):
        sender, captured = _sender_with_capture()
        sender.send_email({"id": "e1", "contact_email": "ok@example.com",
                           "subject": "Hi\nBcc: evil@example.com",
                           "body": "A body long enough to be realistic."}, "me")
        msg = _parsed(captured)
        assert msg.get("Bcc") is None
        assert "\n" not in (msg.get("Subject") or "")


class TestFollowUpThreading:
    def test_follow_up_carries_threading_headers_and_thread_id(self):
        sender, captured = _sender_with_capture()
        sender.send_email({
            "id": "e2", "contact_email": "ok@example.com",
            "subject": "Re: earlier note", "body": "Following up on my earlier note.",
            "reply_to_message_id": "origmsg", "reply_to_thread_id": "origthread",
        }, "me")
        msg = _parsed(captured)
        assert msg.get("In-Reply-To") == "<origmsg>"
        assert msg.get("References") == "<origmsg>"
        assert captured["body"].get("threadId") == "origthread"

    def test_in_reply_to_uses_the_rfc_message_id_not_the_api_id(self):
        """Gmail's API id ("18c2f...") is not the RFC 5322 Message-ID header.
        Putting the API id in In-Reply-To matches nothing, so the header value
        has to come from get_thread_context, already bracketed."""
        sender, captured = _sender_with_capture()
        sender.send_email({
            "id": "e", "contact_email": "ok@example.com", "subject": "Re: hi",
            "body": "Following up on my earlier note to you.",
            "reply_to_message_id": "<CAJ123abc@mail.gmail.com>",
            "reply_to_thread_id": "thread99",
        }, "me")
        msg = _parsed(captured)
        assert msg["In-Reply-To"] == "<CAJ123abc@mail.gmail.com>"
        assert msg["References"] == "<CAJ123abc@mail.gmail.com>"

    def test_bare_message_id_gets_bracketed(self):
        sender, captured = _sender_with_capture()
        sender.send_email({
            "id": "e", "contact_email": "ok@example.com", "subject": "Re: hi",
            "body": "Following up on my earlier note to you.",
            "reply_to_message_id": "CAJ123abc@mail.gmail.com",
        }, "me")
        assert _parsed(captured)["In-Reply-To"] == "<CAJ123abc@mail.gmail.com>"

    def test_get_thread_context_reads_the_message_id_header(self):
        sender = EmailSender(credentials_path="/x", token_path="/x")
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
            "threadId": "thread42",
            "payload": {"headers": [{"name": "Message-ID", "value": "<abc@mail.gmail.com>"}]},
        }
        sender.service = service
        ctx = sender.get_thread_context("apiid123")
        assert ctx == {"message_id": "<abc@mail.gmail.com>", "thread_id": "thread42"}

    def test_get_thread_context_degrades_quietly(self):
        """A failed lookup must not block the send — it just loses threading."""
        sender = EmailSender(credentials_path="/x", token_path="/x")
        service = MagicMock()
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = \
            Exception("404 not found")
        sender.service = service
        assert sender.get_thread_context("gone") == {"message_id": None, "thread_id": None}

    def test_get_thread_context_without_a_service_is_safe(self):
        sender = EmailSender(credentials_path="/x", token_path="/x")
        assert sender.get_thread_context("anything") == {"message_id": None, "thread_id": None}

    def test_first_contact_email_has_no_threading_fields(self):
        sender, captured = _sender_with_capture()
        sender.send_email({"id": "e3", "contact_email": "ok@example.com",
                           "subject": "Hello", "body": "First time reaching out here."}, "me")
        msg = _parsed(captured)
        assert msg.get("In-Reply-To") is None
        assert "threadId" not in captured["body"]


class TestMimeCorrectness:
    """Real relays reject or rewrap malformed messages, which silently costs
    deliverability on mail the user thinks went out clean."""

    def test_no_line_exceeds_the_rfc_5322_limit(self):
        sender, captured = _sender_with_capture()
        body = "Hi Jane,\n\n" + ("A very long unbroken paragraph. " * 60) + "\n\nThanks,"
        sender.send_email({"id": "e", "contact_email": "ok@example.com",
                           "subject": "Hello", "body": body}, "me")
        raw = base64.urlsafe_b64decode(captured["body"]["raw"])
        assert all(len(line.rstrip(b"\r")) <= 998 for line in raw.split(b"\n"))

    def test_non_ascii_subject_survives_a_roundtrip(self):
        sender, captured = _sender_with_capture()
        subject = "Café résumé — naïve"
        sender.send_email({"id": "e", "contact_email": "ok@example.com",
                           "subject": subject,
                           "body": "A body of reasonable length goes here."}, "me")
        msg = _parsed(captured)
        decoded = str(email_lib.header.make_header(
            email_lib.header.decode_header(msg["Subject"])))
        assert decoded == subject

    def test_non_ascii_body_survives_a_roundtrip(self):
        sender, captured = _sender_with_capture()
        body = "Hi Jürgen,\n\nI admire your work on naïve Bayes at Café Inc.\n\nThanks,"
        sender.send_email({"id": "e", "contact_email": "ok@example.com",
                           "subject": "Hello", "body": body}, "me")
        for part in _parsed(captured).walk():
            if part.get_content_type() == "text/plain":
                text = part.get_payload(decode=True).decode("utf-8")
                assert "Jürgen" in text and "naïve" in text
                return
        pytest.fail("no text/plain part found")

    def test_attachment_filename_is_properly_encoded(self, tmp_path):
        pdf = tmp_path / "ZZTEST résumé v2.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        sender, captured = _sender_with_capture()
        sender.send_email({"id": "e", "contact_email": "ok@example.com",
                           "subject": "Hello", "body": "Body of reasonable length."},
                          "me", str(pdf))
        for part in _parsed(captured).walk():
            if part.get_content_disposition() == "attachment":
                assert part.get_filename() == "ZZTEST résumé v2.pdf"
                # No hand-built unquoted 'filename= x.pdf' with a leading space
                assert "filename= " not in part.get("Content-Disposition")
                return
        pytest.fail("no attachment found")


class _Resp:
    def __init__(self, status):
        self.status = status


def _sender_that_raises(exc):
    sender = EmailSender(credentials_path="/nonexistent", token_path="/nonexistent")
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.side_effect = exc
    sender.service = service
    return sender


class TestIndeterminateSendResult:
    """Losing the response to an accepted POST is ordinary — a batch sleeps 3s
    per message and runs for minutes. "We don't know" must not be reported as
    "it didn't go out", because only the second one is a safe retry."""

    def _send(self, sender):
        return sender.send_email({"id": "e1", "contact_email": "ok@example.com",
                                  "subject": "Hello",
                                  "body": "A body long enough to be realistic."}, "me")

    def test_a_lost_response_is_flagged_as_unknown(self):
        result = self._send(_sender_that_raises(TimeoutError("timed out reading response")))
        assert result["success"] is False
        assert result["delivery_unknown"] is True

    def test_a_server_error_is_flagged_as_unknown(self):
        exc = Exception("backend error")
        exc.resp = _Resp(500)
        assert self._send(_sender_that_raises(exc))["delivery_unknown"] is True

    @pytest.mark.parametrize("status", [408, 429])
    def test_come_back_later_does_not_rule_out_delivery(self, status):
        exc = Exception("slow down")
        exc.resp = _Resp(status)
        assert self._send(_sender_that_raises(exc))["delivery_unknown"] is True

    @pytest.mark.parametrize("status", [400, 403, 404])
    def test_a_gmail_refusal_is_a_verdict_and_stays_retryable(self, status):
        exc = Exception("refused")
        exc.resp = _Resp(status)
        assert self._send(_sender_that_raises(exc))["delivery_unknown"] is False

    def test_a_bad_recipient_never_reaches_gmail_at_all(self):
        sender, _ = _sender_with_capture()
        result = sender.send_email({"id": "e", "contact_email": "not an address",
                                    "subject": "Hi", "body": "Hello there."}, "me")
        assert result["success"] is False
        assert not result.get("delivery_unknown")

    def test_helper_ignores_a_missing_status(self):
        assert _refused_by_gmail(Exception("no status here")) is False

    @staticmethod
    def _sender_with_sent_folder(messages, metadata):
        """messages = what list() returns; metadata = {id: (subject, epoch_ms)}."""
        sender = EmailSender(credentials_path="/x", token_path="/x")
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": messages}

        def _get(userId, id, format, metadataHeaders):
            subject, internal = metadata[id]
            result = MagicMock()
            result.execute.return_value = {
                "internalDate": str(internal),
                "payload": {"headers": [{"name": "Subject", "value": subject}]},
            }
            return result

        service.users.return_value.messages.return_value.get.side_effect = _get
        sender.service = service
        return sender, service

    def test_find_delivered_message_reports_a_message_in_the_sent_folder(self):
        sender, _ = self._sender_with_sent_folder(
            [{"id": "m1", "threadId": "t1"}], {"m1": ("Hello", 1_700_000_000_000)})
        assert sender.find_delivered_message("ok@example.com", "Hello") == {
            "gmail_message_id": "m1", "gmail_thread_id": "t1"}

    def test_the_lookup_is_bounded_by_the_attempt_and_checks_the_real_send_time(self):
        """Template subjects are deterministic, so an *older* copy of the same
        subject to the same person matches the same search. Accepting it marked
        a row delivered (with today's timestamp and the old message's ids) for an
        email that never went out."""
        attempt = "2026-07-29T16:40:00"
        attempt_epoch = int(datetime.fromisoformat(attempt).timestamp())
        older_ms = (attempt_epoch - 3600) * 1000
        sender, service = self._sender_with_sent_folder(
            [{"id": "old", "threadId": "told"}],
            {"old": ("Internship inquiry at Acme", older_ms)})

        assert sender.find_delivered_message(
            "ok@example.com", "Internship inquiry at Acme", sent_after=attempt) is None
        # ...and the search itself was bounded, rather than relying on the recheck
        q = service.users.return_value.messages.return_value.list.call_args.kwargs["q"]
        assert f"after:{attempt_epoch - 60}" in q

    def test_a_phrase_match_on_a_longer_subject_is_not_accepted(self):
        """Gmail's subject: operator is a phrase match, not an equality test."""
        sender, _ = self._sender_with_sent_folder(
            [{"id": "m1", "threadId": "t1"}],
            {"m1": ("Internship inquiry at Acme Robotics", 1_700_000_000_000)})
        assert sender.find_delivered_message(
            "ok@example.com", "Internship inquiry at Acme") is None

    def test_the_first_identifiable_candidate_wins(self):
        """maxResults=5, so a stale first hit must not hide the real one."""
        attempt = "2026-07-29T16:40:00"
        attempt_epoch = int(datetime.fromisoformat(attempt).timestamp())
        sender, _ = self._sender_with_sent_folder(
            [{"id": "old", "threadId": "told"}, {"id": "new", "threadId": "tnew"}],
            {"old": ("Hello", (attempt_epoch - 7200) * 1000),
             "new": ("Hello", (attempt_epoch + 5) * 1000)})
        assert sender.find_delivered_message(
            "ok@example.com", "Hello", sent_after=attempt) == {
                "gmail_message_id": "new", "gmail_thread_id": "tnew"}

    def test_an_unidentifiable_candidate_is_not_treated_as_proof(self):
        sender = EmailSender(credentials_path="/x", token_path="/x")
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "m1", "threadId": "t1"}]}
        service.users.return_value.messages.return_value.get.return_value.execute.side_effect = \
            Exception("network down")
        sender.service = service
        assert sender.find_delivered_message("ok@example.com", "Hello") is None

    def test_find_delivered_message_returns_none_when_nothing_landed(self):
        sender = EmailSender(credentials_path="/x", token_path="/x")
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {}
        sender.service = service
        assert sender.find_delivered_message("ok@example.com", "Hello") is None

    def test_find_delivered_message_degrades_quietly(self):
        """A failed lookup means we still don't know — never "it didn't land"."""
        sender = EmailSender(credentials_path="/x", token_path="/x")
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = \
            Exception("network down")
        sender.service = service
        assert sender.find_delivered_message("ok@example.com", "Hello") is None
        assert EmailSender("/x", "/x").find_delivered_message("ok@example.com", "Hi") is None


class TestRecipientDeduplication:
    @pytest.fixture
    def service(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "test.db"))
            db.update_profile({"full_name": "Ada", "school": "Cambridge"})
            resumes = ResumeService(db)
            gen = GenerationService(db, EmailComposer(db, resumes),
                                   EnrichmentService(), RateLimiter(db))
            yield db, gen

    def test_contact_with_a_sent_email_is_already_contacted(self, service):
        db, gen = service
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="sent")
        assert gen._already_contacted(contact["id"]) is True

    def test_contact_with_a_pending_draft_is_already_contacted(self, service):
        """Two drafts for one person would send twice on the next batch."""
        db, gen = service
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="draft")
        assert gen._already_contacted(contact["id"]) is True

    def test_a_fresh_contact_is_not_already_contacted(self, service):
        db, gen = service
        contact = db.create_contact(email="a@b.com")
        assert gen._already_contacted(contact["id"]) is False

    def test_a_trashed_draft_does_not_block_a_retry(self, service):
        db, gen = service
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="trashed")
        assert gen._already_contacted(contact["id"]) is False

    def test_archived_contacts_are_skipped_by_generation(self, service):
        """Archive means "stop reaching out" — the delete guard tells users to
        archive, so archiving has to actually prevent new drafts."""
        db, gen = service
        contact = db.create_contact(email="a@b.com", name="ZZTEST Archived")
        db.update_contact(contact["id"], {"status": "archived"})
        job = db.create_job("generation", {"contact_ids": [contact["id"]]})
        gen._run(job["id"], {"contact_ids": [contact["id"]],
                             "email_type": "application", "use_template_only": True})

        result = db.get_job(job["id"])["result"]
        assert '"generated": 0' in result
        assert "archived" in result
        assert db.list_emails() == []

    def test_a_follow_up_does_not_count_as_first_contact(self, service):
        """Follow-ups are the sanctioned way to reach back out; they must not
        themselves make the contact look first-contacted."""
        db, gen = service
        contact = db.create_contact(email="a@b.com")
        db.create_email(contact_id=contact["id"], status="sent", is_follow_up=True)
        assert gen._already_contacted(contact["id"]) is False


class TestCancelledGenerationRace:
    """"Cancel generation" then retry used to leave two identical first-contact
    drafts for one person, both armed by "Select all"."""

    @pytest.fixture
    def service(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "test.db"))
            db.update_profile({"full_name": "Ada", "school": "Cambridge"})
            gen = GenerationService(db, EmailComposer(db, ResumeService(db)),
                                    EnrichmentService(), RateLimiter(db))
            yield db, gen

    def test_a_job_cancelled_during_compose_inserts_nothing_more(self, service):
        db, gen = service
        contact = db.create_contact(name="ZZTEST Race", email="zzrace@example.invalid")
        payload = {"contact_ids": [contact["id"]], "email_type": "application",
                   "use_template_only": True}
        job = db.create_job("generation", payload)

        # Stand in for the seconds a real compose() takes: the Cancel click
        # lands while the worker is inside it.
        real_compose = gen.composer.compose

        def compose_then_cancel(*args, **kwargs):
            composed = real_compose(*args, **kwargs)
            db.update_job(job["id"], status="cancelled")
            return composed

        gen.composer.compose = compose_then_cancel
        gen._run(job["id"], payload)

        assert db.list_emails() == []
        # and the abandoned draft did not permanently spend generation quota
        assert RateLimiter(db).generated_today() == 0

    def test_a_draft_that_appeared_during_compose_is_not_duplicated(self, service):
        """The de-dup guard runs before compose(); a concurrent run can insert
        in between, so it has to run again next to the insert."""
        db, gen = service
        contact = db.create_contact(name="ZZTEST Twin", email="zztwin@example.invalid")
        payload = {"contact_ids": [contact["id"]], "email_type": "application",
                   "use_template_only": True}
        job = db.create_job("generation", payload)

        real_compose = gen.composer.compose

        def compose_then_other_job_inserts(*args, **kwargs):
            composed = real_compose(*args, **kwargs)
            db.create_email(contact_id=contact["id"], status="draft",
                            subject="Internship inquiry", body="from the other run")
            return composed

        gen.composer.compose = compose_then_other_job_inserts
        gen._run(job["id"], payload)

        assert len(db.list_emails()) == 1
        result = db.get_job(job["id"])["result"]
        assert "already emailed" in result

    def test_a_cancelled_but_still_running_worker_blocks_the_next_run(self, service):
        """cancel() flips the job row immediately while the thread is still
        composing, so the route's "any job still running?" check sees nothing."""
        db, gen = service
        contact = db.create_contact(name="ZZTEST Busy", email="zzbusy@example.invalid")

        released = threading.Event()
        gen.composer.compose = lambda *a, **k: (released.wait(2) and None) or {
            "subject": "Internship inquiry", "body": "hello",
            "used_template_fallback": True, "fallback_reason": "user_requested"}

        gen.start([contact["id"]], use_template_only=True)
        job = db.list_jobs("generation", limit=1)[0]
        assert gen.cancel(job["id"]) is True
        # The row now says 'cancelled' — but the worker is alive, so a second
        # run must not be allowed to start alongside it.
        assert [j for j in db.list_jobs("generation", limit=5)
                if j["status"] == "running"] == []
        assert gen.is_busy() is True
        with pytest.raises(GenerationBusy):
            gen.start([contact["id"]], use_template_only=True)

        released.set()
        for _ in range(40):
            if not gen.is_busy():
                break
            time.sleep(0.05)
        assert gen.is_busy() is False
        # and the run that was allowed to start again drafts exactly once
        gen.composer.compose = EmailComposer(db, ResumeService(db)).compose
        gen.start([contact["id"]], use_template_only=True)
        for _ in range(40):
            if not gen.is_busy():
                break
            time.sleep(0.05)
        assert len(db.list_emails()) == 1
