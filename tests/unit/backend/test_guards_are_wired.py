"""Guards that a mutation survey showed could be deleted with a green suite.

Round three of adversarial review found eleven production changes that could be
reverted without failing a single test — including the 409 that a whole commit
was named after. Each test here was written against a specific mutation and
verified to fail when that change is reverted.
"""
import asyncio
import os
import tempfile

import pytest

import main
from db import Database, _looks_like_role_local, now_iso
from models import ContactUpdate


@pytest.fixture
def db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Database(os.path.join(tmp, "test.db"))
        monkeypatch.setattr(main, "db", fresh)
        yield fresh


def _emailed_contact(db, address="careers@acme.com"):
    company = db.create_company("Acme")
    contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                email=address)
    email = db.create_email(contact_id=contact["id"], company_id=company["id"],
                            subject="Internship inquiry at Acme",
                            body="Hi Jane,\n\nA body long enough here.")
    db.update_email(email["id"], {"status": "sent", "sent_at": now_iso(),
                                  "gmail_message_id": "gm-1",
                                  "recipient_email": address})
    return company, contact, email


def _put(contact_id, force=False, **updates):
    return asyncio.run(
        main.update_contact(contact_id, ContactUpdate(**updates), force=force))


class TestMovingASentAddressByHandIsRefused:
    """The headline guard of its commit, and nothing tested it."""

    def test_it_refuses(self, db):
        _co, contact, _e = _emailed_contact(db)
        with pytest.raises(main.HTTPException) as caught:
            _put(contact["id"], email="someone.else@acme.com")
        assert caught.value.status_code == 409
        assert db.get_contact(contact["id"])["email"] == "careers@acme.com"

    def test_the_message_says_what_to_do(self, db):
        _co, contact, _e = _emailed_contact(db)
        with pytest.raises(main.HTTPException) as caught:
            _put(contact["id"], email="someone.else@acme.com")
        assert "force=true" in str(caught.value.detail)

    def test_force_overrides_it(self, db):
        _co, contact, _e = _emailed_contact(db)
        row = _put(contact["id"], force=True, email="someone.else@acme.com")
        assert row["email"] == "someone.else@acme.com"

    def test_force_still_reclassifies_and_clears_verification(self, db):
        _co, contact, _e = _emailed_contact(db)
        db.update_contact(contact["id"], {"email_kind": "generic",
                                          "email_verified": 1,
                                          "person_verified": 1})
        row = _put(contact["id"], force=True, email="jane.doe@acme.com")
        assert row["email_kind"] == "personal"
        assert row["email_verified"] == 0
        assert row["person_verified"] == 0

    def test_an_un_emailed_contact_is_not_blocked(self, db):
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="careers@acme.com")
        row = _put(contact["id"], email="jane.doe@acme.com")
        assert row["email"] == "jane.doe@acme.com"

    def test_the_sent_email_still_names_the_original_recipient(self, db):
        _co, contact, email = _emailed_contact(db)
        _put(contact["id"], force=True, email="someone.else@acme.com")
        assert db.get_email(email["id"])["contact_email"] == "careers@acme.com"


class TestAnEchoedAddressIsDropped:
    def test_an_unchanged_address_is_not_rewritten(self, db):
        """`updates.pop("email")` — without it, an unchanged value is still
        written back and re-derives a classification from nothing new."""
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane.doe@acme.com")
        db.update_contact(contact["id"], {"email_kind": "personal",
                                          "email_verified": 1})
        before = db.get_contact(contact["id"])["updated_at"]
        row = _put(contact["id"], email="jane.doe@acme.com")
        assert row["email_kind"] == "personal"
        assert row["email_verified"] == 1
        assert row["updated_at"] == before

    def test_an_emailed_contact_can_still_be_archived(self, db):
        """The UI sends {status} alone, but a client echoing the whole contact
        must not trip the 409 on an address it did not change."""
        _co, contact, _e = _emailed_contact(db)
        row = _put(contact["id"], email="careers@acme.com", status="archived")
        assert row["status"] == "archived"

    def test_case_only_differences_are_not_a_change(self, db):
        _co, contact, _e = _emailed_contact(db)
        row = _put(contact["id"], email="CAREERS@ACME.COM")
        assert row["email"] == "careers@acme.com"


class TestRenamingClearsThePairingFlags:
    def test_a_rename_drops_verification(self, db):
        """Both flags assert "this mailbox was confirmed to be this person".
        Leaving them after a rename produced a row that reads "address does not
        match this person" and "verified as this person" at the same time, and
        the UI renders the green chip off the second one."""
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane.doe@acme.com")
        db.update_contact(contact["id"], {"email_kind": "personal",
                                          "email_verified": 1,
                                          "person_verified": 1})
        row = _put(contact["id"], name="Bob Smith")
        assert row["email_kind"] == "named_unmatched"
        assert row["email_verified"] == 0
        assert row["person_verified"] == 0


