"""Regressions for the measured scraping fixes.

Each case here corresponds to a defect found by replaying a 30-site capture
through the real extractor, and each is written so that reverting the fix
fails the test. The numbers in the docstrings are what that corpus measured;
see docs/decisions.md for the hypotheses that were refuted instead.
"""
import pytest

from web_scraper import _Fetched, WebScraper
from contact_verify import clear_mx_cache, domain_has_mx
from enrichment import (
    _is_valid_outreach_email,
    discover_internal_links,
    extract_contact_candidates,
    extract_emails_from_html,
    rank_sitemap_pages,
)


class TestMachineGeneratedAddresses:
    """270 of 444 contacts corpus-wide were error-tracker DSNs, not people."""

    @pytest.mark.parametrize("address", [
        "f172c25063bf4e3492ece32b840ab90b@o415358.ingest.us.sentry.io",
        "586020d86f05489db63a554690acf1e9@o1398359.ingest.sentry.io",
        "02746a65b3e745ba983cb91f83688cbc@errors.stripe.com",
    ])
    def test_error_tracker_dsn_is_not_a_contact(self, address):
        assert _is_valid_outreach_email(address) is False
        assert extract_emails_from_html(f"<p>{address}</p>") == []

    def test_sentry_subdomain_is_rejected_not_just_the_apex(self):
        # _BAD_DOMAINS held "sentry.io" but matched exactly, while every real
        # ingest host is a subdomain — so the filter never fired.
        assert _is_valid_outreach_email("a@sentry.io") is False
        assert _is_valid_outreach_email("a@o415358.ingest.us.sentry.io") is False

    def test_a_real_person_at_a_flagged_parent_domain_survives(self):
        # errors.stripe.com is junk; stripe.com is a real employer.
        assert _is_valid_outreach_email("jane.diaz@stripe.com") is True

    @pytest.mark.parametrize("local", ["deadbeef", "abc123", "cafe"])
    def test_short_hex_locals_are_still_people(self, local):
        # The rule keys on 16+ hex characters. Real short locals that happen to
        # be hex ("cafe@", "abc123@") must not be swept up with the DSNs.
        assert _is_valid_outreach_email(f"{local}@acme.com") is True


class TestEscapedMarkupArtifacts:
    r"""`>` is alphanumeric, so EMAIL_RE started matching at the `u`."""

    def test_json_escaped_markup_does_not_invent_an_address(self):
        html = r'<script>{"h":"<a href="mailto:kbrooks@wsgr.com">kbrooks@wsgr.com</a>"}</script>'
        found = extract_emails_from_html(html)
        assert found == ["kbrooks@wsgr.com"]
        assert not any(a.startswith("u003e") for a in found)

    def test_escape_decoding_does_not_disturb_ordinary_html(self):
        html = '<a href="mailto:dana@acme.com">Dana</a>'
        assert extract_emails_from_html(html) == ["dana@acme.com"]


class TestObfuscatedAddressesSurviveTagInterleaving:
    """The rejected H2b variant screened raw HTML for `at`/`dot` markers.

    html.parser drops a bogus tag like `</>` *without* splitting the text node,
    so `dana a</>t acme d</>ot com` renders as `dana at acme dot com` — the
    screen said "no marker" and a real contact vanished.
    """

    def test_bogus_tag_inside_an_obfuscated_address(self):
        html = "<p>Dana Whitfield, CEO. Email dana a</>t acme d</>ot com</p>"
        assert extract_emails_from_html(html) == ["dana@acme.com"]
        contacts = extract_contact_candidates(
            [{"url": "https://acme.com/team", "html": html}], "acme.com")
        assert [(c["email"], c["name"]) for c in contacts] == [
            ("dana@acme.com", "Dana Whitfield")]

    def test_marker_split_across_real_tags(self):
        html = "<p>Bob Lee, CTO. bob <b>at</b> acme <b>dot</b> com</p>"
        assert extract_emails_from_html(html) == ["bob@acme.com"]


