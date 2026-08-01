"""A bounce is evidence, not silence.

The reply checker classified a bounce as "not a reply" and threw the fact
away. "Not a reply" is exactly the condition that makes a contact a follow-up
candidate, so a hard-bounced address was chased on a schedule — and repeated
hard bounces are what turn a Gmail account into a spam-foldered one, so the
cost landed on the deliverable addresses rather than the dead one.
"""
import itertools
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import main
from db import Database, now_iso
from response_checker import AUTO, BOUNCE, OWN, REPLY, ResponseChecker

_seq = itertools.count()


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


def _gmail(messages, headers_by_id):
    """A Gmail stub: one thread holding `messages`, headers per message id."""
    service = MagicMock()
    service.users().threads().get().execute.return_value = {"messages": messages}

    def _get(userId=None, id=None, format=None, metadataHeaders=None):
        result = MagicMock()
        result.execute.return_value = {
            "threadId": "t1",
            "payload": {"headers": [{"name": k, "value": v}
                                    for k, v in headers_by_id.get(id, {}).items()]},
        }
        return result

    service.users().messages().get.side_effect = _get
    return service


def _msg(mid, when="1700000000000", labels=None):
    return {"id": mid, "internalDate": when, "labelIds": labels or []}


class TestClassifyingOneMessage:
    @pytest.mark.parametrize("headers,expected", [
        ({"From": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
          "Subject": "Delivery Status Notification (Failure)"}, BOUNCE),
        ({"From": "postmaster@acme.com", "Subject": "Undeliverable: Hi"}, BOUNCE),
        ({"From": "Jane <jane@acme.com>",
          "Subject": "Address not found"}, BOUNCE),
        ({"From": "Jane <jane@acme.com>",
          "Subject": "Your message couldn't be delivered"}, BOUNCE),
        ({"From": "Jane <jane@acme.com>",
          "Subject": "Automatic reply: Out of office"}, AUTO),
        ({"From": "noreply@acme.com", "Subject": "Ticket #4"}, AUTO),
        ({"From": "Jane <jane@acme.com>", "Subject": "Re: Internship"}, REPLY),
    ])
    def test_separates_a_bounce_from_an_auto_reply(self, headers, expected):
        """Collapsing the two is what discarded the fact that an address is
        dead: an out-of-office means the mailbox is alive and worth a later
        follow-up, a postmaster failure means it never will be."""
        service = _gmail([], {"m1": headers})
        assert ResponseChecker(service)._classify("m1") == expected

    def test_our_own_message_is_neither(self):
        service = _gmail([], {"m1": {"From": "me@example.com", "Subject": "Hi"}})
        checker = ResponseChecker(service, own_address="me@example.com")
        assert checker._classify("m1") == OWN

    def test_an_unreadable_message_is_never_called_a_bounce(self):
        """Guessing BOUNCE on a network blip would mark a live address dead
        and permanently stop mail to a real person."""
        service = MagicMock()
        service.users().messages().get.side_effect = RuntimeError("network")
        assert ResponseChecker(service)._classify("m1") == REPLY


class TestCheckingAThread:
    def test_a_bounce_is_reported_and_is_not_a_reply(self):
        service = _gmail(
            [_msg("orig", labels=["SENT"]), _msg("bounce", "1700000001000")],
            {"bounce": {"From": "mailer-daemon@googlemail.com",
                        "Subject": "Delivery Status Notification (Failure)"}})
        verdict = ResponseChecker(service).check_thread("orig", "t1")
        assert verdict["bounced"] is True
        assert isinstance(verdict["bounced_at"], datetime)
        assert verdict["has_reply"] is False

    def test_a_real_reply_after_a_bounce_still_counts(self):
        """People do answer from a second address once the first one fails."""
        service = _gmail(
            [_msg("orig", labels=["SENT"]), _msg("bounce", "1700000001000"),
             _msg("reply", "1700000002000")],
            {"bounce": {"From": "mailer-daemon@x", "Subject": "Undeliverable"},
             "reply": {"From": "Jane <jane@personal.com>", "Subject": "Re: Hi"}})
        verdict = ResponseChecker(service).check_thread("orig", "t1")
        assert verdict["bounced"] is True
        assert verdict["has_reply"] is True

    def test_a_failed_check_claims_neither(self):
        service = MagicMock()
        service.users().threads().get.side_effect = RuntimeError("rate limit")
        verdict = ResponseChecker(service).check_thread("orig", "t1")
        assert verdict["has_reply"] is None
        assert verdict["bounced"] is False

    def test_the_old_two_value_api_still_works(self):
        service = _gmail(
            [_msg("orig", labels=["SENT"]), _msg("reply", "1700000002000")],
            {"reply": {"From": "Jane <jane@acme.com>", "Subject": "Re: Hi"}})
        has_reply, when = ResponseChecker(service).check_response("orig", "t1")
        assert has_reply is True
        assert isinstance(when, datetime)


