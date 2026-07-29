"""Regression tests for the hardening pass: SSRF, recipient validation,
and reply-check failures never masquerading as 'no reply'."""
import base64
import email as email_lib
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from email_sender import EmailSender
from models import CompanyCreate, ContactCreate, ContactUpdate
from response_checker import ResponseChecker
from web_scraper import is_safe_public_url


class TestSSRFGuard:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/", "http://localhost/", "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/",
        "http://0.0.0.0/",
    ])
    def test_rejects_internal_addresses(self, url):
        assert is_safe_public_url(url) is False

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd", "ftp://example.com/", "gopher://example.com/",
        "javascript:alert(1)", "",
    ])
    def test_rejects_non_http_schemes(self, url):
        assert is_safe_public_url(url) is False

    def test_rejects_nonstandard_ports(self):
        assert is_safe_public_url("http://example.com:8000/") is False
        assert is_safe_public_url("http://example.com:22/") is False

    def test_rejects_malformed_port(self):
        assert is_safe_public_url("http://example.com:notaport/") is False

    def test_rejects_unresolvable_host(self):
        assert is_safe_public_url("http://this-host-does-not-exist.invalid/") is False

    def test_allows_public_https(self):
        assert is_safe_public_url("https://example.com/about") is True


class TestUrlValidation:
    def test_company_rejects_internal_url_at_ingest(self):
        with pytest.raises(ValidationError):
            CompanyCreate(name="Acme", url="http://127.0.0.1:8000/")

    def test_company_rejects_non_http_scheme(self):
        with pytest.raises(ValidationError):
            CompanyCreate(name="Acme", url="file:///etc/passwd")

    def test_company_accepts_normal_url(self):
        assert CompanyCreate(name="Acme", url="https://acme.com").url == "https://acme.com"

    def test_company_url_is_optional(self):
        assert CompanyCreate(name="Acme").url is None


class TestRecipientValidation:
    @pytest.mark.parametrize("bad", [
        "a@b.com, evil@x.com",       # comma injection
        "a@b.com; evil@x.com",       # semicolon injection
        "Real Name <a@b.com>",       # angle brackets
        "a@b.com\nBcc: evil@x.com",  # header injection
        "not-an-email-at-all",
        "@nodomain.com",
        "nolocal@",
    ])
    def test_rejects_unsafe_addresses(self, bad):
        with pytest.raises(ValidationError):
            ContactCreate(name="X", email=bad)

    def test_allows_empty_email(self):
        assert ContactCreate(name="X", email="").email == ""

    def test_allows_plain_address(self):
        assert ContactCreate(email="jane.smith@sub.acme.co.uk").email == "jane.smith@sub.acme.co.uk"

    def test_update_model_validates_too(self):
        with pytest.raises(ValidationError):
            ContactUpdate(email="a@b.com, evil@x.com")


class TestSendTimeRecipientGuard:
    """Even if a bad address reaches the DB, the sender must refuse it."""

    def _sender(self):
        sender = EmailSender(credentials_path="/nonexistent", token_path="/nonexistent")
        sender.service = MagicMock()
        return sender

    @pytest.mark.parametrize("bad", [
        "a@b.com, evil@x.com", "a@b.com\nBcc: evil@x.com", "not-an-email", "",
    ])
    def test_refuses_to_send_without_touching_the_network(self, bad):
        sender = self._sender()
        result = sender.send_email(
            {"id": "e1", "contact_email": bad, "subject": "Hi", "body": "Hello"},
            from_email="me")
        assert result["success"] is False
        sender.service.users.assert_not_called()


