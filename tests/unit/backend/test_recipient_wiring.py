"""The guards must be wired in, not merely defined.

An adversarial pass showed three of these protections could be deleted outright
with the whole suite still green: the send path's recipient stamp, the guard on
the enrich-time address upgrade, and the drafts-screen warning. Testing the
helper in isolation proves the helper works; it says nothing about whether the
caller asks it. These tests drive the real `_send_batch_job` and the real
`_enrich_company_async`, with only the network stubbed.
"""
import itertools

import pytest

import main

_seq = itertools.count()


class _FakeSender:
    send_delay = 0

    def __init__(self, result=None, sent_folder=None):
        self.calls = []
        self.lookups = []
        self.result = result
        self.sent_folder = sent_folder

    def send_email(self, email, from_email, resume_path=None):
        self.calls.append(email.get("contact_email"))
        if self.result is not None:
            return {**self.result, "email_id": email["id"]}
        return {"success": True, "email_id": email["id"],
                "gmail_message_id": f"m{next(_seq)}", "gmail_thread_id": "t"}

    def find_delivered_message(self, to_email, subject, sent_after=None):
        self.lookups.append(to_email)
        return self.sent_folder

    def get_thread_context(self, gmail_message_id):
        return {"message_id": f"<{gmail_message_id}@mail>", "thread_id": "t"}


@pytest.fixture
def send_env(monkeypatch):
    sender = _FakeSender()
    monkeypatch.setattr(main, "email_sender", sender)
    # No DNS in unit tests. Fixture addresses use the reserved .invalid TLD,
    # which correctly has no mail server, so the real deliverability check
    # would refuse every one of them.
    monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: True)
    main._send_lock.acquire()
    yield sender
    if main._send_lock.locked():
        main._send_lock.release()


def _contact_with_draft(name="ZZ Jane"):
    """Unique per call. The suite shares one database, and `contacts.email` is
    uniquely indexed, so a fixed address silently hands back the contact an
    earlier test already sent to."""
    n = next(_seq)
    company = main.db.create_company(f"ZZ Wire {n}")
    address = f"careers{n}@zzwire.invalid"
    contact = main.db.create_contact(company_id=company["id"], name=name,
                                     email=address)
    main.db.update_contact(contact["id"], {"email_kind": "generic"})
    draft = main.db.create_email(contact_id=contact["id"],
                                 company_id=company["id"], subject="Hi",
                                 body="A body long enough to be realistic.",
                                 status="draft")
    return company, contact, draft


class TestTheSendPathActuallyStampsTheRecipient:
    def test_a_successful_send_freezes_the_address(self, send_env):
        company, contact, draft = _contact_with_draft()
        job = main.db.create_job("send", payload={})
        main._send_batch_job(job["id"], [draft["id"]], None, False,
                             "me@example.com")
        assert main.db.get_email(draft["id"])["recipient_email"] == contact["email"]

    def test_and_that_stamp_survives_the_contact_moving(self, send_env):
        """The whole point. Without the stamp the recipient is re-resolved from
        the contact and the record of who was written to silently changes."""
        company, contact, draft = _contact_with_draft()
        job = main.db.create_job("send", payload={})
        main._send_batch_job(job["id"], [draft["id"]], None, False,
                             "me@example.com")
        main.db.execute("UPDATE contacts SET email=? WHERE id=?",
                        ("someone.else@zzwire.invalid", contact["id"]))
        assert main.db.get_email(draft["id"])["contact_email"] == contact["email"]

    def test_a_failed_send_stamps_nothing(self, monkeypatch):
        sender = _FakeSender(result={"success": False, "error": "nope"})
        monkeypatch.setattr(main, "email_sender", sender)
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: True)
        main._send_lock.acquire()
        try:
            _co, _ct, draft = _contact_with_draft()
            job = main.db.create_job("send", payload={})
            main._send_batch_job(job["id"], [draft["id"]], None, False,
                                 "me@example.com")
        finally:
            if main._send_lock.locked():
                main._send_lock.release()
        assert main.db.get_email(draft["id"])["recipient_email"] is None


