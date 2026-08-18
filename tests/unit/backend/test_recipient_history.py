"""Who an email went to must not change after it was sent.

The `emails` table had no recipient column: every read path resolved the
recipient by live join onto `contacts.email`. So editing a contact — or
re-researching their company, which can upgrade a role inbox to a person
mailbox — retroactively rewrote the record of who had already been written to.
CLAUDE.md calls that record "what prevents emailing someone twice".

The concrete failure it enables: a follow-up is composed as "following up on my
note from <date>" and is addressed by the same join, so it arrives at someone
who never received the note.
"""

import pytest

from db import now_iso


@pytest.fixture
def sent(db):
    company = db.create_company("Acme")
    contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                email="careers@acme.com")
    email = db.create_email(
        contact_id=contact["id"], company_id=company["id"],
        subject="Internship inquiry at Acme",
        body="Hi Jane,\n\nA body long enough to be realistic.\n\nThanks so much,")
    db.update_email(email["id"], {
        "status": "sent", "sent_at": now_iso(), "gmail_message_id": "gm-1",
        "recipient_email": "careers@acme.com",
    })
    return {"company": company, "contact": contact, "email": email}


class TestTheRecipientIsFrozenAtSend:
    def test_the_column_exists_on_a_fresh_database(self, db):
        cols = {r["name"] for r in db.query("PRAGMA table_info(emails)")}
        assert "recipient_email" in cols

    def test_editing_the_contact_does_not_rewrite_history(self, db, sent):
        db.update_contact(sent["contact"]["id"], {"email": "jane.doe@acme.com"})
        row = db.get_email(sent["email"]["id"])
        assert row["contact_email"] == "careers@acme.com"

    def test_the_list_view_agrees_with_the_detail_view(self, db, sent):
        """Compare the two views to each other, not each to a literal: a test
        that checks only the list would pass while the two disagreed."""
        db.update_contact(sent["contact"]["id"], {"email": "jane.doe@acme.com"})
        listed = [e for e in db.list_emails() if e["id"] == sent["email"]["id"]][0]
        detail = db.get_email(sent["email"]["id"])
        assert listed["contact_email"] == detail["contact_email"]
        assert listed["contact_email"] == "careers@acme.com"

    def test_a_follow_up_candidate_keeps_the_original_address(self, db, sent):
        """The follow-up says "following up on my note" — sending it to an
        address that never got the note is the whole failure."""
        db.execute("UPDATE emails SET sent_at=datetime('now','-30 days') WHERE id=?",
                   (sent["email"]["id"],))
        db.update_contact(sent["contact"]["id"], {"email": "jane.doe@acme.com"})
        candidates = db.get_follow_up_candidates(days=7)
        assert len(candidates) == 1
        assert candidates[0]["contact_email"] == "careers@acme.com"

    def test_a_draft_still_follows_the_live_contact(self, db):
        """Nothing has been sent, so correcting the address must take effect —
        freezing too early would send to a mailbox the user just fixed."""
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="wrong@acme.com")
        email = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Hi", body="A body long enough here.")
        db.update_contact(contact["id"], {"email": "jane.doe@acme.com"})
        assert db.get_email(email["id"])["contact_email"] == "jane.doe@acme.com"

    def test_legacy_sent_rows_still_resolve_through_the_join(self, db):
        """Rows sent before the column existed have recipient_email NULL and
        must keep showing something rather than blank."""
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="Jane Doe",
                                    email="jane.doe@acme.com")
        email = db.create_email(contact_id=contact["id"], company_id=company["id"],
                                subject="Hi", body="A body long enough here.")
        db.update_email(email["id"], {"status": "sent", "sent_at": now_iso(),
                                      "gmail_message_id": "gm-legacy"})
        assert db.get_email(email["id"])["recipient_email"] is None
        assert db.get_email(email["id"])["contact_email"] == "jane.doe@acme.com"
