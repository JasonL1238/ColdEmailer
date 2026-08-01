"""Changing a contact's address must not quietly change history or warnings.

Two defects, both surfaced by classifying legacy contacts. Before that, those
rows read email_kind='unknown', which happened to keep them out of the branches
below; giving them a real classification armed both.
"""
import asyncio
import os
import tempfile

import pytest

import main
from db import Database, now_iso
from models import ContactUpdate


@pytest.fixture
def db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Database(os.path.join(tmp, "test.db"))
        monkeypatch.setattr(main, "db", fresh)
        yield fresh


class TestAnUpgradeMustNotMoveASentAddress:
    """`_enrich_company_async` upgrades a role inbox to a person mailbox when
    re-research finds one. That is right for an untouched contact and wrong
    once mail has gone out: the recipient of a sent email was resolved by live
    join, so moving it rewrote who had already been written to, and the
    follow-up ("following up on my note from…") went to someone who never got
    a note.
    """

    def _contact(self, db, **email_kwargs):
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="careers@acme.com")
        db.update_contact(contact["id"], {"email_kind": "generic"})
        if email_kwargs:
            email = db.create_email(contact_id=contact["id"],
                                    company_id=company["id"], subject="Hi",
                                    body="A body long enough to be realistic.")
            db.update_email(email["id"], email_kwargs)
        return contact

    def test_an_untouched_contact_may_still_be_upgraded(self, db):
        contact = self._contact(db)
        assert main._contact_has_been_emailed(contact["id"]) is False

    def test_a_contact_with_sent_mail_is_locked(self, db):
        contact = self._contact(db, status="sent", sent_at=now_iso(),
                                gmail_message_id="gm-1")
        assert main._contact_has_been_emailed(contact["id"]) is True

    def test_a_delivered_row_counts_even_if_the_status_lags(self, db):
        """Legacy rows arrived carrying a Gmail id while still labelled
        approved — the message is out regardless of the status column."""
        contact = self._contact(db, status="approved", gmail_message_id="gm-2")
        assert main._contact_has_been_emailed(contact["id"]) is True

    def test_a_draft_does_not_lock_anything(self, db):
        contact = self._contact(db, status="draft")
        assert main._contact_has_been_emailed(contact["id"]) is False

    def test_no_contact_id_is_not_a_lock(self, db):
        assert main._contact_has_been_emailed(None) is False


class TestEditingTheAddressReclassifiesIt:
    """PUT /api/contacts/{id} let the address change without recomputing
    email_kind, so a person mailbox edited to careers@ kept 'personal' and the
    drafts screen stayed silent on a role inbox. Once the warning exists,
    silence reads as an all-clear."""

    def _put(self, contact_id, **updates):
        return asyncio.run(main.update_contact(contact_id, ContactUpdate(**updates)))

    def test_editing_a_person_to_a_role_inbox_reclassifies(self, db):
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane.doe@acme.com")
        db.update_contact(contact["id"], {"email_kind": "personal",
                                          "email_verified": 1})
        row = self._put(contact["id"], email="careers@acme.com")
        assert row["email_kind"] == "generic"

    def test_the_old_verification_flags_do_not_carry_over(self, db):
        """They described the previous mailbox; keeping them would claim the
        new address was checked when it never was."""
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane.doe@acme.com")
        db.update_contact(contact["id"], {"email_kind": "personal",
                                          "email_verified": 1,
                                          "person_verified": 1})
        row = self._put(contact["id"], email="someone.else@acme.com")
        assert row["email_verified"] == 0
        assert row["person_verified"] == 0

    def test_an_edit_that_leaves_the_address_alone_changes_nothing(self, db):
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane.doe@acme.com")
        db.update_contact(contact["id"], {"email_kind": "personal",
                                          "email_verified": 1})
        row = self._put(contact["id"], role="Head of Engineering")
        assert row["email_kind"] == "personal"
        assert row["email_verified"] == 1
