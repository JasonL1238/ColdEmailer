"""Tests for email extraction, domain parsing, and outreach ranking."""
from enrichment import (
    extract_emails_from_html,
    heuristic_metadata,
    rank_outreach_emails,
    registered_domain,
)


class TestRegisteredDomain:
    def test_strips_scheme_and_www(self):
        assert registered_domain("https://www.acme.com/about") == "acme.com"

    def test_handles_bare_domains(self):
        assert registered_domain("acme.io") == "acme.io"

    def test_handles_two_part_tlds(self):
        assert registered_domain("https://shop.acme.co.uk") == "acme.co.uk"

    def test_strips_port_and_subdomain(self):
        assert registered_domain("http://blog.acme.com:8080/x") == "acme.com"

    def test_returns_none_for_empty(self):
        assert registered_domain("") is None
        assert registered_domain(None) is None


class TestExtractEmails:
    def test_finds_mailto_and_plain_text_emails(self):
        html = '<a href="mailto:hello@acme.com">Say hi</a><p>careers@acme.com</p>'
        assert set(extract_emails_from_html(html)) == {"hello@acme.com", "careers@acme.com"}

    def test_deduplicates_case_insensitively(self):
        html = "Hello@Acme.com and hello@acme.com"
        assert len(extract_emails_from_html(html)) == 1

    def test_rejects_noreply_and_system_addresses(self):
        html = "noreply@acme.com postmaster@acme.com abuse@acme.com privacy@acme.com"
        assert extract_emails_from_html(html) == []

    def test_rejects_placeholder_domains(self):
        html = "someone@example.com hi@yourdomain.com x@wixpress.com"
        assert extract_emails_from_html(html) == []

    def test_rejects_image_filenames_that_look_like_emails(self):
        assert extract_emails_from_html("sprite@2x.png") == []

    def test_handles_empty_input(self):
        assert extract_emails_from_html("") == []
        assert extract_emails_from_html(None) == []

    def test_trims_trailing_punctuation(self):
        assert extract_emails_from_html("Contact us at hi@acme.com.") == ["hi@acme.com"]


class TestRankOutreachEmails:
    def test_company_domain_outranks_third_party(self):
        ranked = rank_outreach_emails(
            ["someone@gmail.com", "hello@acme.com"], "acme.com")
        assert ranked[0] == "hello@acme.com"

    def test_founders_outrank_generic_support(self):
        ranked = rank_outreach_emails(
            ["support@acme.com", "founders@acme.com"], "acme.com")
        assert ranked[0] == "founders@acme.com"

    def test_deduplicates(self):
        assert rank_outreach_emails(["a@acme.com", "a@acme.com"], "acme.com") == ["a@acme.com"]

    def test_handles_no_company_domain(self):
        assert len(rank_outreach_emails(["a@x.com", "b@y.com"], None)) == 2


class TestHeuristicMetadata:
    def test_builds_summary_from_substantial_sentences(self):
        text = ("We build tools for developers who ship fast every single day. "
                "Short. Our platform handles deployment, monitoring, and rollback for you.")
        meta = heuristic_metadata("Acme", text)
        assert "developers" in meta["summary"]
        assert "Short." not in meta["summary"]

    def test_returns_empty_dict_for_no_text(self):
        assert heuristic_metadata("Acme", "") == {}