class TestPageParseCacheIsTransparent:
    """A crawl refreshes contacts up to 4x, reparsing every page each time."""

    def _pages(self):
        return [
            {"url": "https://acme.com/team",
             "html": '<div><h3>Ada Byron</h3><p>CTO</p>'
                     '<a href="mailto:ada@acme.com">ada@acme.com</a></div>'},
            {"url": "https://acme.com/about",
             "html": '<div><h3>Grace Hopper</h3><p>CEO</p>'
                     '<a href="mailto:grace@acme.com">grace@acme.com</a></div>'},
        ]

    def _fingerprint(self, contacts):
        return sorted((c["email"], c["name"], c["role"], c["seniority_rank"],
                       c["school_match"], c["name_from_email"])
                      for c in contacts)

    def test_cached_and_uncached_runs_agree(self):
        pages = self._pages()
        uncached = extract_contact_candidates(pages, "acme.com")
        cache = {}
        first = extract_contact_candidates(pages, "acme.com", cache=cache)
        second = extract_contact_candidates(pages, "acme.com", cache=cache)
        assert self._fingerprint(first) == self._fingerprint(uncached)
        assert self._fingerprint(second) == self._fingerprint(uncached)

    def test_repeated_calls_do_not_rebuild_the_page_trees(self, monkeypatch):
        import enrichment

        calls = []
        real = enrichment.BeautifulSoup
        monkeypatch.setattr(
            enrichment, "BeautifulSoup",
            lambda *a, **kw: (calls.append(1), real(*a, **kw))[1])

        pages = self._pages()
        cache = {}
        extract_contact_candidates(pages, "acme.com", cache=cache)
        first_pass = len(calls)
        assert len(cache) == len(pages)

        calls.clear()
        extract_contact_candidates(pages, "acme.com", cache=cache)
        second_pass = len(calls)

        # Whole-document parses are the expensive ones and must not recur; the
        # small per-sibling trees are rebuilt, which measurement showed is worth
        # 1.3-2.1x across a crawl's four refresh passes even so.
        assert len(cache) == len(pages), "second pass added cache entries"
        assert second_pass < first_pass

    def test_changed_html_at_the_same_url_is_not_served_stale(self):
        cache = {}
        url = "https://acme.com/team"
        before = extract_contact_candidates(
            [{"url": url, "html": '<a href="mailto:ada@acme.com">Ada Byron</a>'}],
            "acme.com", cache=cache)
        after = extract_contact_candidates(
            [{"url": url, "html": '<a href="mailto:zoe@acme.com">Zoe Quinn</a>'}],
            "acme.com", cache=cache)
        assert [c["email"] for c in before] == ["ada@acme.com"]
        assert [c["email"] for c in after] == ["zoe@acme.com"]


class TestSitemapPeopleRanking:
    """20/30 corpus sites publish a sitemap; segment-exact matching is why it
    is usable — substring matching selected val.town/u/fuckyouscratchteam."""

    def test_keeps_locale_prefixed_bio_pages(self):
        urls = [
            "https://www.wsgr.com/en/people/holly-hafford.html",
            "https://www.wsgr.com/en/services/practice-areas/litigation.html",
        ]
        assert rank_sitemap_pages(urls, "wsgr.com") == [
            "https://www.wsgr.com/en/people/holly-hafford.html"]

    @pytest.mark.parametrize("url", [
        "https://www.val.town/u/fuckyouscratchteam",
        "https://linear.app/changelog/2026-06-04-team-documents",
        "https://vercel.com/changelog/improved-hard-caps-for-spend-management",
        "https://www.airtable.com/solutions/project-management",
    ])
    def test_rejects_urls_that_merely_contain_the_word(self, url):
        from enrichment import registered_domain
        assert rank_sitemap_pages([url], registered_domain(url)) == []

    def test_index_pages_outrank_the_bios_beneath_them(self):
        urls = [
            "https://www.cooley.com/about/geographies/asia/people",
            "https://www.cooley.com/people",
        ]
        assert rank_sitemap_pages(urls, "cooley.com")[0] == \
            "https://www.cooley.com/people"

    def test_off_domain_and_policy_urls_are_dropped(self):
        urls = [
            "https://cdn.example.com/people",
            "https://acme.com/legal/team",
            "https://acme.com/team",
        ]
        assert rank_sitemap_pages(urls, "acme.com") == ["https://acme.com/team"]


