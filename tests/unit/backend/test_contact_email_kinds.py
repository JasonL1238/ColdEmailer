"""Contacts that predate `email_kind` must still be classified.

Ingest rejects role inboxes and the outreach ranking puts named people first,
but both read `contacts.email_kind`. Anything stored before that column existed
carries 'unknown', so info@acme.com looked exactly like a named human to every
filter, chip and sort — and got an email written to it.
"""

import pytest

from db import repair_contact_email_kinds


def _kind(db, contact_id):
    return db.get_contact(contact_id)["email_kind"]


class TestRepairContactEmailKinds:
    def test_classifies_a_role_inbox(self, db):
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="", email="info@acme.com")
        db.update_contact(c["id"], {"email_kind": "unknown"})
        assert repair_contact_email_kinds(db) == 1
        assert _kind(db, c["id"]) == "generic"

    def test_flags_a_named_address_that_does_not_match_the_person(self, db):
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="Jane Doe",
                              email="bob.smith@acme.com")
        db.update_contact(c["id"], {"email_kind": "unknown"})
        assert repair_contact_email_kinds(db) == 1
        assert _kind(db, c["id"]) == "named_unmatched"

    @pytest.mark.parametrize("name,email", [
        ("Press Releases", "press.releases@acme.com"),
        ("Media Relations", "media.relations@acme.com"),
        ("Job Applications", "job.applications@acme.com"),
        ("Customer Service", "customer.service@acme.com"),
        ("No Reply", "no.reply@acme.com"),
    ])
    def test_never_promotes_a_role_inbox_whose_name_came_from_the_address(
            self, db, name, email):
        """The circular case, and the reason this repair cannot claim 'personal'.

        Discovery stores `name = candidate.name or _guess_name_from_email(local)`,
        and there is no column recording which happened. Comparing such a name
        back against its own local part matches trivially — which would file a
        role inbox as the strongest verdict, list it under the UI's "Personal
        email" filter, and withhold the "role inbox" chip from precisely the
        addresses that need it.
        """
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name=name, email=email)
        db.update_contact(c["id"], {"email_kind": "unknown"})
        repair_contact_email_kinds(db)
        assert _kind(db, c["id"]) != "personal"

    def test_never_claims_personal_even_for_a_genuine_person(self, db):
        """Under-claiming is the deliberate trade. Provenance is unknowable for
        these rows, so a real jane.doe@ is left unconfirmed rather than
        asserted; re-researching the company upgrades it properly."""
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="Jane Doe",
                              email="jane.doe@acme.com")
        db.update_contact(c["id"], {"email_kind": "unknown"})
        repair_contact_email_kinds(db)
        assert _kind(db, c["id"]) == "named_unmatched"

    @pytest.mark.parametrize("email", [
        "legal@acme.com", "privacy@acme.com", "hiring@acme.com",
        "internships@acme.com", "noreply@acme.com", "webmaster@acme.com",
    ])
    def test_catches_role_inboxes_outside_the_known_generic_list(self, db, email):
        """contact_ingest rejects a mailbox it cannot tie to a person for
        exactly this reason: legal@ and privacy@ are not in GENERIC_LOCALS and
        would otherwise pass as 'named_unmatched', i.e. a person."""
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="", email=email)
        db.update_contact(c["id"], {"email_kind": "unknown"})
        assert repair_contact_email_kinds(db) == 1
        assert _kind(db, c["id"]) == "generic"

    def test_leaves_an_already_classified_row_alone(self, db):
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="Jane Doe",
                              email="jane.doe@acme.com")
        db.update_contact(c["id"], {"email_kind": "personal"})
        assert repair_contact_email_kinds(db) == 0

    def test_skips_linkedin_only_contacts(self, db):
        """No address to classify — the row must not even be selected.

        Asserting only on email_kind here would be vacuous: a blank-email row
        already reads 'unknown' before the repair runs. Pinning updated_at
        proves the WHERE clause excluded it rather than the loop skipping it.
        """
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="Jane Doe",
                              email="", linkedin_url="https://linkedin.com/in/jane-doe")
        before = db.get_contact(c["id"])["updated_at"]
        assert repair_contact_email_kinds(db) == 0
        assert db.get_contact(c["id"])["updated_at"] == before

    @pytest.mark.parametrize("junk", ["n/a", "none", "   ", "tbd"])
    def test_an_unparseable_address_is_never_reselected(self, db, junk):
        """`email <> ''` alone let junk like 'n/a' through. Nothing can classify
        it, so it was re-examined on every startup forever.

        Both the LIKE '%_@_%' filter and the `if kind == "unknown": continue`
        guard keep it from being written; this pins the outcome the user cares
        about — the row is never touched, however often the repair runs — not
        which of the two did the work."""
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="Jane", email=junk)
        db.update_contact(c["id"], {"email_kind": "unknown"})
        before = db.get_contact(c["id"])["updated_at"]
        for _ in range(3):
            assert repair_contact_email_kinds(db) == 0
        assert db.get_contact(c["id"])["updated_at"] == before

    def test_is_idempotent(self, db):
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="", email="hello@acme.com")
        db.update_contact(c["id"], {"email_kind": "unknown"})
        assert repair_contact_email_kinds(db) == 1
        assert repair_contact_email_kinds(db) == 0

    def test_writes_email_kind_and_nothing_else(self, db):
        """email_verified means "MX was confirmed" and person_verified means a
        human was matched; the repair does no DNS and trusts no name, so it
        must leave both exactly as it found them.

        Asserting they are 0 afterwards would be vacuous — check_mx=False can
        only ever produce False. Seeding them to 1 first is what gives this
        teeth: a repair that wrote either field back would clobber them.
        """
        company = db.create_company("Acme")
        c = db.create_contact(company_id=company["id"], name="Jane Doe",
                              email="jane.doe@acme.com")
        db.update_contact(c["id"], {"email_kind": "unknown",
                                    "email_verified": 1, "person_verified": 1,
                                    "linkedin_verified": 1, "seniority_rank": 3})
        repair_contact_email_kinds(db)
        row = db.get_contact(c["id"])
        assert row["email_kind"] == "named_unmatched"
        assert row["email_verified"] == 1
        assert row["person_verified"] == 1
        assert row["linkedin_verified"] == 1
        assert row["seniority_rank"] == 3

    def test_logs_what_it_changed(self, db):
        company = db.create_company("Acme")
        for addr in ("info@acme.com", "careers@acme.com"):
            c = db.create_contact(company_id=company["id"], name="", email=addr)
            db.update_contact(c["id"], {"email_kind": "unknown"})
        assert repair_contact_email_kinds(db) == 2
        events = db.query("SELECT detail FROM events WHERE event='repair'")
        assert any("2 unclassified contact address" in e["detail"] for e in events)


class TestTheEmailListExposesTheKind:
    def test_list_emails_carries_contact_email_kind(self, db):
        """The drafts screen flags a role inbox before you send to it, which it
        can only do if the join surfaces the classification."""
        company = db.create_company("Acme")
        contact = db.create_contact(company_id=company["id"], name="",
                                    email="info@acme.com")
        db.update_contact(contact["id"], {"email_kind": "generic"})
        db.create_email(contact_id=contact["id"], company_id=company["id"],
                        subject="Hi", body="A body long enough to be realistic.")
        row = db.list_emails()[0]
        assert row["contact_email_kind"] == "generic"