class TestOutgoingHtmlIsEscaped:
    """The body carries scraped company text and the user's own signature.
    Interpolating it into the HTML part unescaped meant the delivered email
    differed from the plain text the user reviewed in the app."""

    def _send_and_read_parts(self, body):
        sender = EmailSender(credentials_path="/nonexistent", token_path="/nonexistent")
        sender.service = MagicMock()
        send = sender.service.users.return_value.messages.return_value.send
        send.return_value.execute.return_value = {"id": "m1", "threadId": "t1"}

        result = sender.send_email(
            {"id": "e1", "contact_email": "jane@acme.com", "subject": "Hi",
             "body": body}, from_email="me")
        assert result["success"] is True

        raw = send.call_args.kwargs["body"]["raw"]
        message = email_lib.message_from_bytes(base64.urlsafe_b64decode(raw))
        parts = {p.get_content_type(): p.get_payload(decode=True).decode()
                 for p in message.walk() if not p.is_multipart()}
        return parts["text/plain"], parts["text/html"]

    def test_scraped_markup_never_becomes_a_live_link(self):
        payload = 'Check <a href="https://evil.example.com/login">secure payroll</a> now'
        plain, html_part = self._send_and_read_parts(f"Hi Jane,\n\n{payload}\n\nThanks,")
        assert payload in plain
        assert "<a href" not in html_part
        assert "&lt;a href=" in html_part

    def test_angle_bracketed_address_survives_in_the_html_part(self):
        plain, html_part = self._send_and_read_parts(
            "Hi Jane,\n\nA line of text.\n\nJason Li <li59@seas.upenn.edu>")
        assert "li59@seas.upenn.edu" in plain
        assert "li59@seas.upenn.edu" in html_part

    def test_paragraph_breaks_still_become_markup(self):
        _, html_part = self._send_and_read_parts("Hi Jane,\n\nSecond para\nsame para")
        assert html_part.count("<p style=") == 2
        assert "<br>" in html_part


class TestReplyCheckFailuresAreUnknown:
    def test_api_error_returns_unknown_not_no_reply(self):
        service = MagicMock()
        service.users.return_value.threads.return_value.get.return_value.execute.side_effect = \
            Exception("429 rate limit exceeded")
        has_reply, when = ResponseChecker(service).check_response("msg1", "thread1")
        assert has_reply is None   # not False — caller must not clear a reply
        assert when is None

    def test_genuine_absence_of_replies_returns_false(self):
        service = MagicMock()
        service.users.return_value.threads.return_value.get.return_value.execute.return_value = \
            {"messages": [{"id": "msg1", "internalDate": "1700000000000"}]}
        has_reply, _ = ResponseChecker(service).check_response("msg1", "thread1")
        assert has_reply is False

    def test_missing_message_id_returns_false(self):
        assert ResponseChecker(MagicMock()).check_response("") == (False, None)


class TestOwnMessagesAreNotReplies:
    """Our own follow-ups live in the same Gmail thread. Counting them as
    replies inflated the reply rate to ~90% and hid people who never answered.
    """

    @staticmethod
    def _service(thread_messages, headers_by_id=None):
        service = MagicMock()
        service.users.return_value.threads.return_value.get.return_value.execute.return_value = \
            {"messages": thread_messages}

        def get_message(userId, id, **kwargs):
            result = MagicMock()
            result.execute.return_value = {
                "payload": {"headers": [
                    {"name": k, "value": v}
                    for k, v in (headers_by_id or {}).get(id, {}).items()
                ]}
            }
            return result

        service.users.return_value.messages.return_value.get.side_effect = get_message
        return service

    def test_own_follow_up_in_thread_is_not_a_reply(self):
        service = self._service([
            {"id": "orig", "internalDate": "1700000000000", "labelIds": ["SENT"]},
            {"id": "mine", "internalDate": "1700000900000", "labelIds": ["SENT"]},
        ])
        has_reply, _ = ResponseChecker(service).check_response("orig", "thread1")
        assert has_reply is False

    def test_a_genuine_inbound_reply_still_counts(self):
        service = self._service([
            {"id": "orig", "internalDate": "1700000000000", "labelIds": ["SENT"]},
            {"id": "theirs", "internalDate": "1700000900000", "labelIds": ["INBOX"]},
        ], {"theirs": {"From": "jane@acme.com", "Subject": "Re: hello"}})
        has_reply, when = ResponseChecker(service).check_response("orig", "thread1")
        assert has_reply is True
        assert when is not None

    def test_message_from_our_own_address_is_not_a_reply(self):
        """Belt and braces for threads where labelIds are missing."""
        service = self._service([
            {"id": "orig", "internalDate": "1700000000000"},
            {"id": "mine", "internalDate": "1700000900000"},
        ], {"mine": {"From": "Ada <ada@cam.ac.uk>", "Subject": "Re: hello"}})
        checker = ResponseChecker(service, own_address="ada@cam.ac.uk")
        has_reply, _ = checker.check_response("orig", "thread1")
        assert has_reply is False
