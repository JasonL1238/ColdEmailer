"""Layered scraper: API discovery → HTTPX HTML → Playwright gate."""
import json

import pytest

from web_scraper import (
    WebScraper,
    discover_api_endpoints,
    json_to_research_html,
    _looks_like_js_shell,
    _json_payload_useful,
    _json_has_people,
    is_safe_public_url,
)


class TestApiDiscovery:
    def test_extracts_fetch_and_api_paths_same_origin_only(self):
        html = """
        <html><body>
          <script>
            fetch('/api/team');
            axios.get('https://evil.example/api/secrets');
            fetch('https://acme.com/api/people?active=1');
            const x = '/static/app.js';
          </script>
        </body></html>
        """
        urls = discover_api_endpoints(html, "https://acme.com/about",
                                      probe_common=False)
        assert "https://acme.com/api/team" in urls
        assert "https://acme.com/api/people?active=1" in urls
        assert not any("evil.example" in u for u in urls)
        assert not any(u.endswith(".js") for u in urls)

    def test_reads_next_data_blob_urls(self):
        html = """
        <script id="__NEXT_DATA__" type="application/json">
          {"props":{"pageProps":{"dataUrl":"/api/v1/team"}}}
        </script>
        """
        urls = discover_api_endpoints(html, "https://acme.com/", probe_common=False)
        assert "https://acme.com/api/v1/team" in urls

    def test_json_to_research_html_is_email_scannable(self):
        from enrichment import extract_emails_from_html
        chunk = json_to_research_html(
            "https://acme.com/api/team",
            {"members": [{"name": "Jane Doe", "email": "jane.doe@acme.com"}]},
        )
        assert "jane.doe@acme.com" in extract_emails_from_html(chunk)
        assert "api-source" in chunk


class TestJsShellDetection:
    def test_spa_shell_with_almost_no_text(self):
        html = '<div id="__next"></div><script>' + ("x" * 5000) + "</script>"
        assert _looks_like_js_shell(html, "Loading") is True

    def test_normal_about_page_is_not_a_shell(self):
        text = ("Acme builds warehouse robotics for industrial teams that need "
                "safer fulfillment every day across North America.")
        html = f"<html><body><h1>About</h1><p>{text}</p></body></html>"
        assert _looks_like_js_shell(html, text) is False


