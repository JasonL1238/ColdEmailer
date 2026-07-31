"""High-signal scraping evaluations.

These cases model failure modes seen on real company sites: nonstandard team
paths, public email obfuscation, generic inboxes competing with named leaders,
and Penn State being mistaken for the University of Pennsylvania.
"""
import pytest

from enrichment import (
    EnrichmentService,
    annotate_contact,
    discover_internal_links,
    extract_contact_candidates,
    extract_emails_from_html,
    llm_metadata,
    select_outreach_contacts,
)


def _cloudflare_encode(address: str, key: int = 0x12) -> str:
    return f"{key:02x}" + "".join(f"{ord(char) ^ key:02x}" for char in address)


@pytest.fixture(autouse=True)
def _offline_outreach_enrichment(monkeypatch):
    """Crawl fixtures stay offline — LinkedIn/email API enrichment is unit-tested
    separately in test_contact_enrich.py."""
    import enrichment

    def _passthrough(contacts, **_kwargs):
        return [annotate_contact(c, check_mx=False) for c in (contacts or [])]

    monkeypatch.setattr(enrichment, "enrich_contacts_outreach", _passthrough)


class TestRobustEmailExtraction:
    def test_decodes_common_obfuscation_without_accepting_system_mail(self):
        encoded = _cloudflare_encode("founder@acme.com")
        html = f"""
          <p>Jane can be reached at jane [at] acme [dot] com.</p>
          <a href="mailto:careers@acme.com?subject=Hello">Careers</a>
          <a data-cfemail="{encoded}">protected</a>
          <p>noreply@acme.com privacy@acme.com sprite@2x.png</p>
        """
        assert set(extract_emails_from_html(html)) == {
            "jane@acme.com", "careers@acme.com", "founder@acme.com",
        }

    def test_does_not_turn_ordinary_at_and_dot_prose_into_an_email(self):
        html = "<p>Meet us at noon and end the sentence with a dot today.</p>"
        assert extract_emails_from_html(html) == []


class TestLinkDiscovery:
    def test_follows_the_sites_real_nonstandard_research_pages(self):
        html = """
          <a href="/our-leadership">Our leadership</a>
          <a href="/company/story">Who we are</a>
          <a href="/press/latest-launch">Latest news</a>
          <a href="/legal/privacy">Privacy</a>
          <a href="https://linkedin.com/company/acme">LinkedIn</a>
        """
        links = discover_internal_links(html, "https://acme.com", "acme.com")
        assert links[:3] == [
            "https://acme.com/our-leadership",
            "https://acme.com/company/story",
            "https://acme.com/press/latest-launch",
        ]
        assert not any("privacy" in link or "linkedin" in link for link in links)


