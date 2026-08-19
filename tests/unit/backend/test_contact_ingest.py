"""The contact boundary: inbound sanitization, and attaching scraped people."""
from contact_ingest import (attach_candidate, contact_notes, find_existing,
                            owned_elsewhere_note, sanitize_inbound_contact,
                            verified_channels)


class TestSanitizeInbound:
    def test_rejects_generic_inbox(self):
        cleaned, err = sanitize_inbound_contact(
            name="Support", email="info@acme.com", check_mx=False)
        assert cleaned is None
        assert err == "company_or_role_inbox"

    def test_rejects_email_that_does_not_match_name(self):
        cleaned, err = sanitize_inbound_contact(
            name="Jane Doe", email="john.smith@acme.com", check_mx=False)
        assert cleaned is None
        assert err == "email_does_not_match_name"

    def test_accepts_person_email(self):
        cleaned, err = sanitize_inbound_contact(
            name="Jane Doe", email="jane.doe@acme.com", check_mx=False)
        assert err is None
        assert cleaned["email"] == "jane.doe@acme.com"
        assert cleaned["email_kind"] == "personal"

    def test_keeps_matching_linkedin_when_email_is_generic(self):
        cleaned, err = sanitize_inbound_contact(
            name="Jane Doe",
            email="hello@acme.com",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            check_mx=False,
        )
        assert err is None
        assert cleaned["email"] == ""
        assert cleaned["linkedin_verified"] is True

    def test_rejects_nameless_linkedin(self):
        cleaned, err = sanitize_inbound_contact(
            name="",
            email="",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            check_mx=False,
        )
        assert cleaned is None
        assert err == "linkedin_needs_name"

    def test_rejects_malformed_email(self):
        cleaned, err = sanitize_inbound_contact(
            name="Jane Doe", email="jane.doe@@example.com", check_mx=False)
        assert cleaned is None
        assert err == "invalid_email"

    def test_rejects_email_without_person_name(self):
        cleaned, err = sanitize_inbound_contact(
            name="", email="legal@acme.com", check_mx=False)
        assert cleaned is None
        assert err == "name_required_for_email"
        cleaned, err = sanitize_inbound_contact(
            name="", email="jane.doe@acme.com", check_mx=False)
        assert cleaned is None
        assert err == "name_required_for_email"

    def test_drops_mismatched_linkedin_with_warning(self):
        cleaned, err = sanitize_inbound_contact(
            name="Jane Doe",
            email="jane.doe@acme.com",
            linkedin_url="https://www.linkedin.com/in/john-smith",
            check_mx=False,
        )
        assert err is None
        assert cleaned["email"] == "jane.doe@acme.com"
        assert cleaned["linkedin_url"] is None
        assert cleaned["ingest_warning"] == "linkedin_does_not_match_name"


def _candidate(**overrides):
    base = {
        "name": "Jane Doe",
        "email": "jane@newco.com",
        "linkedin_url": "https://www.linkedin.com/in/jane-doe",
        "role": "CTO",
        "email_kind": "personal",
        "email_person_match": True,
        "email_verified": True,
        "linkedin_verified": True,
        "person_verified": True,
        "seniority_rank": 2,
    }
    base.update(overrides)
    return base


class TestVerifiedChannels:
    """Discovery, deep research and re-research all filter candidates here."""

    def test_keeps_both_channels_when_both_verified(self):
        assert verified_channels(_candidate()) == (
            "jane@newco.com", "https://www.linkedin.com/in/jane-doe")

    def test_deliverable_guess_note_does_not_claim_ownership(self):
        note = contact_notes(_candidate(
            email="", email_verified=False, email_person_match=False,
            email_guess="jane.doe@newco.com",
            email_guess_smtp_status="deliverable"))
        assert "accepts mail" in note
        assert "ownership is not proven" in note

    def test_drops_linkedin_that_failed_slug_verification(self):
        email, linkedin = verified_channels(
            _candidate(linkedin_verified=False))
        assert email == "jane@newco.com"
        assert linkedin == ""

    def test_drops_a_role_inbox_but_keeps_the_person_on_linkedin(self):
        email, linkedin = verified_channels(
            _candidate(email="hello@newco.com", email_kind="generic"))
        assert email == ""
        assert linkedin == "https://www.linkedin.com/in/jane-doe"

    def test_drops_a_named_address_that_matches_neither_name_nor_mx(self):
        email, _ = verified_channels(_candidate(
            email_person_match=False, email_verified=False))
        assert email == ""

    def test_keeps_an_unmatched_address_when_nobody_is_named(self):
        # Nothing to contradict: without a name there is no mismatch to find.
        email, _ = verified_channels(_candidate(
            name="", email_person_match=False, email_verified=False))
        assert email == "jane@newco.com"