class TestSitemapFetching:
    """The fetch half of H7: one request, an index followed one level, and the
    SSRF and same-origin guards still on their production path."""

    @pytest.fixture(autouse=True)
    def _public(self, monkeypatch):
        monkeypatch.setattr("web_scraper.is_safe_public_url",
                            lambda u: u.startswith("https://acme.com"))

    def _scraper(self, monkeypatch, docs):
        """docs: {url -> xml string}. Anything absent 404s."""
        scraper = WebScraper()
        asked = []

        def fake_get(url, *a, **kw):
            asked.append(url)
            if url not in docs:
                return _Fetched(status_code=404, headers={}, content=b"",
                                final_url=url)
            return _Fetched(status_code=200,
                            headers={"Content-Type": "application/xml"},
                            content=docs[url].encode(), final_url=url)

        monkeypatch.setattr(scraper, "_safe_get", fake_get)
        monkeypatch.setattr(scraper, "_rate_limit", lambda url: None)
        return scraper, asked

    def _urlset(self, *urls):
        locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return f'<?xml version="1.0"?><urlset>{locs}</urlset>'

    def test_reads_page_urls_from_a_flat_sitemap(self, monkeypatch):
        scraper, asked = self._scraper(monkeypatch, {
            "https://acme.com/sitemap.xml": self._urlset(
                "https://acme.com/people", "https://acme.com/pricing"),
        })
        assert scraper.fetch_sitemap_urls("https://acme.com/") == [
            "https://acme.com/people", "https://acme.com/pricing"]
        assert asked == ["https://acme.com/sitemap.xml"]

    def test_missing_sitemap_is_not_an_error(self, monkeypatch):
        scraper, _ = self._scraper(monkeypatch, {})
        assert scraper.fetch_sitemap_urls("https://acme.com/") == []

    def test_a_document_that_is_not_a_sitemap_yields_nothing(self, monkeypatch):
        # Many sites serve their SPA shell for any unknown path.
        scraper, _ = self._scraper(monkeypatch, {
            "https://acme.com/sitemap.xml": "<html><body>Not found</body></html>",
        })
        assert scraper.fetch_sitemap_urls("https://acme.com/") == []

    def test_follows_an_index_one_level(self, monkeypatch):
        scraper, asked = self._scraper(monkeypatch, {
            "https://acme.com/sitemap.xml": self._urlset(
                "https://acme.com/sitemap-1.xml"),
            "https://acme.com/sitemap-1.xml": self._urlset(
                "https://acme.com/en/people/ada.html"),
        })
        assert scraper.fetch_sitemap_urls("https://acme.com/") == [
            "https://acme.com/en/people/ada.html"]
        assert len(asked) == 2

    def test_index_children_are_capped(self, monkeypatch):
        import web_scraper

        kids = [f"https://acme.com/sm-{i}.xml" for i in range(10)]
        docs = {"https://acme.com/sitemap.xml": self._urlset(*kids)}
        for i, k in enumerate(kids):
            docs[k] = self._urlset(f"https://acme.com/p{i}")
        scraper, asked = self._scraper(monkeypatch, docs)
        found = scraper.fetch_sitemap_urls("https://acme.com/")
        assert len(found) == web_scraper.SITEMAP_MAX_INDEX_CHILDREN
        assert len(asked) == 1 + web_scraper.SITEMAP_MAX_INDEX_CHILDREN

    def test_off_origin_entries_are_dropped(self, monkeypatch):
        # A sitemap is attacker-influenced input; it must not redirect the
        # crawl onto another host, and the SSRF guard stays on its real path.
        scraper, _ = self._scraper(monkeypatch, {
            "https://acme.com/sitemap.xml": self._urlset(
                "https://evil.com/people",
                "http://169.254.169.254/latest/meta-data",
                "https://acme.com/people"),
        })
        assert scraper.fetch_sitemap_urls("https://acme.com/") == [
            "https://acme.com/people"]

    def test_limit_is_honoured(self, monkeypatch):
        scraper, _ = self._scraper(monkeypatch, {
            "https://acme.com/sitemap.xml": self._urlset(
                *[f"https://acme.com/p{i}" for i in range(50)]),
        })
        assert len(scraper.fetch_sitemap_urls("https://acme.com/", limit=7)) == 7

    def test_entities_in_locs_are_decoded(self, monkeypatch):
        scraper, _ = self._scraper(monkeypatch, {
            "https://acme.com/sitemap.xml":
                "<urlset><url><loc>https://acme.com/p?a=1&amp;b=2</loc>"
                "</url></urlset>",
        })
        assert scraper.fetch_sitemap_urls("https://acme.com/") == [
            "https://acme.com/p?a=1&b=2"]


