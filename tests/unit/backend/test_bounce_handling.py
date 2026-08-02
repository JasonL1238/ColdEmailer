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

    def _thread(userId=None, id=None, format=None):
        assert id == "t1", f"asked Gmail for thread {id!r}, not the thread id"
        result = MagicMock()
        result.execute.return_value = {"messages": messages}
        return result

    service.users().threads().get.side_effect = _thread

    def _get(userId=None, id=None, format=None, metadataHeaders=None):
        # Model the real API, not just the happy path. Gmail returns no
        # `payload` for format="minimal", and only the headers actually asked
        # for — so a stub that ignores these arguments lets a change to either
        # pass every test while detecting nothing at all against real Gmail.
        headers = headers_by_id.get(id, {})
        if format == "minimal":
            return _result({"threadId": "t1"})
        if format == "metadata":
            wanted = {h.lower() for h in (metadataHeaders or [])}
            headers = {k: v for k, v in headers.items() if k.lower() in wanted}
        return _result({
            "threadId": "t1",
            "payload": {"headers": [{"name": k, "value": v}
                                    for k, v in headers.items()]},
        })

    def _result(value):
        result = MagicMock()
        result.execute.return_value = value
        return result

    service.users().messages().get.side_effect = _get
    return service


def _msg(mid, when="1700000000000", labels=None):
    return {"id": mid, "internalDate": when, "labelIds": labels or []}