class TestAttachCandidate:
    def test_stores_the_person_and_reports_both_channels_landed(self, db):
        company = db.create_company("NewCo")
        result = attach_candidate(
            db, company=company, candidate=_candidate(),
            email="jane@newco.com",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            source="discovery")
        assert result.conflicts == []
        assert result.is_new_outreach
        assert result.email_landed and result.linkedin_landed
        stored = db.get_contact(result.contact["id"])
        assert stored["company_id"] == company["id"]
        assert stored["role"] == "CTO"
        assert stored["seniority_rank"] == 2

    def test_a_person_held_by_another_company_is_not_reassigned(self, db):
        old = db.create_company("OldCo")
        db.create_contact(company_id=old["id"], name="Jane Doe",
                          email="jane@newco.com")
        new = db.create_company("NewCo")
        result = attach_candidate(
            db, company=new, candidate=_candidate(linkedin_url=None),
            email="jane@newco.com", linkedin_url="", source="discovery")
        assert result.contact is None
        assert not result.is_new_outreach
        assert any("already belongs to OldCo" in c for c in result.conflicts)

    def test_that_conflict_reaches_the_activity_feed(self, db):
        old = db.create_company("OldCo")
        db.create_contact(company_id=old["id"], name="Jane Doe",
                          email="jane@newco.com")
        new = db.create_company("NewCo")
        attach_candidate(db, company=new, candidate=_candidate(),
                         email="jane@newco.com", linkedin_url="",
                         source="discovery")
        kinds = [e["event"] for e in db.recent_events(limit=50)]
        assert "contact_conflict" in kinds

    def test_log_events_false_still_returns_the_conflict(self, db):
        """Deep research reports on the company row it is already updating."""
        old = db.create_company("OldCo")
        db.create_contact(company_id=old["id"], name="Jane Doe",
                          email="jane@newco.com")
        new = db.create_company("NewCo")
        result = attach_candidate(
            db, company=new, candidate=_candidate(), email="jane@newco.com",
            linkedin_url="", source="deep_research", log_events=False)
        assert result.conflicts
        kinds = [e["event"] for e in db.recent_events(limit=50)]
        assert "contact_conflict" not in kinds

    def test_a_linkedin_url_that_did_not_land_is_reported(self, db, monkeypatch):
        """The guard deep research was missing before this moved into one place.

        Another insert took the URL between the pre-check and this one, so the
        row that comes back carries the email and not the profile. Saying
        nothing would leave the user believing they have a LinkedIn for this
        person, and the LinkedIn message flow would offer to draft one.
        """
        company = db.create_company("NewCo")
        landed_without_li = db.create_contact(
            company_id=company["id"], name="Jane Doe", email="jane@newco.com")
        holder = db.create_contact(
            company_id=company["id"], name="Jane Doe",
            linkedin_url="https://www.linkedin.com/in/jane-doe")
        monkeypatch.setattr(db, "create_contact",
                            lambda **_k: dict(landed_without_li))
        monkeypatch.setattr(db, "find_contact_by_linkedin",
                            lambda *_a, **_k: holder)

        result = attach_candidate(
            db, company=company, candidate=_candidate(),
            email="jane@newco.com",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            source="deep_research", log_events=False)
        assert result.contact is not None
        assert result.email_landed
        assert not result.linkedin_landed
        assert any("could not be attached to NewCo" in c
                   for c in result.conflicts)

    def test_notes_default_to_the_candidate_provenance(self, db):
        company = db.create_company("NewCo")
        result = attach_candidate(
            db, company=company,
            candidate=_candidate(source_url="https://newco.com/team"),
            email="jane@newco.com", linkedin_url="", source="discovery")
        assert "Source: https://newco.com/team" in result.contact["notes"]

    def test_an_explicit_note_wins(self, db):
        company = db.create_company("NewCo")
        result = attach_candidate(
            db, company=company, candidate=_candidate(),
            email="jane@newco.com", linkedin_url="", source="deep_research",
            notes="Criteria match: alumni")
        assert result.contact["notes"] == "Criteria match: alumni"

    def test_a_stated_campaign_is_not_overridden_by_the_company(self, db):
        campaign = db.create_campaign("March fintech")
        other = db.create_campaign("Old run")
        company = db.create_company("NewCo", campaign_id=other["id"])
        result = attach_candidate(
            db, company=company, candidate=_candidate(),
            email="jane@newco.com", linkedin_url="", source="discovery",
            campaign_id=campaign["id"])
        assert result.contact["campaign_id"] == campaign["id"]


class TestLookupHelpers:
    def test_find_existing_prefers_the_email_match(self, db):
        company = db.create_company("NewCo")
        by_email = db.create_contact(company_id=company["id"], name="Jane Doe",
                                     email="jane@newco.com")
        db.create_contact(company_id=company["id"], name="John Smith",
                          linkedin_url="https://www.linkedin.com/in/john-smith")
        found = find_existing(db, "jane@newco.com",
                              "https://www.linkedin.com/in/john-smith")
        assert found["id"] == by_email["id"]

    def test_find_existing_falls_back_to_linkedin(self, db):
        company = db.create_company("NewCo")
        by_li = db.create_contact(
            company_id=company["id"], name="John Smith",
            linkedin_url="https://www.linkedin.com/in/john-smith")
        found = find_existing(db, "", "https://www.linkedin.com/in/john-smith")
        assert found["id"] == by_li["id"]

    def test_an_unknown_owner_is_named_generically(self, db):
        note = owned_elsewhere_note(db, None, "jane@newco.com", "NewCo")
        assert note == ("jane@newco.com already belongs to another company — "
                        "not reassigned to NewCo")


def test_role_inboxes_never_become_a_person_named_after_the_inbox():
    """One generic-inbox vocabulary, `contact_verify.GENERIC_LOCALS`.

    contact_ingest used to carry its own 19-word copy that shadowed the
    canonical 46-word one and did not split on separators, so compound role
    addresses were named as if they were people: `sales.support@acme.com`
    produced a contact called "Sales Support", which then looked like an
    identified human everywhere downstream.
    """
    from contact_ingest import guess_name_from_email

    for role_local in ("people.ops", "hr.team", "admin.office",
                       "sales.support", "info.desk", "billing.accounts",
                       "press.media", "careers", "hello"):
        assert guess_name_from_email(role_local) == "", role_local

    # Real names are untouched, including the ones that merely start with a
    # role-ish token.
    assert guess_name_from_email("jane.doe") == "Jane Doe"
    assert guess_name_from_email("john.smith") == "John Smith"
    assert guess_name_from_email("mary.jane.watson") == "Mary Jane Watson"
