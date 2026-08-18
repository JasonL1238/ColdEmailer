"""Person email + LinkedIn verification."""
from contact_verify import (
    annotate_contact,
    email_matches_person,
    is_generic_inbox,
    linkedin_matches_person,
    select_verified_person_contacts,
    verify_email,
    verify_linkedin,
)


class TestGenericInbox:
    def test_flags_common_company_locals(self):
        assert is_generic_inbox("hello@acme.com")
        assert is_generic_inbox("info")
        assert is_generic_inbox("careers@acme.com")
        assert is_generic_inbox("founders")
        assert not is_generic_inbox("jane.doe@acme.com")
        assert not is_generic_inbox("jdoe@acme.com")


class TestEmailMatchesPerson:
    def test_first_last_local(self):
        assert email_matches_person("jane.doe@acme.com", "Jane Doe")
        assert email_matches_person("jdoe@acme.com", "Jane Doe")
        assert email_matches_person("janedoe@acme.com", "Jane Doe")
        assert email_matches_person("doe.jane@acme.com", "Jane Doe")

    def test_rejects_generic_even_with_name(self):
        assert not email_matches_person("hello@acme.com", "Jane Doe")
        assert not email_matches_person("info@acme.com", "Jane Doe")

    def test_rejects_wrong_person_and_weak_forms(self):
        assert not email_matches_person("jane.doe@acme.com", "John Smith")
        assert not email_matches_person("jane@acme.com", "Jane Doe")
        assert not email_matches_person("john.smith@acme.com", "Jane Smith")
        assert not email_matches_person("doe@acme.com", "Jane Doe")
        assert not email_matches_person("shannon@acme.com", "Ann Smith")
        assert not email_matches_person("christian.meyer@acme.com", "Chris Anderson")
        assert not email_matches_person("johnson@acme.com", "John Smith")
        assert not email_matches_person("maximum@acme.com", "Max Power")

    def test_rejects_circular_name_inferred_from_email(self):
        assert not email_matches_person(
            "kim@acme.com", "Kim", name_from_email=True)

    def test_accented_names_fold(self):
        assert email_matches_person("jose.garcia@acme.com", "José García")
        assert not email_matches_person("joshua.garcin@acme.com", "José García")


class TestLinkedInMatchesPerson:
    def test_slug_must_include_identifying_tokens(self):
        assert linkedin_matches_person(
            "https://www.linkedin.com/in/jane-doe", "Jane Doe")
        assert linkedin_matches_person(
            "https://www.linkedin.com/in/jane-doe-a12b34", "Jane Doe")
        assert not linkedin_matches_person(
            "https://www.linkedin.com/in/jane", "Jane Doe")
        assert not linkedin_matches_person(
            "https://www.linkedin.com/in/john-smith", "Jane Doe")
        assert not linkedin_matches_person(
            "https://www.linkedin.com/in/alex-kimberly-jones", "Alex Kim")

    def test_accented_and_apostrophe_names(self):
        assert linkedin_matches_person(
            "https://www.linkedin.com/in/jose-garcia", "José García")
        assert linkedin_matches_person(
            "https://www.linkedin.com/in/patrick-obrien", "Patrick O'Brien")

    def test_verify_linkedin_flags(self):
        ok = verify_linkedin("https://www.linkedin.com/in/jane-doe", "Jane Doe")
        assert ok["linkedin_verified"] is True
        bad = verify_linkedin("https://www.linkedin.com/in/jane", "Jane Doe")
        assert bad["linkedin_verified"] is False


class TestSelectVerified:
    def test_prefers_person_email_over_company_inbox(self):
        candidates = [
            {"email": "hello@acme.com", "name": "", "role": None,
             "seniority_rank": 10, "on_domain": True, "school_match": False},
            {"email": "jane.doe@acme.com", "name": "Jane Doe", "role": "CEO",
             "seniority_rank": 0, "on_domain": True, "school_match": False,
             "linkedin_url": "https://www.linkedin.com/in/jane-doe"},
        ]
        picked = select_verified_person_contacts(
            candidates, limit=1, check_mx=False)
        assert picked[0]["email"] == "jane.doe@acme.com"
        # MX skipped → email_verified stays false; LinkedIn still verifies person
        assert picked[0]["email_verified"] is False
        assert picked[0]["email_person_match"] is True
        assert picked[0]["linkedin_verified"] is True
        assert picked[0]["person_verified"] is True

    def test_drops_generics_when_require_person(self):
        candidates = [
            {"email": "hello@acme.com", "name": "", "role": None,
             "seniority_rank": 10, "on_domain": True, "school_match": False},
        ]
        assert select_verified_person_contacts(
            candidates, limit=1, check_mx=False) == []

    def test_verify_email_marks_generic(self):
        info = verify_email("info@acme.com", "Jane Doe", check_mx=False)
        assert info["email_kind"] == "generic"
        assert info["email_verified"] is False

    def test_mx_skipped_does_not_claim_email_verified(self):
        info = verify_email("jane.doe@acme.com", "Jane Doe", check_mx=False)
        assert info["email_person_match"] is True
        assert info["email_verified"] is False
        assert info["reason"] == "mx_not_checked"

    def test_annotate_rejects_name_from_email_loop(self):
        out = annotate_contact({
            "email": "kim@acme.com",
            "name": "Kim",
            "name_from_email": True,
            "linkedin_url": None,
        }, check_mx=False)
        assert out["email_person_match"] is False
        assert out["person_verified"] is False

    def test_annotate_rejects_on_domain_first_name_inferred_from_email(self):
        """Page-less first-name locals must not clear name_from_email."""
        out = annotate_contact({
            "email": "kim@acme.com",
            "name": "Kim",
            "name_from_email": True,
            "on_domain": True,
            "linkedin_url": None,
        }, check_mx=False)
        assert out["name_from_email"] is True
        assert out["email_person_match"] is False
        assert out["email_verified"] is False
        assert out["person_verified"] is False

    def test_unchecked_mx_does_not_person_verify_first_name_local(self):
        out = annotate_contact({
            "email": "kim@acme.com",
            "name": "Kim",
            "name_from_email": False,
            "on_domain": True,
            "linkedin_url": None,
        }, check_mx=False)
        assert out["email_mx_ok"] is None
        assert out["email_person_match"] is True
        assert out["email_verified"] is False
        assert out["person_verified"] is False


def test_classify_email_domain_respects_two_part_tlds():
    """Two unrelated .co.uk employers must not compare equal.

    The old private `_email_registered_domain` took the last two labels, so
    `other.co.uk` and `acme.co.uk` both reduced to `co.uk` and every .co.uk
    address was classified as the company's own.
    """
    from person_finder import classify_email_domain

    assert classify_email_domain("jane@acme.co.uk", "acme.co.uk") == "company"
    assert classify_email_domain("bob@other.co.uk", "acme.co.uk") == "other"
    assert classify_email_domain("jane@acme.com", "acme.com") == "company"
    # No host on either side must never read as "company".
    assert classify_email_domain("nobody", "acme.com") == "other"
