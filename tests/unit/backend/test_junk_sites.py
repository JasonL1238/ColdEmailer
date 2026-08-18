"""Website identification must reject parked pages, aggregators, and content
farms — scraping one of those yields nonsense and any address on it belongs to
a completely different company."""
import pytest

from discovery import is_junk_site


class TestIsJunkSite:
    @pytest.mark.parametrize("url", [
        "https://forsale.dynadot.com/example",
        "https://www.hugedomains.com/domain_profile.cfm?d=acme",
        "https://dan.com/buy-domain/acme.com",
        "https://sedo.com/search/details/?domain=acme.com",
        "https://www.namecheap.com/domains/registration/",
    ])
    def test_rejects_domain_parking_and_for_sale_pages(self, url):
        assert is_junk_site(url) is True

    @pytest.mark.parametrize("url", [
        "https://linkedin.com/company/acme",
        "https://www.crunchbase.com/organization/acme",
        "https://open.spotify.com/artist/x",
        "https://acme.substack.com",
        "https://acme.wordpress.com",
    ])
    def test_rejects_aggregators_and_platforms(self, url):
        assert is_junk_site(url) is True

    def test_rejects_keyword_stuffed_content_farms(self):
        assert is_junk_site("https://www.dailythemedcrosswordanswers.com") is True

    @pytest.mark.parametrize("url", [
        "https://acme.com",
        "https://www.vellum.ai",
        "https://openlayer.com/about",
        "https://fixie.ai",
        "https://some-startup.io",
    ])
    def test_accepts_real_company_sites(self, url):
        assert is_junk_site(url) is False

    def test_handles_empty_input(self):
        assert is_junk_site("") is False
        assert is_junk_site(None) is False

    def test_short_hostnames_are_not_content_farms(self):
        """Guard against the length heuristic eating legitimate long-ish names."""
        assert is_junk_site("https://cloudflarestatus.com") is False


def test_find_website_keeps_domains_that_merely_contain_an_aggregator_name():
    """`x.com` used to be substring-matched, so netflix.com was discarded.

    find_website now filters through discovery.is_junk_site() alone, which
    compares registered domains rather than substrings.
    """
    from discovery import is_junk_site

    for real in ("https://www.netflix.com", "https://matrix.com",
                 "https://equinix.com", "https://citrix.com",
                 "https://phoenix.com"):
        assert is_junk_site(real) is False, real
    for aggregator in ("https://x.com/acme", "https://www.linkedin.com/company/acme",
                       "https://github.com/acme"):
        assert is_junk_site(aggregator) is True, aggregator
