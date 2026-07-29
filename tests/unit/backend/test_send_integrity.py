"""Send-path integrity: no duplicate first-contact emails, follow-ups thread
onto the original conversation, and headers can't be injected."""
import base64
import email as email_lib
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from db import Database
from email_composer import EmailComposer
from email_sender import EmailSender, _sanitize_header
from enrichment import EnrichmentService
from generation import GenerationService
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