class TestEveryRecipientLookupIsFrozen:
    """Two queries missed the COALESCE and no test noticed."""

    def test_the_company_detail_route_agrees_with_the_email_detail(self, db):
        """Drives the real route. Inlining the query here would test a copy of
        the SQL rather than the one the app runs."""
        company, contact, email = _emailed_contact(db)
        db.execute("UPDATE contacts SET email=? WHERE id=?",
                   ("moved@acme.com", contact["id"]))
        detail = asyncio.run(main.get_company(company["id"]))
        assert detail["emails"][0]["contact_email"] == "careers@acme.com"
        assert (detail["emails"][0]["contact_email"]
                == db.get_email(email["id"])["contact_email"])

    def test_no_recipient_projection_is_left_unfrozen(self):
        """A source check, because the reply-check query needs Gmail to run.

        Two projections were missed the first time and nothing noticed; a new
        one would be just as silent. Every `AS contact_email` in the backend
        must resolve through recipient_email first.
        """
        import pathlib as _p
        import re as _re
        backend = _p.Path(main.__file__).parent
        # `AS contact_email` exactly — not contact_email_kind, which is a
        # classification and legitimately follows the live contact.
        alias = _re.compile(r"AS\s+contact_email\b(?!_)", _re.I)
        offenders = []
        for path in sorted(backend.glob("*.py")):
            for num, line in enumerate(path.read_text().splitlines(), 1):
                if alias.search(line) and "COALESCE" not in line:
                    offenders.append(f"{path.name}:{num}")
        assert not offenders, f"recipient resolved from a mutable join at {offenders}"


class TestRoleLocalDetection:
    @pytest.mark.parametrize("addr", [
        "no-reply@acme.com", "do-not-reply@acme.com", "mailer-daemon@acme.com",
        "auto-reply@acme.com", "bounces@acme.com", "helpdesk@acme.com",
        "legal@acme.com", "noreply@acme.com",
        # Mixed vocabularies: one token from each set.
        "careers.internships@acme.com", "hr.legal@acme.com",
        "jobs-hiring@acme.com", "recruiting.internships@acme.com",
    ])
    def test_recognises_a_role_mailbox(self, addr):
        """Splitting the local on [._+-] means a hyphenated entry can never
        match token-by-token, so no-reply@ read as a person — and got a "this
        address does not look like this contact" warning on an autoresponder."""
        assert _looks_like_role_local(addr) is True

    @pytest.mark.parametrize("addr", [
        "jane.doe@acme.com", "j.smith@acme.com", "erin@acme.com",
        "sam.rivera@acme.com", "a.patel@acme.com",
    ])
    def test_leaves_people_alone(self, addr):
        """Mislabelling a person 'generic' is not merely a wrong chip: the
        address-overwrite branch in _enrich_company_async triggers on exactly
        that value."""
        assert _looks_like_role_local(addr) is False

    def test_an_empty_local_is_not_a_role(self):
        assert _looks_like_role_local("") is False
        assert _looks_like_role_local("@acme.com") is False


class TestWhichModelWroteTheDraftIsRecorded:
    def test_the_column_is_actually_populated(self, db, monkeypatch):
        """Adding the column and the db plumbing without a caller left it
        permanently NULL — the fix was inert."""
        import email_composer

        monkeypatch.setattr(email_composer, "last_model_used",
                            lambda: "gemini-2.5-flash-lite")
        monkeypatch.setattr(
            email_composer, "llm_complete",
            lambda prompt, system=None, max_tokens=0: (
                "Subject: A question about Acme\nBody:\n"
                "Hi Jane,\n\nA perfectly reasonable body of adequate length "
                "for the parser to accept it.\n\nThanks so much,"))
        composer = email_composer.EmailComposer(db, main.resumes)
        out = composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                               email_type="application")
        assert out["llm_model"] == "gemini-2.5-flash-lite"

        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane",
                                    email="jane@acme.com")
        row = db.create_email(contact_id=contact["id"],
                              company_id=company["id"],
                              subject=out["subject"], body=out["body"],
                              llm_model=out["llm_model"])
        assert db.get_email(row["id"])["llm_model"] == "gemini-2.5-flash-lite"


class TestPersonFinderApprovalCrossesTheContactBoundary:
    def test_attach_candidate_is_the_write_path(self, db, monkeypatch):
        """Approving a person-finder candidate with a company must persist
        through contact_ingest.attach_candidate — a rewrite that inserts the
        row directly would keep every visible behavior and silently drop the
        conflict/race handling the boundary exists for."""
        import asyncio

        from models import PersonApproveRequest
        from tests.unit.backend.test_person_finder import (
            FakeEnrichment, _staged_job)
        from person_finder import PersonFinderService

        monkeypatch.setattr(main, "person_finder",
                            PersonFinderService(db, FakeEnrichment()))
        monkeypatch.setattr(
            "contact_verify.domain_has_mx", lambda d, timeout=2.0: True)
        called = {"n": 0}
        real = main.attach_candidate

        def spying(*args, **kwargs):
            called["n"] += 1
            return real(*args, **kwargs)
        monkeypatch.setattr(main, "attach_candidate", spying)
        job_id = _staged_job(db)
        out = asyncio.run(main.approve_person_candidate(
            job_id,
            PersonApproveRequest(candidate_id="c1",
                                 email="jane.doe@acme.com")))
        assert called["n"] == 1
        assert out["contact"]["email"] == "jane.doe@acme.com"