class TestFetchLayering:
    def test_fetch_html_merges_discovered_api_json(self, monkeypatch):
        page_html = """
        <html><body>
          <h1>Acme</h1>
          <script>fetch('/api/team')</script>
        </body></html>
        """
        api_payload = {
            "team": [{"name": "Jane Doe", "email": "jane.doe@acme.com",
                      "title": "CEO"}],
        }
        scraper = WebScraper()
        monkeypatch.setattr(scraper, "_fetch_html_http",
                            lambda url: (page_html, "https://acme.com/"))
        monkeypatch.setattr(scraper, "_fetch_html_playwright",
                            lambda url: (None, [], url))

        def fake_json(url):
            if url.rstrip("/").endswith("/api/team"):
                return api_payload
            return None

        monkeypatch.setattr(scraper, "fetch_json", fake_json)
        # Avoid real DNS SSRF checks for acme.com in this unit test.
        monkeypatch.setattr(
            "web_scraper.is_safe_public_url",
            lambda u: u.startswith("https://acme.com"),
        )
        monkeypatch.setattr(
            "web_scraper.discover_api_endpoints",
            lambda html, base, **kw: ["https://acme.com/api/team"],
        )

        out = scraper.fetch_html("https://acme.com/")
        assert out is not None
        assert "jane.doe@acme.com" in out
        assert "api-source" in out

    def test_playwright_used_when_http_shell_has_no_api(self, monkeypatch):
        shell = '<html><body><div id="__next"></div><script>' + ("x" * 5000) + \
                "</script></body></html>"
        rendered = (
            "<html><body><h1>Acme team</h1>"
            "<a href='mailto:jane.doe@acme.com'>Jane</a></body></html>"
        )
        scraper = WebScraper()
        monkeypatch.setattr(scraper, "_fetch_html_http",
                            lambda url: (shell, "https://acme.com/"))
        monkeypatch.setattr(scraper, "fetch_json", lambda url: None)
        monkeypatch.setattr(
            "web_scraper.discover_api_endpoints",
            lambda *a, **k: [],
        )
        called = {"pw": False}

        def fake_pw(url):
            called["pw"] = True
            return rendered, [], "https://www.acme.com/"

        monkeypatch.setattr(scraper, "_fetch_html_playwright", fake_pw)
        monkeypatch.setattr(
            "web_scraper.is_safe_public_url",
            lambda u: u.startswith("https://acme.com"),
        )
        out = scraper.fetch_html("https://acme.com/")
        assert called["pw"] is True
        assert "jane.doe@acme.com" in out

    def test_http_miss_does_not_launch_playwright(self, monkeypatch):
        scraper = WebScraper()
        monkeypatch.setattr(scraper, "_fetch_html_http",
                            lambda url: (None, "https://acme.com/missing"))
        called = {"pw": False}
        monkeypatch.setattr(
            scraper, "_fetch_html_playwright",
            lambda url: called.__setitem__("pw", True) or (None, [], url),
        )
        monkeypatch.setattr(
            "web_scraper.is_safe_public_url",
            lambda u: u.startswith("https://acme.com"),
        )
        assert scraper.fetch_html("https://acme.com/missing") is None
        assert called["pw"] is False

    def test_noscript_enable_javascript_is_not_a_js_shell(self):
        html = ("<html><body><noscript>Please enable JavaScript</noscript>"
                "<p>Hi</p></body></html>")
        assert _looks_like_js_shell(html, "Hi") is False

    def test_head_redirect_loop_is_not_success(self, monkeypatch):
        scraper = WebScraper()

        class _R:
            def __init__(self, status_code, location=None):
                self.status_code = status_code
                self.headers = {"Location": location} if location else {}

        def fake_head(url, timeout=None):
            return _R(302, location=url)  # bounce forever to self

        monkeypatch.setattr(scraper._client, "head", fake_head)
        monkeypatch.setattr(
            "web_scraper.is_safe_public_url",
            lambda u: u.startswith("https://acme.com"),
        )
        resp = scraper.head("https://acme.com/bounce", timeout=1, allow_redirects=True)
        assert resp.status_code >= 400

    def test_rejects_private_hosts(self):
        assert is_safe_public_url("http://127.0.0.1/") is False
        assert is_safe_public_url("http://169.254.169.254/latest") is False

    def test_stub_json_does_not_suppress_playwright(self, monkeypatch):
        shell = '<html><body><div id="__next"></div><script>' + ("x" * 5000) + \
                "</script></body></html>"
        rendered = "<html><body><a href='mailto:jane.doe@acme.com'>Jane</a></body></html>"
        scraper = WebScraper()
        monkeypatch.setattr(scraper, "_fetch_html_http",
                            lambda url: (shell, "https://acme.com/"))
        monkeypatch.setattr(
            scraper, "fetch_json",
            lambda url: {"email": "info@acme.com", "members": []},
        )
        monkeypatch.setattr(
            "web_scraper.discover_api_endpoints",
            lambda *a, **k: ["https://acme.com/api/team"],
        )
        called = {"pw": False}
        monkeypatch.setattr(
            scraper, "_fetch_html_playwright",
            lambda url: (called.__setitem__("pw", True) or rendered, [], url),
        )
        monkeypatch.setattr(
            "web_scraper.is_safe_public_url",
            lambda u: u.startswith("https://acme.com"),
        )
        out = scraper.fetch_html("https://acme.com/")
        assert called["pw"] is True
        assert "jane.doe@acme.com" in out
        assert "info@acme.com" in out  # SSR/API signal preserved alongside render

    def test_spa_marker_wins_over_short_ssr_blurb(self):
        blurb = ("Acme builds warehouse robotics for industrial teams that need "
                 "safer fulfillment every day.")  # >120 chars
        html = f'<div id="__next"><p>{blurb}</p></div>'
        assert _looks_like_js_shell(html, blurb) is True

    def test_empty_json_not_useful(self):
        assert _json_payload_useful([]) is False
        assert _json_payload_useful({}) is False
        assert _json_payload_useful(
            {"status": "ok", "data": {"team": [], "people": []}}) is False
        assert _json_payload_useful(
            [{"name": "Jane", "email": "jane@acme.com"}]) is True
        assert _json_has_people(
            [{"name": "Jane", "email": "jane@acme.com"}]) is True
        # name+title without contact is not enough to suppress Playwright.
        assert _json_has_people(
            [{"name": "Team", "title": "Our Team", "members": []}]) is False
        assert _json_has_people(
            {"name": "About", "title": "About Us"}) is False
        # Lone company inbox is research-useful but must not suppress Playwright.
        stub = {"email": "info@acme.com", "members": []}
        assert _json_payload_useful(stub) is True
        assert _json_has_people(stub) is False

    def test_next_data_marker_matches_lowercased_html(self):
        blurb = ("Acme builds warehouse robotics for industrial teams that need "
                 "safer fulfillment every day.")
        html = f'<script id="__NEXT_DATA__" type="application/json">{{}}</script><p>{blurb}</p>'
        assert _looks_like_js_shell(html, blurb) is True

    def test_linkedin_inside_next_data_does_not_suppress_shell(self):
        blurb = ("Acme builds warehouse robotics for industrial teams that need "
                 "safer fulfillment every day.")
        html = (
            f'<div id="__next"><p>{blurb}</p></div>'
            '<script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"linkedin":"https://linkedin.com/in/jane"}}}'
            "</script>"
        )
        assert _looks_like_js_shell(html, blurb) is True

    def test_ssr_mailto_anchor_suppresses_shell(self):
        blurb = ("Acme builds warehouse robotics for industrial teams that need "
                 "safer fulfillment every day.")
        html = (
            f'<div id="__next"><p>{blurb}</p>'
            '<a href="mailto:jane.doe@acme.com">Email Jane</a></div>'
        )
        assert _looks_like_js_shell(html, blurb) is False

    def test_safe_get_handles_gzip_encoded_responses(self, monkeypatch):
        """Regression: rebuilt httpx.Response used to DecodingError on gzip sites."""
        import gzip
        import httpx as httpx_mod

        body = b"<html><body><h1>Acme</h1><p>Hello team page content here.</p></body></html>"
        compressed = gzip.compress(body)

        class _StreamResp:
            status_code = 200
            headers = httpx_mod.Headers({
                "Content-Type": "text/html; charset=utf-8",
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            })
            request = httpx_mod.Request("GET", "https://acme.com/")

            def iter_bytes(self):
                # httpx decompresses before iter_bytes in real life; simulate that.
                yield body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        scraper = WebScraper()
        monkeypatch.setattr(
            scraper._client, "stream",
            lambda *a, **k: _StreamResp(),
        )
        monkeypatch.setattr(
            "web_scraper.is_safe_public_url",
            lambda u: u.startswith("https://acme.com"),
        )
        fetched = scraper._safe_get("https://acme.com/")
        assert fetched is not None
        assert fetched.status_code == 200
        assert "Acme" in fetched.text
        assert "content-encoding" not in {k.lower() for k in fetched.headers}