class TestAffinityAndSeniorityRanking:
    def test_upenn_ceo_beats_founder_inbox_and_generic_email(self):
        pages = [{
            "url": "https://acme.com/leadership",
            "html": """
              <section>
                <h2>Jane Doe — CEO</h2>
                <p>Jane earned her MBA from the Wharton School at the
                University of Pennsylvania.</p>
                <a href="mailto:jane.doe@acme.com">Email Jane</a>
              </section>
              <p><a href="mailto:founders@acme.com">Founders</a></p>
              <p><a href="mailto:hello@acme.com">Hello</a></p>
            """,
        }]
        emails = extract_emails_from_html(pages[0]["html"])
        candidates = extract_contact_candidates(
            pages, "acme.com", "University of Pennsylvania")
        chosen = select_outreach_contacts(
            candidates, emails, "acme.com", check_mx=False)

        assert chosen[0]["email"] == "jane.doe@acme.com"
        assert chosen[0]["name"] == "Jane Doe"
        assert chosen[0]["role"] == "CEO"
        assert chosen[0]["school_match"] is True
        assert chosen[0]["source_url"] == "https://acme.com/leadership"
        assert chosen[0]["email_person_match"] is True
        # MX skipped in unit tests — verified flag requires a real MX check
        assert chosen[0]["email_verified"] is False

    def test_penn_state_is_not_an_upenn_match(self):
        pages = [{
            "url": "https://acme.com/team",
            "html": """
              <p>John Smith — CEO. He graduated from Penn State University.</p>
              <a href="mailto:john.smith@acme.com">Email</a>
            """,
        }]
        candidates = extract_contact_candidates(
            pages, "acme.com", "University of Pennsylvania")
        assert candidates[0]["school_match"] is False

    def test_never_prefers_an_offdomain_person_over_safe_company_addresses(self):
        """Generics are not person contacts; unmatched off-domain locals are dropped.

        Legacy behavior stored hello@ when it was the only on-domain address.
        Person-only selection refuses company inboxes and first-name-only
        addresses that do not match a full name.
        """
        pages = [{
            "url": "https://acme.com/team",
            "html": """
              <p>Jane Doe — CEO, Wharton MBA.</p>
              <a href="mailto:jane@gmail.com">Email Jane</a>
              <a href="mailto:hello@acme.com">Company contact</a>
            """,
        }]
        emails = extract_emails_from_html(pages[0]["html"])
        candidates = extract_contact_candidates(
            pages, "acme.com", "University of Pennsylvania")
        chosen = select_outreach_contacts(candidates, emails, "acme.com")
        assert chosen == []

    def test_prefers_on_domain_person_email_over_generic_inbox(self):
        pages = [{
            "url": "https://acme.com/team",
            "html": """
              <p>Jane Doe — CEO, Wharton MBA.</p>
              <a href="mailto:jane.doe@acme.com">Email Jane</a>
              <a href="mailto:hello@acme.com">Company contact</a>
            """,
        }]
        emails = extract_emails_from_html(pages[0]["html"])
        candidates = extract_contact_candidates(
            pages, "acme.com", "University of Pennsylvania")
        chosen = select_outreach_contacts(
            candidates, emails, "acme.com", check_mx=False)
        assert chosen[0]["email"] == "jane.doe@acme.com"
        assert chosen[0]["email_person_match"] is True
        assert chosen[0]["email_verified"] is False
        # LinkedIn slug match still counts as person_verified without MX
        assert chosen[0].get("linkedin_verified") in {True, False}

    def test_rejects_linkedin_slug_that_does_not_match_person_name(self):
        pages = [{
            "url": "https://acme.com/leadership",
            "html": """
              <section>
                <h2>Jane Doe — CEO</h2>
                <a href="https://www.linkedin.com/in/john-smith-99">LinkedIn</a>
              </section>
            """,
        }]
        candidates = extract_contact_candidates(pages, "acme.com")
        assert candidates == []

    def test_keeps_linkedin_only_leader_linked_from_company_team_page(self):
        pages = [{
            "url": "https://acme.com/leadership",
            "html": """
              <section>
                <h2>Jane Doe — Chief Technology Officer</h2>
                <p>Jane earned her MBA from Wharton and previously worked at Stripe.</p>
                <a href="https://www.linkedin.com/in/jane-doe">LinkedIn</a>
              </section>
            """,
        }]
        candidates = extract_contact_candidates(
            pages, "acme.com", "University of Pennsylvania", "Stripe\nGoogle")
        chosen = select_outreach_contacts(
            candidates, [], "acme.com", check_mx=False)

        assert len(chosen) == 1
        assert chosen[0]["email"] == ""
        assert chosen[0]["linkedin_url"] == "https://www.linkedin.com/in/jane-doe"
        assert chosen[0]["name"] == "Jane Doe"
        assert chosen[0]["role"] == "CTO"
        assert chosen[0]["school_match"] is True
        assert chosen[0]["linkedin_verified"] is True
        assert "Shared: Stripe" in chosen[0]["affinity"]

    def test_ignores_company_and_non_linkedin_profile_links(self):
        pages = [{
            "url": "https://acme.com/team",
            "html": """
              <h2>Jane Doe — CEO</h2>
              <a href="https://www.linkedin.com/company/acme">Company LinkedIn</a>
              <a href="https://evil.example/in/jane-doe">Profile</a>
            """,
        }]
        candidates = extract_contact_candidates(pages, "acme.com")
        assert candidates == []