class TestTheEnrichUpgradeAsksTheGuard:
    """`_enrich_company_async` upgrades a role inbox to a person mailbox.

    Each test carries its own control: an identical contact that has NOT been
    emailed goes through the same enrich run. Asserting only that the guarded
    contact stayed put passes just as well when the upgrade never fired at all
    — which is exactly how deleting the guard left this suite green. The
    control proves the run really did upgrade, so the difference between the
    two contacts can only be the guard.
    """

    def _run_enrich(self, monkeypatch, company_id, candidates):
        monkeypatch.setattr(main.rate_limiter, "can_research_company",
                            lambda: (True, ""))
        # A unique domain per run. `companies.domain` is uniquely indexed, so a
        # fixed one makes the second _enrich_company_async of the session die
        # on the constraint before it ever reaches the contact loop — and every
        # assertion about that loop then passes without the loop running.
        domain = f"zzwire{next(_seq)}.invalid"
        monkeypatch.setattr(
            main.enrichment, "enrich",
            lambda name, url=None, **kw: {
                "url": f"https://{domain}", "domain": domain,
                "ok": True, "identity_verified": True, "summary": "Widgets.",
                "emails": [], "contacts": candidates,
                "research_sources": [], "pages_scraped": 1,
                "pages_attempted": 1, "research_quality": "high",
            })
        # _enrich_company_async imports this inside the function body, so it
        # has to be patched on the source module, not on main.
        import enrichment
        monkeypatch.setattr(
            enrichment, "select_outreach_contacts",
            lambda contacts, emails, domain, **kw: candidates)
        main._enrich_company_async(company_id, "full")

    def _pair(self):
        """Two identical role-inbox contacts in one company, each with a
        pending upgrade. Addresses and LinkedIn slugs are unique because both
        columns are uniquely indexed across the shared test database."""
        n = next(_seq)
        company = main.db.create_company(f"ZZ Pair {n}")
        made = []
        for tag in ("ctl", "grd"):
            link = f"https://www.linkedin.com/in/zz-{tag}-{n}"
            contact = main.db.create_contact(
                company_id=company["id"], name=f"ZZ {tag} {n}",
                email=f"careers-{tag}{n}@zzwire.invalid", linkedin_url=link)
            main.db.update_contact(contact["id"], {"email_kind": "generic"})
            draft = main.db.create_email(
                contact_id=contact["id"], company_id=company["id"],
                subject="Hi", body="A body long enough to be realistic.",
                status="draft")
            upgrade = f"zz.{tag}{n}@zzwire.invalid"
            made.append({
                "contact": contact, "draft": draft, "upgrade": upgrade,
                "candidate": {
                    "name": f"ZZ {tag} {n}", "email": upgrade,
                    "linkedin_url": link, "linkedin_verified": True,
                    "email_kind": "personal", "email_verified": True,
                    "email_person_match": True, "person_verified": True,
                    "seniority_rank": 5},
            })
        return company, made[0], made[1]

    def _email_of(self, entry):
        return main.db.get_contact(entry["contact"]["id"])["email"]

    def test_a_contact_with_sent_mail_keeps_its_address(self, monkeypatch, send_env):
        company, control, guarded = self._pair()
        job = main.db.create_job("send", payload={})
        main._send_batch_job(job["id"], [guarded["draft"]["id"]], None, False,
                             "me@example.com")
        assert main.db.get_email(guarded["draft"]["id"])["status"] == "sent"
        assert main._contact_has_been_emailed(guarded["contact"]["id"]) is True
        assert main._contact_has_been_emailed(control["contact"]["id"]) is False

        self._run_enrich(monkeypatch, company["id"],
                         [control["candidate"], guarded["candidate"]])

        assert self._email_of(control) == control["upgrade"], \
            "the control must move, or this run upgraded nothing"
        assert self._email_of(guarded) == guarded["contact"]["email"]

    def test_an_in_flight_send_also_locks_the_address(self, monkeypatch):
        """send_attempted_at is set before the Gmail call and cleared only on a
        definite verdict. A row in that window may already be in the
        recipient's inbox, so moving the address then broke the retry's
        Sent-folder lookup and could deliver a confirmed resend to the wrong
        person."""
        company, control, guarded = self._pair()
        main.db.update_email(guarded["draft"]["id"],
                             {"send_attempted_at": main.now_iso()})
        assert main._contact_has_been_emailed(guarded["contact"]["id"]) is True
        assert main._contact_has_been_emailed(control["contact"]["id"]) is False

        self._run_enrich(monkeypatch, company["id"],
                         [control["candidate"], guarded["candidate"]])

        assert self._email_of(control) == control["upgrade"]
        assert self._email_of(guarded) == guarded["contact"]["email"]

    def test_a_blank_address_is_not_refilled_after_a_send(self, monkeypatch, send_env):
        """Clearing the address then re-researching used to put a different
        person's mailbox onto a contact that had already been written to."""
        company, control, guarded = self._pair()
        job = main.db.create_job("send", payload={})
        main._send_batch_job(job["id"], [guarded["draft"]["id"]], None, False,
                             "me@example.com")
        main.db.update_contact(guarded["contact"]["id"], {"email": ""})
        main.db.update_contact(control["contact"]["id"], {"email": ""})
        assert main._contact_has_been_emailed(guarded["contact"]["id"]) is True
        assert main._contact_has_been_emailed(control["contact"]["id"]) is False

        self._run_enrich(monkeypatch, company["id"],
                         [control["candidate"], guarded["candidate"]])

        assert self._email_of(control) == control["upgrade"], \
            "filling a blank on an un-emailed contact must still work"
        assert self._email_of(guarded) == ""