class TestClassifyingOneMessage:
    @pytest.mark.parametrize("headers,expected", [
        # Machine provenance — any one of these is proof on its own.
        ({"From": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
          "Subject": "Delivery Status Notification (Failure)"}, BOUNCE),
        ({"From": "postmaster@acme.com", "Subject": "Undeliverable: Hi"}, BOUNCE),
        # A daemon whose subject says nothing bounce-shaped. The sender signal
        # must be load-bearing by itself.
        ({"From": "MAILER-DAEMON@acme.com", "Subject": "Re: Coffee chat"}, BOUNCE),
        ({"From": "Some Relay <relay@acme.com>",
          "Return-Path": "<MAILER-DAEMON@acme.com>",
          "Subject": "Notification"}, BOUNCE),
        ({"From": "bounce-handler@acme.com", "Subject": "Notice",
          "Content-Type": 'multipart/report; report-type=delivery-status; boundary="x"'},
         BOUNCE),
        ({"From": "relay@acme.com", "Subject": "Notice",
          "X-Failed-Recipients": "jane@acme.com"}, BOUNCE),
        ({"From": "relay@acme.com", "Subject": "Undeliverable: Hi",
          "Auto-Submitted": "auto-replied"}, BOUNCE),
        # Auto-replies: the mailbox is alive.
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

    @pytest.mark.parametrize("sender,subject", [
        ("Priya Raman <priya@nuro.ai>",
         "Re: Delivery failure rates in your last-mile fleet"),
        ("Jen Wu <jen@lab.mit.edu>", "Re: Returned mail study — happy to chat"),
        ("Recruiting <hiring@acme.com>",
         "Re: Address not found on your careers page"),
        ("Alex Chen <alex@doordash.com>",
         "Re: Your note — recipient not found issue we fixed"),
        ("Ravi <ravi@acme.com>",
         "Re: Delivery has failed twice — let's talk process"),
        ("Erin Doyle <erin@postmasterlabs.com>", "Re: Coffee chat"),
    ])
    def test_a_human_using_bounce_words_is_not_a_bounce(self, sender, subject):
        """Subject text is written by whoever is replying, and this app quotes
        the target company's own talking points into its subject lines — so a
        logistics company answering about delivery failures was classified as
        a bounce. That discarded their reply, removed them from follow-ups and
        refused every future send to them, irreversibly.
        """
        service = _gmail([], {"m1": {"From": sender, "Subject": subject}})
        assert ResponseChecker(service)._classify("m1") == REPLY

    def test_our_own_mail_wins_over_a_bounce_shaped_subject(self):
        """The own-address guard used to sit *after* the bounce test, so any
        subject match bypassed it."""
        service = _gmail([], {"m1": {"From": "me@example.com",
                                     "Subject": "Undeliverable: Hi"}})
        checker = ResponseChecker(service, own_address="me@example.com")
        assert checker._classify("m1") == OWN

    def test_our_own_message_is_neither(self):
        service = _gmail([], {"m1": {"From": "me@example.com", "Subject": "Hi"}})
        checker = ResponseChecker(service, own_address="me@example.com")
        assert checker._classify("m1") == OWN

    def test_an_address_that_merely_contains_ours_is_not_ours(self):
        """The guard was `own in sender`, a substring test on the raw header.
        A profile of hr@acme.com therefore read a genuine reply from
        chr@acme.com as our own mail: dropped from the reply rate, and the
        contact left queued for another follow-up on the premise of silence.
        No attacker required — short local parts collide by themselves."""
        service = _gmail([], {"m1": {"From": "Chris <chr@acme.com>",
                                     "Subject": "Re: hello"}})
        checker = ResponseChecker(service, own_address="hr@acme.com")
        assert checker._classify("m1") == REPLY

    def test_a_display_name_quoting_our_address_is_not_ours(self):
        """From is sender-controlled, so a loose match is also spoofable —
        and here it would suppress the reply rather than fake one."""
        service = _gmail([], {"m1": {"From": '"me@example.com" <dana@x.com>',
                                     "Subject": "Re: hello"}})
        checker = ResponseChecker(service, own_address="me@example.com")
        assert checker._classify("m1") == REPLY

    def test_our_own_address_still_matches_with_odd_casing_and_spacing(self):
        service = _gmail([], {"m1": {"From": "Me  <ME@Example.COM>  ",
                                     "Subject": "Hi"}})
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
        assert main._domain_accepts_mail("x@maybe.com", {"maybe.com": None}) is True

    def test_a_resolver_timeout_really_does_produce_unknown(self, monkeypatch):
        """Pre-seeding None into the cache asserts a value the resolver has to
        be able to produce. It could not: every failure path returned False, so
        a flapping VPN made gmail.com "has no mail server" and refused whole
        batches as permanently failed."""
        import dns.resolver

        import contact_verify

        def _timeout(self, *a, **kw):
            raise dns.resolver.LifetimeTimeout(timeout=2.0)

        monkeypatch.setattr(dns.resolver.Resolver, "resolve", _timeout)
        assert contact_verify.domain_has_mx("gmail.com") is None
        assert main._domain_accepts_mail("jane@gmail.com", {}) is True

    def test_a_nonexistent_domain_is_still_a_definite_no(self, monkeypatch):
        import dns.resolver

        import contact_verify

        def _nxdomain(self, *a, **kw):
            raise dns.resolver.NXDOMAIN()

        monkeypatch.setattr(dns.resolver.Resolver, "resolve", _nxdomain)
        assert contact_verify.domain_has_mx("no-such-zztest.invalid") is False
        assert main._domain_accepts_mail("x@no-such-zztest.invalid", {}) is False

    def test_a_null_mx_domain_is_refused(self, monkeypatch):
        """RFC 7505: an MX of "." is an explicit declaration that the domain
        accepts no mail. Treating the record as "has MX" let it through."""
        import contact_verify
        import dns.resolver

        class _Rec:
            exchange = "."

        def _null_mx(self, name, rdtype, *a, **kw):
            if rdtype == "MX":
                return [_Rec()]
            raise dns.resolver.NoAnswer()

        monkeypatch.setattr(dns.resolver.Resolver, "resolve", _null_mx)
        assert contact_verify.domain_has_mx("example.com") is False

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


class TestABounceCanBeUndone:
    """Detection can be wrong, and the flag was write-only: no route, repair or
    UI could clear it, so one false positive retired a contact for good while
    the send error told the user to do the very thing that does not help."""

    def _bounced(self, db, monkeypatch):
        monkeypatch.setattr(main, "db", db)
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane@acme.com")
        db.update_contact(contact["id"], {"bounced_at": now_iso(),
                                          "bounce_detail": "Undeliverable"})
        return contact

    def _put(self, contact_id, **updates):
        from models import ContactUpdate
        import asyncio
        return asyncio.run(
            main.update_contact(contact_id, ContactUpdate(**updates), force=True))

    def test_moving_to_a_new_address_clears_it(self, db, monkeypatch):
        """A bounce belongs to an address, not a person."""
        contact = self._bounced(db, monkeypatch)
        row = self._put(contact["id"], email="jane.doe@acme.com")
        assert row["bounced_at"] is None
        assert row["bounce_detail"] is None

    def test_an_explicit_clear_works_without_moving_the_address(self, db, monkeypatch):
        contact = self._bounced(db, monkeypatch)
        row = self._put(contact["id"], clear_bounce=True)
        assert row["bounced_at"] is None
        assert row["email"] == "jane@acme.com"

    def test_an_unrelated_edit_leaves_it_alone(self, db, monkeypatch):
        contact = self._bounced(db, monkeypatch)
        row = self._put(contact["id"], role="Head of Engineering")
        assert row["bounced_at"] is not None

    def test_after_clearing_the_contact_can_be_mailed_again(self, db, monkeypatch):
        contact = self._bounced(db, monkeypatch)
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: True)
        sender = MagicMock()
        sender.send_delay = 0
        sender.send_email.return_value = {"success": True, "gmail_message_id": "m",
                                          "gmail_thread_id": "t"}
        monkeypatch.setattr(main, "email_sender", sender)
        self._put(contact["id"], email="jane.doe@acme.com")
        draft = db.create_email(contact_id=contact["id"], subject="Hi",
                                body="A body long enough here.", status="draft")
        job = db.create_job("send", payload={})
        main._send_lock.acquire()
        main._send_batch_job(job["id"], [draft["id"]], None, False, "me@example.com")
        sender.send_email.assert_called_once()


class TestTheEmailLevelBounceAlsoBlocksASend:
    def test_a_bounced_email_row_is_refused_even_if_the_contact_is_clean(
            self, db, monkeypatch):
        """The refusal reads `email.bounced_at OR contact.bounced_at`; dropping
        the first half left the whole suite green."""
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: True)
        sender = MagicMock()
        sender.send_delay = 0
        monkeypatch.setattr(main, "email_sender", sender)
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane@acme.com")
        draft = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Hi", body="A body long enough here.",
                                status="draft")
        db.update_email(draft["id"], {"bounced_at": now_iso()})
        assert db.get_contact(contact["id"])["bounced_at"] is None
        job = db.create_job("send", payload={})
        main._send_lock.acquire()
        main._send_batch_job(job["id"], [draft["id"]], None, False, "me@example.com")
        sender.send_email.assert_not_called()