class TestGroundedAIContactAssociation:
    def test_accepts_only_scraped_addresses_with_exact_biography_evidence(
            self, monkeypatch):
        import enrichment

        text = (
            "SOURCE: https://acme.com/team\n"
            "Jane Doe is Acme's CEO and earned her MBA from the Wharton School."
        )
        monkeypatch.setattr(enrichment, "get_cloud_llm_provider", lambda: "test")
        monkeypatch.setattr(enrichment, "complete_json", lambda *_args, **_kwargs: {
            "summary": "Acme builds robotics systems.",
            "contacts": [
                {
                    "email": "jane@acme.com",
                    "name": "Jane Doe",
                    "role": "CEO",
                    "source_url": "https://acme.com/team",
                    "evidence": (
                        "Jane Doe is Acme's CEO and earned her MBA from "
                        "the Wharton School."
                    ),
                },
                {
                    "email": "invented@acme.com",
                    "name": "Made Up",
                    "role": "CEO",
                    "source_url": "https://acme.com/team",
                    "evidence": "This exact sentence is not present in the source page.",
                },
            ],
        })

        result = llm_metadata(
            "Acme", text, ["jane@acme.com"], "University of Pennsylvania")
        assert [c["email"] for c in result["_contacts"]] == ["jane@acme.com"]
        assert result["_contacts"][0]["school_match"] is True


class _FixtureScraper:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []
        self.min_delay = 2

    def fetch_html(self, url):
        self.requested.append(url)
        return self.pages.get(url.rstrip("/"))

    @staticmethod
    def extract_text(html):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


class TestPagePrefetch:
    """Prefetching is a latency trick only — crawl order and results must not move."""

    PAGES = {
        "https://acme.com": """
          <html><body><h1>Acme</h1>
          <p>Acme builds reliable robotics systems for industrial teams
          that need safer and more efficient warehouse operations.</p>
          <a href="/company/our-people">Leadership</a>
          <a href="/press/product-launch">News</a>
          </body></html>
        """,
        "https://acme.com/company/our-people": """
          <html><body><h1>Acme leadership</h1>
          <p>Jane Doe — CEO of Acme.</p>
          <a href="mailto:jane.doe@acme.com">Email Jane</a>
          </body></html>
        """,
        "https://acme.com/press/product-launch": """
          <html><body><h1>Acme launches Atlas</h1>
          <p>Acme launched Atlas, a warehouse robotics platform, in March 2026.</p>
          </body></html>
        """,
    }

    def _crawl(self, monkeypatch, workers):
        import enrichment

        monkeypatch.setattr(enrichment, "PREFETCH_WORKERS", workers)
        monkeypatch.setattr(enrichment, "llm_metadata", lambda *_a: None)
        service = EnrichmentService()
        fixture = _FixtureScraper(self.PAGES)
        service.scraper = fixture
        monkeypatch.setattr(service, "_school_research_pages",
                            lambda *_a, **_k: [])
        result = service.enrich("Acme", "https://acme.com", mode="full")
        return result, fixture

    def test_prefetching_does_not_change_the_crawl(self, monkeypatch):
        serial, serial_scraper = self._crawl(monkeypatch, 1)
        parallel, parallel_scraper = self._crawl(monkeypatch, 3)

        assert serial["research_sources"] == parallel["research_sources"]
        assert serial["pages_scraped"] == parallel["pages_scraped"]
        assert ([c["email"] for c in serial["contacts"]]
                == [c["email"] for c in parallel["contacts"]])
        # Warming may fetch a page the loop never reaches, but never twice.
        assert len(parallel_scraper.requested) == len(set(parallel_scraper.requested))
        assert set(serial_scraper.requested) <= set(parallel_scraper.requested)

    def test_get_falls_back_to_a_direct_fetch_on_a_miss(self):
        from enrichment import _PagePrefetcher

        fixture = _FixtureScraper(self.PAGES)
        prefetch = _PagePrefetcher(fixture, workers=2)
        try:
            html = prefetch.get("https://acme.com")
        finally:
            prefetch.close()
        assert html is not None and "Acme" in html
        assert fixture.requested == ["https://acme.com"]

    def test_warmed_page_is_not_fetched_again(self):
        from enrichment import _PagePrefetcher

        fixture = _FixtureScraper(self.PAGES)
        prefetch = _PagePrefetcher(fixture, workers=2)
        try:
            prefetch.submit(["https://acme.com"])
            html = prefetch.get("https://acme.com")
        finally:
            prefetch.close()
        assert html is not None
        assert fixture.requested == ["https://acme.com"]

    def test_a_failing_page_is_reported_as_a_miss_not_an_exception(self):
        from enrichment import _PagePrefetcher

        class _Exploding:
            def fetch_html(self, url):
                raise RuntimeError("network on fire")

        prefetch = _PagePrefetcher(_Exploding(), workers=2)
        try:
            prefetch.submit(["https://acme.com"])
            assert prefetch.get("https://acme.com") is None
        finally:
            prefetch.close()

    def test_close_is_safe_with_work_still_queued(self):
        from enrichment import _PagePrefetcher

        fixture = _FixtureScraper(self.PAGES)
        prefetch = _PagePrefetcher(fixture, workers=2)
        prefetch.submit(list(self.PAGES))
        prefetch.close()
        prefetch.close()  # idempotent


