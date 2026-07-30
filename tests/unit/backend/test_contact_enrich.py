"""Offline unit tests for LinkedIn search + free email enrichment."""
from contact_enrich import (
    email_patterns_for,
    enrich_contacts_outreach,
    extract_linkedin_urls,
    find_linkedin_via_search,
    hunter_find_email,
)


class TestLinkedInSearch:
    def test_extracts_profile_urls(self):
        urls = extract_linkedin_urls(
            "See https://www.linkedin.com/in/jane-doe-1234 and "
            "http://linkedin.com/in/jane-doe-1234/"
        )
        assert urls == ["https://www.linkedin.com/in/jane-doe-1234"]

    def test_requires_company_mention_in_snippet(self):
        def search(_q, max_results=5):
            return [
                {
                    "title": "Jane Doe - Acme Robotics",
                    "href": "https://www.linkedin.com/in/jane-doe",
                    "body": "CTO at Acme Robotics",
                },
            ]
        url = find_linkedin_via_search(
            "Jane Doe", "Acme Robotics", search_fn=search)
        assert url == "https://www.linkedin.com/in/jane-doe"

    def test_rejects_same_name_without_company_evidence(self):
        def search(_q, max_results=5):
            return [{
                "title": "Jane Doe - OtherCorp",
                "href": "https://www.linkedin.com/in/jane-doe-77",
                "body": "Engineer at OtherCorp",
            }]
        assert find_linkedin_via_search(
            "Jane Doe", "Acme Robotics", search_fn=search) is None

    def test_rejects_company_token_collision(self):
        """Acme AI must not bind a Jane Doe at Acme Analytics."""
        def search(_q, max_results=5):
            return [{
                "title": "Jane Doe - Acme Analytics",
                "href": "https://www.linkedin.com/in/jane-doe",
                "body": "Engineer at Acme Analytics",
            }]
        assert find_linkedin_via_search(
            "Jane Doe", "Acme AI", search_fn=search) is None

    def test_ignores_company_token_only_in_profile_url(self):
        def search(_q, max_results=5):
            return [{
                "title": "Jane Doe - OtherCorp",
                "href": "https://www.linkedin.com/in/jane-doe-acme",
                "body": "Engineer at OtherCorp",
            }]
        assert find_linkedin_via_search(
            "Jane Doe", "Acme Robotics", search_fn=search) is None

    def test_rejects_slug_name_mismatch(self):
        def search(_q, max_results=5):
            return [{
                "title": "John Smith - Acme Robotics",
                "href": "https://www.linkedin.com/in/john-smith",
                "body": "Works at Acme Robotics",
            }]
        assert find_linkedin_via_search(
            "Jane Doe", "Acme Robotics", search_fn=search) is None


class TestEmailLookup:
    def test_patterns_are_person_shaped(self):
        patterns = email_patterns_for("Jane Doe", "acme.com")
        assert "jane.doe@acme.com" in patterns
        assert "jdoe@acme.com" in patterns
        assert "hello@acme.com" not in patterns

    def test_hunter_uses_api_and_validates_name(self):
        class Resp:
            status_code = 200
            def json(self):
                return {"data": {"email": "jane.doe@acme.com", "score": 92}}

        found = hunter_find_email(
            "Jane Doe", "acme.com",
            api_key="test-key",
            http_get=lambda *_a, **_k: Resp(),
        )
        assert found["email"] == "jane.doe@acme.com"
        assert found["email_source"] == "hunter"
        assert found["on_domain"] is True

    def test_hunter_rejects_off_domain_and_gmail(self):
        class GmailResp:
            status_code = 200
            def json(self):
                return {"data": {"email": "jane.doe@gmail.com", "score": 90}}

        assert hunter_find_email(
            "Jane Doe", "acme.com",
            api_key="test-key",
            http_get=lambda *_a, **_k: GmailResp(),
        ) is None

        class OtherResp:
            status_code = 200
            def json(self):
                return {"data": {"email": "jane.doe@otherco.com", "score": 90}}

        assert hunter_find_email(
            "Jane Doe", "acme.com",
            api_key="test-key",
            http_get=lambda *_a, **_k: OtherResp(),
        ) is None

    def test_hunter_rejects_mismatched_email(self):
        class Resp:
            status_code = 200
            def json(self):
                return {"data": {"email": "bob@acme.com", "score": 90}}

        assert hunter_find_email(
            "Jane Doe", "acme.com",
            api_key="test-key",
            http_get=lambda *_a, **_k: Resp(),
        ) is None

    def test_outreach_fills_linkedin_and_keeps_pattern_as_guess_only(self):
        def search(_q, max_results=5):
            return [{
                "title": "Jane Doe - Acme Robotics",
                "href": "https://www.linkedin.com/in/jane-doe",
                "body": "CTO at Acme Robotics",
            }]

        enriched = enrich_contacts_outreach(
            [{"name": "Jane Doe", "role": "CTO", "email": "",
              "on_domain": True, "name_from_email": False}],
            company_name="Acme Robotics",
            domain="acme.com",
            search_fn=search,
            hunter_key="",
            check_mx=False,
            allow_pattern_guesses=True,
        )
        assert enriched[0]["linkedin_verified"] is True
        assert not enriched[0].get("email")
        assert enriched[0].get("email_guess") == "jane.doe@acme.com"
        assert enriched[0].get("email_verified") is not True

    def test_pattern_guess_skipped_without_linkedin_when_disabled(self):
        enriched = enrich_contacts_outreach(
            [{"name": "Jane Doe", "role": "CTO", "email": "",
              "on_domain": True, "name_from_email": False}],
            company_name="Acme",
            domain="acme.com",
            search_fn=lambda *_a, **_k: [],
            hunter_key="",
            check_mx=False,
            allow_pattern_guesses=False,
        )
        assert not enriched[0].get("email")
        assert not enriched[0].get("email_guess")
        assert not enriched[0].get("linkedin_verified")