class TestABouncedContactIsNotChased:
    def _sent(self, db, days_ago=30, **contact_kwargs):
        company = db.create_company(f"Acme {next(_seq)}")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email=f"jane{next(_seq)}@acme.com",
                                    **contact_kwargs)
        email = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Internship inquiry",
                                body="Hi Jane,\n\nA body long enough here.")
        db.update_email(email["id"], {"status": "sent",
                                      "recipient_email": contact["email"],
                                      "gmail_message_id": f"gm{next(_seq)}"})
        db.execute("UPDATE emails SET sent_at=datetime('now', ?) WHERE id=?",
                   (f"-{days_ago} days", email["id"]))
        return contact, email

    def test_an_unanswered_contact_is_still_due(self, db):
        contact, _e = self._sent(db)
        due = [c["contact_id"] for c in db.get_follow_up_candidates(days=7)]
        assert contact["id"] in due

    def test_a_bounced_contact_is_dropped(self, db):
        contact, _e = self._sent(db)
        db.update_contact(contact["id"], {"bounced_at": now_iso()})
        due = [c["contact_id"] for c in db.get_follow_up_candidates(days=7)]
        assert contact["id"] not in due

    def test_a_bounced_email_is_dropped_even_without_a_contact_flag(self, db):
        contact, email = self._sent(db)
        db.update_email(email["id"], {"bounced_at": now_iso()})
        due = [c["contact_id"] for c in db.get_follow_up_candidates(days=7)]
        assert contact["id"] not in due


class TestTheReplyCheckRecordsTheBounce:
    """check_thread reporting a bounce is useless unless the route writes it
    down: deleting the write left the whole suite green."""

    class _Checker:
        def __init__(self, bounced_at):
            self.bounced_at = bounced_at

        def __call__(self, service, own_address=None):
            return self

        def check_thread(self, gmail_message_id, gmail_thread_id=None):
            return {"has_reply": False, "replied_at": None,
                    "bounced": True, "bounced_at": self.bounced_at}

    class _Gmail:
        service = object()
        send_delay = 0

        def authenticate(self):
            return self.service

    def _env(self, db, monkeypatch, bounced_at):
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(main, "email_sender", self._Gmail())
        monkeypatch.setattr(main, "ResponseChecker", self._Checker(bounced_at))
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane@acme.com")
        email = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Hi", body="A body long enough here.")
        db.update_email(email["id"], {"status": "sent", "sent_at": now_iso(),
                                      "gmail_message_id": "gm-1",
                                      "recipient_email": "jane@acme.com"})
        return contact, email

    def test_it_stamps_the_email_and_the_contact(self, db, monkeypatch):
        when = datetime(2026, 7, 1, 9, 30)
        contact, email = self._env(db, monkeypatch, when)
        data = main.check_replies(recheck=True)
        assert data["bounced"] == 1
        assert db.get_email(email["id"])["bounced_at"].startswith("2026-07-01")
        assert db.get_contact(contact["id"])["bounced_at"] is not None

    def test_the_contact_stops_being_a_follow_up_candidate(self, db, monkeypatch):
        """The whole point: "not a reply" is what made it one."""
        contact, email = self._env(db, monkeypatch, datetime(2026, 7, 1, 9, 30))
        db.execute("UPDATE emails SET sent_at=datetime('now','-30 days') WHERE id=?",
                   (email["id"],))
        assert [c["contact_id"] for c in db.get_follow_up_candidates(days=7)] == [contact["id"]]
        main.check_replies(recheck=True)
        assert db.get_follow_up_candidates(days=7) == []

    def test_it_is_not_double_counted_on_a_second_check(self, db, monkeypatch):
        self._env(db, monkeypatch, datetime(2026, 7, 1, 9, 30))
        assert main.check_replies(recheck=True)["bounced"] == 1
        assert main.check_replies(recheck=True)["bounced"] == 0