class TestEndToEndCompanyCrawl:
    def test_crawls_discovered_pages_and_reports_auditable_coverage(self, monkeypatch):
        import enrichment

        pages = {
            "https://acme.com": """
              <html><body><h1>Acme</h1>
              <p>Acme builds reliable robotics systems for industrial teams
              that need safer and more efficient warehouse operations.</p>
              <a href="/company/our-people">Leadership</a>
              <a href="/press/product-launch">News</a>
              </body></html>
            """,
            "https://acme.com/company/our-people": """
              <html><body><h1>Acme leadership</h1>
              <p>Jane Doe — CEO. Jane studied at the University of Pennsylvania.</p>
              <a href="mailto:jane.doe@acme.com">Email Jane</a>
              </body></html>
            """,
            "https://acme.com/press/product-launch": """
              <html><body><h1>Acme launches Atlas</h1>
              <p>Acme launched Atlas, a new warehouse robotics platform for
              safer material handling, in March 2026.</p>
              </body></html>
            """,
        }
        service = EnrichmentService()
        fixture = _FixtureScraper(pages)
        service.scraper = fixture
        monkeypatch.setattr(service, "_school_research_pages",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(enrichment, "llm_metadata", lambda *_args: None)

        result = service.enrich(
            "Acme", "https://acme.com",
            preferred_school="University of Pennsylvania",
            mode="full")

        assert result["identity_verified"] is True
        assert result["ok"] is True
        assert result["pages_scraped"] == 3
        assert result["pages_attempted"] >= 3
        assert result["research_quality"] in {"medium", "high"}
        assert result["research_sources"] == [
            "https://acme.com",
            "https://acme.com/company/our-people",
            "https://acme.com/press/product-launch",
        ]
        assert result["contacts"][0]["email"] == "jane.doe@acme.com"
        assert result["contacts"][0]["school_match"] is True

    def test_fast_mode_stops_once_about_and_person_contact_exist(self, monkeypatch):
        import enrichment

        pages = {
            "https://acme.com": """
              <html><body><h1>Acme</h1>
              <p>Acme builds reliable robotics systems for industrial teams
              that need safer and more efficient warehouse operations.</p>
              <a href="/company/our-people">Leadership</a>
              <a href="/press/product-launch">News</a>
              </body></html>
            """,
            "https://acme.com/company/our-people": """
              <html><body><h1>Acme leadership</h1>
              <p>Jane Doe — CEO. Jane studied at the University of Pennsylvania.</p>
              <a href="mailto:jane.doe@acme.com">Email Jane</a>
              <a href="https://www.linkedin.com/in/jane-doe">LinkedIn</a>
              </body></html>
            """,
            "https://acme.com/press/product-launch": """
              <html><body><h1>Acme launches Atlas</h1>
              <p>Acme launched Atlas, a new warehouse robotics platform.</p>
              </body></html>
            """,
        }
        service = EnrichmentService()
        fixture = _FixtureScraper(pages)
        service.scraper = fixture
        monkeypatch.setattr(service, "_school_research_pages",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(enrichment, "llm_metadata", lambda *_args: None)

        result = service.enrich(
            "Acme", "https://acme.com",
            preferred_school="University of Pennsylvania",
            mode="fast")

        assert result["enrich_mode"] == "fast"
        assert result["identity_verified"] is True
        assert result["ok"] is True
        assert result["pages_scraped"] <= 2
        assert "https://acme.com/press/product-launch" not in result["research_sources"]
        person = result["contacts"][0]
        assert person["email"] == "jane.doe@acme.com"
        assert person["email_person_match"] is True
        assert person["linkedin_verified"] is True
        assert person["person_verified"] is True
        assert result["elapsed_sec"] < 60

    def test_fast_mode_accepts_homepage_as_about_when_no_team_links(self, monkeypatch):
        """Homepage-only sites should not burn the deadline on /about 404s."""
        import enrichment

        pages = {
            "https://acme.com": """
              <html><body><h1>Acme</h1>
              <p>Acme builds reliable robotics systems for industrial teams
              that need safer and more efficient warehouse operations every day.</p>
              <p>Jane Doe — CEO</p>
              <a href="mailto:jane.doe@acme.com">Email Jane</a>
              <a href="https://www.linkedin.com/in/jane-doe">LinkedIn</a>
              </body></html>
            """,
        }
        service = EnrichmentService()
        fixture = _FixtureScraper(pages)
        service.scraper = fixture
        monkeypatch.setattr(service, "_school_research_pages",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(enrichment, "llm_metadata", lambda *_args: None)

        result = service.enrich("Acme", "https://acme.com", mode="fast")
        assert result["ok"] is True
        assert result["elapsed_sec"] < 60
        assert any(c.get("linkedin_verified") for c in result["contacts"])
        # Must not keep probing missing /about forever
        assert result["pages_attempted"] <= 4

    def test_fast_mode_does_not_verify_generic_inbox_on_homepage(self, monkeypatch):
        import enrichment

        pages = {
            "https://acme.com": """
              <html><body><h1>Acme</h1>
              <p>Acme builds reliable robotics systems for industrial teams
              that need safer and more efficient warehouse operations.</p>
              <a href="mailto:hello@acme.com">Email</a>
              </body></html>
            """,
        }
        service = EnrichmentService()
        fixture = _FixtureScraper(pages)
        service.scraper = fixture
        monkeypatch.setattr(service, "_school_research_pages",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(enrichment, "llm_metadata", lambda *_args: None)
        monkeypatch.setattr(service.scraper, "fetch_html",
                            lambda url: pages.get(url.rstrip("/")))

        result = service.enrich("Acme", "https://acme.com", mode="fast")
        assert result["pages_scraped"] >= 1
        assert not any(c.get("person_verified") for c in result.get("contacts") or [])
        assert not any(
            c.get("email_kind") == "personal" for c in result.get("contacts") or [])

    def test_fast_mode_early_exits_on_verified_linkedin(self, monkeypatch):
        """Verified LinkedIn is enough for fast success (equal to person email)."""
        import enrichment

        pages = {
            "https://acme.com": """
              <html><body><h1>Acme</h1>
              <p>Acme builds reliable robotics systems for industrial teams
              that need safer and more efficient warehouse operations every day.</p>
              <a href="/team">Team</a>
              <a href="/press">News</a>
              </body></html>
            """,
            "https://acme.com/team": """
              <html><body><h1>Team</h1>
              <section>
                <h2>Jane Doe — Chief Technology Officer</h2>
                <p>Jane previously led engineering at Stripe.</p>
                <a href="https://www.linkedin.com/in/jane-doe">LinkedIn</a>
              </section>
              </body></html>
            """,
            "https://acme.com/press": """
              <html><body><h1>News</h1>
              <p>Acme shipped Atlas for warehouse teams this quarter.</p>
              </body></html>
            """,
        }
        service = EnrichmentService()
        fixture = _FixtureScraper(pages)
        service.scraper = fixture
        monkeypatch.setattr(service, "_school_research_pages",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(enrichment, "llm_metadata", lambda *_args: None)

        result = service.enrich("Acme", "https://acme.com", mode="fast")
        assert any(c.get("linkedin_verified") for c in result.get("contacts") or [])
        assert "https://acme.com/press" not in result["research_sources"]
        assert result["elapsed_sec"] < 60