class TestTheGatesRunAfterReconciliation:
    def test_an_in_flight_message_is_reconciled_before_being_refused(
            self, db, monkeypatch):
        """A row handed to Gmail with no answer back may already be in the
        recipient's inbox. Refusing it above the Sent-folder lookup left it a
        draft with no message id — invisible to reply tracking, and refused
        identically on every retry."""
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: False)
        sender = MagicMock()
        sender.send_delay = 0
        sender.find_delivered_message.return_value = {
            "gmail_message_id": "found-1", "gmail_thread_id": "t"}
        monkeypatch.setattr(main, "email_sender", sender)

        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane@acme.com")
        db.update_contact(contact["id"], {"bounced_at": now_iso()})
        draft = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Hi", body="A body long enough here.",
                                status="draft")
        db.update_email(draft["id"], {"send_attempted_at": now_iso()})

        job = db.create_job("send", payload={})
        main._send_lock.acquire()
        main._send_batch_job(job["id"], [draft["id"]], None, False, "me@example.com")

        sender.find_delivered_message.assert_called_once()
        row = db.get_email(draft["id"])
        assert row["status"] == "sent"
        assert row["gmail_message_id"] == "found-1"
        sender.send_email.assert_not_called()


class TestTheUIIsToldAboutTheBounce:
    def test_list_emails_exposes_the_contact_flag(self, db):
        """The chip cannot fire without it, and deleting the projection left
        the suite green."""
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane@acme.com")
        db.update_contact(contact["id"], {"bounced_at": now_iso()})
        db.create_email(contact_id=contact["id"], company_id=company["id"],
                        subject="Hi", body="A body long enough here.")
        assert db.list_emails()[0]["contact_bounced_at"] is not None