class TestSitemapSeedsTheCrawl:
    """The integration half of H7: a people URL only the sitemap knows about
    must actually get crawled, and ahead of the blind /team, /people guesses."""

    def _service(self, monkeypatch, pages, sitemap):
        import enrichment

        service = enrichment.EnrichmentService()
        fetched = []

        def fake_fetch_html(url):
            fetched.append(url)
            return pages.get(url.rstrip("/"))

        monkeypatch.setattr(service.scraper, "fetch_html", fake_fetch_html)
        monkeypatch.setattr(service.scraper, "fetch_sitemap_urls",
                            lambda base, **kw: list(sitemap))
        monkeypatch.setattr(service.scraper, "extract_text",
                            lambda html: "Acme Inc makes things. " * 12)
        monkeypatch.setattr(enrichment, "llm_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(
            enrichment, "enrich_contacts_outreach",
            lambda contacts, **kw: [
                enrichment.annotate_contact(c, check_mx=False)
                for c in (contacts or [])])
        # No DNS from a unit test; the MX cache is process-wide, so clear it.
        import contact_verify
        monkeypatch.setattr(contact_verify, "_resolve_mx", lambda d, t: True)
        clear_mx_cache()
        return service, fetched

    def test_a_sitemap_only_people_page_is_crawled(self, monkeypatch):
        bio = ('<html><body><h3>Ada Byron</h3><p>Chief Technology Officer</p>'
               '<a href="mailto:ada@acme.com">ada@acme.com</a>'
               '<p>Acme Inc builds things.</p></body></html>')
        service, fetched = self._service(
            monkeypatch,
            pages={"https://acme.com": "<html><body>Acme Inc</body></html>",
                   "https://acme.com/en/people/ada.html": bio},
            sitemap=["https://acme.com/en/people/ada.html"])

        result = service.enrich("Acme Inc", url="https://acme.com")

        assert "https://acme.com/en/people/ada.html" in fetched
        assert "ada@acme.com" in [c.get("email") for c in result["contacts"]]

    def test_sitemap_pages_are_tried_before_the_blind_guesses(self, monkeypatch):
        import enrichment

        # The prefetcher pulls the head of the queue across three threads, so
        # fetch order only reflects crawl order with it switched off.
        monkeypatch.setattr(enrichment, "PREFETCH_WORKERS", 1)
        service, fetched = self._service(
            monkeypatch,
            pages={"https://acme.com": "<html><body>Acme Inc</body></html>"},
            sitemap=["https://acme.com/en/people/ada.html"])

        service.enrich("Acme Inc", url="https://acme.com")

        seeded = fetched.index("https://acme.com/en/people/ada.html")
        guessed = fetched.index("https://acme.com/about")
        assert seeded < guessed

    def test_it_costs_one_request_and_only_when_guessing_starts(self, monkeypatch):
        calls = []
        linked = ('<html><body>Acme Inc'
                  '<a href="/about">About</a><a href="/team">Team</a>'
                  '</body></html>')
        service, _ = self._service(
            monkeypatch,
            pages={"https://acme.com": linked,
                   "https://acme.com/about": "<html>Acme Inc about</html>",
                   "https://acme.com/team": "<html>Acme Inc team</html>"},
            sitemap=["https://acme.com/en/people/ada.html"])
        monkeypatch.setattr(service.scraper, "fetch_sitemap_urls",
                            lambda base, **kw: (calls.append(base), [])[1])

        service.enrich("Acme Inc", url="https://acme.com")
        assert len(calls) <= 1

    def test_fast_mode_never_pays_for_it(self, monkeypatch):
        calls = []
        service, _ = self._service(
            monkeypatch,
            pages={"https://acme.com": "<html><body>Acme Inc</body></html>"},
            sitemap=["https://acme.com/en/people/ada.html"])
        monkeypatch.setattr(service.scraper, "fetch_sitemap_urls",
                            lambda base, **kw: (calls.append(base), [])[1])

        service.enrich("Acme Inc", url="https://acme.com", mode="fast")
        assert calls == []

    def test_a_failing_sitemap_does_not_break_the_crawl(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("sitemap exploded")

        service, fetched = self._service(
            monkeypatch,
            pages={"https://acme.com": "<html><body>Acme Inc</body></html>"},
            sitemap=[])
        monkeypatch.setattr(service.scraper, "fetch_sitemap_urls", boom)

        result = service.enrich("Acme Inc", url="https://acme.com")
        assert result["pages_scraped"] >= 1


class TestLinkVocabularyUnchanged:
    """H8 was refuted: adding people vocabulary to link discovery bought one
    real page and four pieces of noise across 30 sites. These pin the
    behaviour that measurement said to keep."""

    def test_marketing_pages_are_not_treated_as_people_pages(self):
        html = """
          <a href="/industries/professional-services">Professional Services</a>
          <a href="/partners/solution-partners/10up">10up</a>
          <a href="/practice-areas/litigation/board-and-internal-investigations">B</a>
        """
        assert discover_internal_links(html, "https://acme.com/", "acme.com") == []

    def test_real_people_paths_still_resolve(self):
        html = '<a href="/en/people/index.html">Our People</a>'
        assert discover_internal_links(html, "https://acme.com/", "acme.com") == [
            "https://acme.com/en/people/index.html"]


class TestDeadApiProbeCache:
    """discover_api_endpoints re-derives the same 8 guessed /api/... paths on
    every JS-shell page, so a 40-page SPA paid the same 8 404s forty times."""

    @pytest.fixture(autouse=True)
    def _public_acme(self, monkeypatch):
        monkeypatch.setattr("web_scraper.is_safe_public_url",
                            lambda u: u.startswith("https://"))

    def _scraper(self, monkeypatch, responses):
        """responses: {url -> status}. Records every URL actually requested."""
        scraper = WebScraper()
        asked = []

        def fake_get(url, *a, **kw):
            asked.append(url)
            status = responses.get(url, 404)
            body = b'{"people":[]}' if status == 200 else b""
            return _Fetched(status_code=status,
                            headers={"Content-Type": "application/json"},
                            content=body, final_url=url)

        monkeypatch.setattr(scraper, "_safe_get", fake_get)
        monkeypatch.setattr(scraper, "_rate_limit", lambda url: None)
        return scraper, asked

    def test_a_404_is_asked_once_per_crawl(self, monkeypatch):
        scraper, asked = self._scraper(monkeypatch, {})
        for _ in range(10):
            assert scraper.fetch_json("https://acme.com/api/team") is None
        assert asked == ["https://acme.com/api/team"]

    def test_reset_makes_the_next_crawl_ask_again(self, monkeypatch):
        # The cache must not outlive one crawl: EnrichmentService is a
        # module-level singleton, so a persistent cache would let one company's
        # verdicts suppress another's endpoints.
        scraper, asked = self._scraper(monkeypatch, {})
        scraper.fetch_json("https://acme.com/api/team")
        scraper.reset_dead_api_probes()
        scraper.fetch_json("https://acme.com/api/team")
        assert len(asked) == 2

    def test_trailing_slash_is_a_different_endpoint(self, monkeypatch):
        # The rejected variant keyed on the path with "/" stripped, so a 404 on
        # /api/team silently suppressed a live /api/team/.
        scraper, asked = self._scraper(
            monkeypatch, {"https://acme.com/api/team/": 200})
        assert scraper.fetch_json("https://acme.com/api/team") is None
        assert scraper.fetch_json("https://acme.com/api/team/") == {"people": []}
        assert asked == ["https://acme.com/api/team",
                         "https://acme.com/api/team/"]

    @pytest.mark.parametrize("status", [403, 429, 500, 503])
    def test_transient_refusals_are_retried(self, monkeypatch, status):
        # Only 404/410 mean "not here". Everything else is a state a server
        # leaves, and blacklisting a live endpoint costs real contacts.
        scraper, asked = self._scraper(
            monkeypatch, {"https://acme.com/api/team": status})
        for _ in range(3):
            scraper.fetch_json("https://acme.com/api/team")
        assert len(asked) == 3

    def test_a_404_on_one_origin_does_not_speak_for_another(self, monkeypatch):
        scraper, asked = self._scraper(
            monkeypatch, {"https://beta.com/api/team": 200})
        assert scraper.fetch_json("https://acme.com/api/team") is None
        assert scraper.fetch_json("https://beta.com/api/team") == {"people": []}
        assert len(asked) == 2

    def test_a_live_endpoint_is_never_cached(self, monkeypatch):
        scraper, asked = self._scraper(
            monkeypatch, {"https://acme.com/api/team": 200})
        for _ in range(3):
            assert scraper.fetch_json("https://acme.com/api/team") == {"people": []}
        assert len(asked) == 3


class TestMxMemoization:
    """275 lookups for 4 distinct domains on one crawl, before this."""

    def setup_method(self):
        clear_mx_cache()

    def teardown_method(self):
        clear_mx_cache()

    def test_repeat_lookups_hit_the_cache(self, monkeypatch):
        import contact_verify

        calls = []
        monkeypatch.setattr(contact_verify, "_resolve_mx",
                            lambda d, t: (calls.append(d), True)[1])
        for _ in range(20):
            assert domain_has_mx("acme.com") is True
        assert calls == ["acme.com"]

    def test_unknown_answers_are_not_cached_for_long(self, monkeypatch):
        import contact_verify

        # A timeout is transient; freezing it for the full TTL would strand a
        # recoverable failure for the rest of the run.
        assert contact_verify._MX_CACHE_TTL_UNKNOWN < contact_verify._MX_CACHE_TTL

        # Store the "unknown" with an already-expired stamp, then let the
        # domain recover: the next call must go back to the resolver.
        monkeypatch.setattr(contact_verify, "_MX_CACHE_TTL_UNKNOWN", -1.0)
        monkeypatch.setattr(contact_verify, "_resolve_mx", lambda d, t: None)
        assert domain_has_mx("flaky.com") is None

        calls = []
        monkeypatch.setattr(contact_verify, "_resolve_mx",
                            lambda d, t: (calls.append(d), True)[1])
        assert domain_has_mx("flaky.com") is True
        assert calls == ["flaky.com"], "expired unknown was not re-resolved"

    def test_distinct_domains_are_resolved_separately(self, monkeypatch):
        import contact_verify

        monkeypatch.setattr(contact_verify, "_resolve_mx",
                            lambda d, t: d == "good.com")
        assert domain_has_mx("good.com") is True
        assert domain_has_mx("bad.com") is False
