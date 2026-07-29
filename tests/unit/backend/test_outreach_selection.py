"""Which scraped addresses become contacts.

A company page routinely lists other companies' addresses (vendors, agencies,
an acquirer). Emailing one of those pitches the wrong company, so the selector
prefers the company's own domain and flags anything else.
"""
from enrichment import select_outreach_emails


class TestSelectOutreachEmails:
    def test_prefers_company_domain_and_drops_third_parties(self):
        picked = select_outreach_emails(
            ["hello@ultravox.ai", "team@fixie.ai", "info@fixie.ai"], "fixie.ai")
        assert [e for e, _ in picked] == ["team@fixie.ai", "info@fixie.ai"]
        assert all(note is None for _, note in picked)

    def test_falls_back_to_off_domain_with_a_warning(self):
        picked = select_outreach_emails(["hello@ultravox.ai"], "fixie.ai")
        assert len(picked) == 1
        email, note = picked[0]
        assert email == "hello@ultravox.ai"
        assert note and "fixie.ai" in note

    def test_returns_at_most_the_limit(self):
        picked = select_outreach_emails(
            ["a@acme.com", "b@acme.com", "c@acme.com"], "acme.com", limit=2)
        assert len(picked) == 2

    def test_matches_across_subdomains_and_www(self):
        picked = select_outreach_emails(["hi@mail.acme.com"], "acme.com")
        assert picked[0][1] is None   # treated as the company's own domain

    def test_warns_when_company_domain_is_unknown(self):
        picked = select_outreach_emails(["hi@somewhere.com"], None)
        assert picked[0][1] is not None

    def test_empty_input_yields_nothing(self):
        assert select_outreach_emails([], "acme.com") == []
        assert select_outreach_emails([], None) == []

    def test_only_one_offdomain_candidate_is_ever_returned(self):
        """Off-domain is a guess — don't multiply the chance of a wrong send."""
        picked = select_outreach_emails(
            ["a@other.com", "b@other.com", "c@other.com"], "acme.com")
        assert len(picked) == 1
