"""Inbound contact sanitization for CSV / manual create."""
from contact_ingest import sanitize_inbound_contact


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