class TestTheSendPathRefusesADeadAddress:
    def test_a_bounced_contact_is_not_mailed_again(self, db, monkeypatch):
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: True)
        sender = MagicMock()
        sender.send_delay = 0
        monkeypatch.setattr(main, "email_sender", sender)

        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane@acme.com")
        db.update_contact(contact["id"], {"bounced_at": now_iso()})
        draft = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Hi", body="A body long enough here.",
                                status="draft")
        job = db.create_job("send", payload={})
        main._send_lock.acquire()
        main._send_batch_job(job["id"], [draft["id"]], None, False, "me@example.com")

        sender.send_email.assert_not_called()
        assert db.get_email(draft["id"])["status"] == "draft"
        result = db.get_job(job["id"])
        assert "bounced" in str(result.get("result", "")).lower()


class TestTheSendLoopAsksAboutDeliverability:
    """Testing _domain_accepts_mail alone proves the helper works and says
    nothing about whether _send_batch_job consults it — deleting the call site
    left the whole suite green."""

    def _batch(self, db, monkeypatch, accepts):
        monkeypatch.setattr(main, "db", db)
        sender = MagicMock()
        sender.send_delay = 0
        sender.send_email.return_value = {"success": True, "gmail_message_id": "m",
                                          "gmail_thread_id": "t"}
        monkeypatch.setattr(main, "email_sender", sender)
        monkeypatch.setattr(main, "_domain_accepts_mail",
                            lambda addr, cache: accepts)
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane@nowhere.invalid")
        draft = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Hi", body="A body long enough here.",
                                status="draft")
        job = db.create_job("send", payload={})
        main._send_lock.acquire()
        main._send_batch_job(job["id"], [draft["id"]], None, False, "me@example.com")
        return sender, draft

    def test_an_undeliverable_domain_never_reaches_gmail(self, db, monkeypatch):
        sender, draft = self._batch(db, monkeypatch, accepts=False)
        sender.send_email.assert_not_called()
        assert db.get_email(draft["id"])["status"] == "draft"

    def test_a_deliverable_domain_is_sent(self, db, monkeypatch):
        sender, draft = self._batch(db, monkeypatch, accepts=True)
        sender.send_email.assert_called_once()
        assert db.get_email(draft["id"])["status"] == "sent"


class TestDeliverabilityIsCheckedOptimistically:
    def test_a_domain_with_no_mail_server_is_refused(self):
        cache = {"dead.invalid": False}
        assert main._domain_accepts_mail("x@dead.invalid", cache) is False

    def test_a_domain_that_accepts_mail_passes(self):
        assert main._domain_accepts_mail("x@live.com", {"live.com": True}) is True

    def test_an_unknown_result_proceeds(self):
        """domain_has_mx returns None when it cannot check at all. Treating
        that as undeliverable would block real sends on a DNS blip; Gmail is
        the better judge in that case."""
        assert main._domain_accepts_mail("x@maybe.com", {"maybe.com": None}) is True

    def test_the_lookup_is_cached_per_domain(self, monkeypatch):
        calls = []
        import contact_verify
        monkeypatch.setattr(contact_verify, "domain_has_mx",
                            lambda d, **kw: calls.append(d) or True)
        cache = {}
        for _ in range(5):
            main._domain_accepts_mail("a@acme.com", cache)
        main._domain_accepts_mail("b@acme.com", cache)
        assert calls == ["acme.com"], "one DNS answer per domain per batch"

    def test_a_raising_resolver_does_not_block_the_send(self, monkeypatch):
        import contact_verify
        monkeypatch.setattr(contact_verify, "domain_has_mx",
                            lambda d, **kw: (_ for _ in ()).throw(OSError("no dns")))
        assert main._domain_accepts_mail("a@acme.com", {}) is True

    def test_a_missing_address_is_not_refused_here(self):
        """Recipient validation is email_sender's job and already happens; this
        check must not become a second, weaker gate on empty input."""
        assert main._domain_accepts_mail("", {}) is True
