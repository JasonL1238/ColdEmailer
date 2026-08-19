"""Layered page fetcher for company research.

Order of operations (fastest → heaviest):
  1. Discover same-origin JSON/API endpoints from the HTML/JS shell and call them
     directly (the DevTools → Network → Fetch/XHR approach, done programmatically).
  2. Fetch normal HTML over HTTPX + parse with BeautifulSoup/trafilatura.
  3. Fall back to Playwright only when the page is clearly client-rendered or the
     HTTP body is an empty JS shell.

A site that blocks HTTPX (403/429/503) or times out escalates to step 3 rather
than being written off — those are exactly the pages a browser can still get.
A clean 404 does not: there is nothing there to render.

Scrapy is intentionally not used here: enrichment runs as a short in-process
crawl inside FastAPI jobs, not as a standalone crawl project. HTTPX covers the
same request/retry needs at this scale.

SSRF-safe (public http/https only). robots.txt is ignored.
"""
from __future__ import annotations

import contextlib
import html as html_lib
import json
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import httpx
import ipaddress
import socket
import trafilatura
from bs4 import BeautifulSoup

from text_cleaner import TextCleaner

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 8
FETCH_RETRIES = 3
MAX_API_FETCHES = 8
# Ceiling on remembered 404 endpoints per crawl. A crawl visits tens of pages
# across a handful of origins, so this is slack, not a real limit; it exists so
# a pathological site cannot grow the set without bound.
MAX_DEAD_API_PROBES = 512
# Memory backstop only — MAX_RESPONSE_BYTES is the binding limit, since 2MB of
# sitemap XML holds roughly 25k URLs. This must not bind first: people pages
# sit at raw index 3,853 in Zscaler's sitemap and 16,427 in Val Town's, so an
# earlier cap of 2,000 silently threw away every page the feature exists to
# find. A site whose sitemap exceeds 2MB is read only as far as the truncation.
SITEMAP_MAX_URLS = 50_000
SITEMAP_MAX_INDEX_CHILDREN = 3
_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
# Leave headroom so Playwright-harvested XHR people are not dropped after
# eight identical info@ stubs fill the merge budget.
MAX_API_BEFORE_BROWSER = 4
MIN_USEFUL_TEXT = 120
# Statuses that mean "the server refused this client", not "nothing here".
# Worth a browser retry; a 404 is not.
BLOCK_STATUSES = (403, 429, 503)
# Honour Retry-After, but never stall a crawl job on a server's say-so.
MAX_RETRY_AFTER_SLEEP = 5.0
# Recycle a pooled browser periodically so a long crawl cannot grow unbounded.
PLAYWRIGHT_PAGES_PER_BROWSER = 40

_USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
)

# Paths that often back team/about UIs when the marketing page is a JS shell.
_COMMON_API_PATHS = (
    "/api/team", "/api/people", "/api/staff", "/api/employees",
    "/api/leadership", "/api/about", "/api/company", "/api/contact",
    "/wp-json/wp/v2/pages", "/wp-json/wp/v2/users",
    "/api/v1/team", "/api/v1/people", "/api/v2/team",
)

_API_URL_RE = re.compile(
    r"""(?P<url>
        https?://[^\s"'<>\\]+
        |/(?:api|graphql|wp-json|data|cms)[^\s"'<>\\]*
        |[^\s"'<>\\]+\.json(?:\?[^\s"'<>\\]*)?
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_FETCH_CALL_RE = re.compile(
    r"""(?:fetch|axios\.(?:get|post)|\$\.(?:get|ajax))\s*\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
# Compared against html.lower() — keep these lowercase.
_SPA_MARKERS = (
    "__next_data__", "window.__nuxt__", "ng-version", "data-reactroot",
    'id="__next"', 'id="root"', "id='root'", 'id="app"',
)


# RFC 6052 well-known prefix: a DNS64 resolver embeds the IPv4 address in the
# low 32 bits, so 64:ff9b::a00:1 is 10.0.0.1 wearing a globally-routable coat.
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")


def _embedded_ipv4(ip: ipaddress._BaseAddress):
    """The IPv4 address an IPv6 address stands for, or None."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    if ip in _NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _address_is_public(ip: ipaddress._BaseAddress) -> bool:
    embedded = _embedded_ipv4(ip)
    if embedded is not None and not embedded.is_global:
        return False
    return bool(ip.is_global)


def resolve_public_tcp_addresses(host: str, port: int) -> List[tuple]:
    """Resolve a TCP destination once, rejecting any non-public answer.

    Callers connect to the returned socket addresses instead of resolving the
    hostname a second time.  Besides keeping the SSRF rule in one place, that
    closes the check/connect gap for non-HTTP transports such as direct SMTP.
    """
    if not host or not isinstance(port, int) or not 1 <= port <= 65535:
        return []

    # Judge literals as written.  DNS64 can otherwise dress a private IPv4
    # literal in a globally routable IPv6 address before we inspect it.
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(literal):
            return []
        if isinstance(literal, ipaddress.IPv6Address):
            sockaddr = (str(literal), port, 0, 0)
            family = socket.AF_INET6
        else:
            sockaddr = (str(literal), port)
            family = socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]

    try:
        infos = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, UnicodeError):
        return []
    if not infos:
        return []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return []
        if not _address_is_public(ip):
            return []
    return infos


def is_safe_public_url(url: str) -> bool:
    """True only for http(s) URLs on standard ports whose host resolves
    exclusively to public IP addresses. Blocks SSRF against loopback,
    link-local (169.254.0.0/16), RFC1918, and reserved ranges."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port  # raises ValueError on malformed ports
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if port not in (None, 80, 443):
        return False
    host = parsed.hostname
    if not host:
        return False

    return bool(resolve_public_tcp_addresses(
        host, port or (443 if parsed.scheme == "https" else 80)))


@dataclass
class _Fetched:
    """Detached HTTP result — never rebuild httpx.Response with raw headers
    (Content-Encoding would make httpx try to gunzip an already-decoded body)."""
    status_code: int
    headers: dict
    content: bytes
    final_url: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (
        pa.scheme == pb.scheme
        and (pa.hostname or "").lower() == (pb.hostname or "").lower()
        and (pa.port or (443 if pa.scheme == "https" else 80))
        == (pb.port or (443 if pb.scheme == "https" else 80))
    )


def _strip_scripts_styles(html: str) -> str:
    cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", html or "", flags=re.I | re.S)
    return re.sub(r"<style[^>]*>.*?</style>", " ", cleaned, flags=re.I | re.S)


def _rough_visible_text(html: str) -> str:
    useful = _strip_scripts_styles(html)
    useful = re.sub(r"<[^>]+>", " ", useful)
    return re.sub(r"\s+", " ", useful).strip()


def _looks_like_js_shell(html: str, text: Optional[str]) -> bool:
    """True when the HTTP body is unlikely to contain hydrated research content.

    Strong SPA markers still win even if SSR emitted a short blurb (≥120 chars) —
    team grids are often client-only behind that chrome.
    """
    if not html:
        return True
    useful = (text if text is not None else _rough_visible_text(html)).strip()
    lower = html.lower()
    has_spa = any(marker in lower for marker in _SPA_MARKERS)
    if has_spa:
        # Count real SSR anchors (href kept) but ignore script/JSON blobs so a
        # LinkedIn URL inside __NEXT_DATA__ cannot suppress Playwright.
        markup = _strip_scripts_styles(html).lower()
        if "mailto:" in markup or "linkedin.com/in/" in markup:
            return False
        # SSR blurbs under ~800 chars still count as shells needing hydration.
        return len(useful) < 800
    if len(useful) >= MIN_USEFUL_TEXT:
        return False
    script_len = sum(
        len(m) for m in re.findall(r"<script[^>]*>.*?</script>", html, re.I | re.S)
    )
    return script_len > 4000 and len(useful) < MIN_USEFUL_TEXT


_EMAIL_IN_JSON_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def _person_dicts_in_json(obj, depth: int = 0) -> int:
    """Count dicts that look like named people with email or LinkedIn.

    name+title alone is too weak — CMS pages like {"name":"About","title":"About Us"}
    must not count as people (that used to skip Playwright on JS shells).
    """
    if depth > 6:
        return 0
    count = 0
    if isinstance(obj, list):
        for item in obj:
            count += _person_dicts_in_json(item, depth + 1)
        return count
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("full_name") or obj.get("fullName")
        has_name = bool(name and str(name).strip())
        # Reject obvious non-person page titles.
        if has_name and str(name).strip().lower() in {
            "about", "about us", "team", "our team", "company", "contact",
            "home", "leadership", "people",
        }:
            has_name = False
        email = obj.get("email")
        linkedin = obj.get("linkedin") or obj.get("linkedin_url")
        has_contact = bool(
            (email and str(email).strip() and "@" in str(email))
            or (linkedin and "linkedin.com" in str(linkedin).lower())
        )
        if has_name and has_contact:
            count += 1
        for v in obj.values():
            if isinstance(v, (list, dict)):
                count += _person_dicts_in_json(v, depth + 1)
    return count


def _json_payload_useful(payload) -> bool:
    """True when JSON is worth merging into research HTML (email or people)."""
    if payload is None or payload in ([], {}):
        return False
    if isinstance(payload, (list, dict)) and len(payload) == 0:
        return False
    try:
        blob = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    if _EMAIL_IN_JSON_RE.search(blob):
        return True
    return _person_dicts_in_json(payload) >= 1


def _json_has_people(payload) -> bool:
    """True only for named-person records — used to skip Playwright.

    A lone info@ company inbox in an empty team payload must NOT suppress the
    browser fallback on JS shells.
    """
    if payload is None:
        return False
    return _person_dicts_in_json(payload) >= 1


def discover_api_endpoints(html: str, base_url: str,
                           limit: int = MAX_API_FETCHES,
                           probe_common: bool = False) -> List[str]:
    """Find likely JSON/XHR endpoints the page would load in DevTools Network.

    Only returns same-origin http(s) URLs that pass the SSRF check.
    """
    if not html or not base_url or not is_safe_public_url(base_url):
        return []
    found: List[str] = []
    seen = set()

    def _add(raw: str):
        if not raw or raw.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            return
        absolute = urljoin(base_url, raw.strip().rstrip("\\").rstrip(")"))
        absolute = absolute.split("#", 1)[0]
        path = (urlparse(absolute).path or "").lower()
        if path.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                          ".woff", ".woff2", ".map")):
            return
        interesting = (
            path.endswith(".json")
            or "/api/" in path or path.startswith("/api")
            or "/wp-json/" in path or path.startswith("/wp-json")
            or "/graphql" in path
            or "/data/" in path
            or "/cms/" in path
        )
        if not interesting:
            return
        if not is_safe_public_url(absolute):
            return
        if not _same_origin(base_url, absolute):
            return
        key = absolute.rstrip("/")
        if key in seen:
            return
        seen.add(key)
        found.append(absolute)

    # Next.js / Nuxt embedded payloads often name further data URLs.
    for m in re.finditer(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            html, re.I | re.S):
        blob = m.group(1)
        for um in _API_URL_RE.finditer(blob):
            _add(um.group("url"))
        for um in _FETCH_CALL_RE.finditer(blob):
            _add(um.group(1))

    for m in _FETCH_CALL_RE.finditer(html):
        _add(m.group(1))
    for m in _API_URL_RE.finditer(html):
        _add(m.group("url"))

    # Only guess conventional API paths when the document looks like a JS shell —
    # otherwise we waste a burst of 404s on ordinary static sites.
    if probe_common or _looks_like_js_shell(html, None):
        origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
        for path in _COMMON_API_PATHS:
            _add(urljoin(origin + "/", path.lstrip("/")))

    def _rank(u: str) -> Tuple[int, str]:
        p = urlparse(u).path.lower()
        if any(x in p for x in ("team", "people", "staff", "leadership", "employee")):
            return (0, u)
        if any(x in p for x in ("about", "contact", "company")):
            return (1, u)
        if "wp-json" in p:
            return (2, u)
        return (3, u)

    found.sort(key=_rank)
    return found[:limit]


def json_to_research_html(url: str, payload) -> str:
    """Turn a JSON API response into HTML enrichment can email-scan + text-extract."""
    try:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        body = str(payload)
    body = body[:80000]
    safe_url = html_lib.escape(url, quote=True)
    return (
        f"<!-- api-source: {safe_url} -->\n"
        f"<section data-api-source=\"{safe_url}\">\n"
        f"<h2>API {html_lib.escape(url)}</h2>\n"
        f"<pre>{html_lib.escape(body)}</pre>\n"
        f"</section>\n"
    )


class WebScraper:
    """API-first → HTTPX HTML → Playwright fallback scraper."""

    def __init__(self):
        self.text_cleaner = TextCleaner()
        self.min_delay = 0.05
        self._delay_override = threading.local()
        # Why the last HTTP fetch failed, per thread: a refusal is worth a
        # browser retry, a 404 is not. Thread-local because enrichment fetches
        # pages from a small worker pool.
        self._http_state = threading.local()
        # Pooled Chromium, per thread. Playwright's sync API is bound to the
        # thread that started it, so this can never be shared across threads.
        self._pw_state = threading.local()
        self._ua_lock = threading.Lock()
        # Exact JSON endpoint URLs that answered a definitive 404/410 during the
        # current crawl. discover_api_endpoints re-derives the same 8 guessed
        # /api/... paths on every JS-shell page of a site, so without this a
        # 40-page SPA pays the same 8 404s (plus 8 DNS lookups and 8 politeness
        # sleeps) forty times over. Reset per crawl by reset_dead_api_probes()
        # rather than kept process-wide: EnrichmentService is a module-level
        # singleton, so a persistent cache would let one company's verdicts
        # suppress another's endpoints.
        self._dead_api_probes: Set[str] = set()
        self._dead_api_lock = threading.Lock()
        self.last_request_time: Dict[str, float] = {}
        self._client = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers=self._browser_headers(),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        # Back-compat for callers that still do scraper.session.head(...)
        self.session = self

    # --- requests.Session-shaped shims used by enrichment.find_website ---

    def head(self, url: str, timeout: float = 4, allow_redirects: bool = True,
             **_kwargs):
        class _Resp:
            def __init__(self, status_code: int, final_url: str):
                self.status_code = status_code
                self.url = final_url

        if not is_safe_public_url(url):
            return _Resp(400, url)
        try:
            req_timeout = httpx.Timeout(float(timeout), connect=min(4.0, float(timeout)))
            if allow_redirects:
                # Manual redirect loop so every hop stays SSRF-safe.
                current = url
                for _ in range(MAX_REDIRECTS + 1):
                    if not is_safe_public_url(current):
                        return _Resp(400, current)
                    r = self._client.head(current, timeout=req_timeout)
                    status = r.status_code
                    if status in (301, 302, 303, 307, 308):
                        loc = r.headers.get("Location")
                        if not loc:
                            return _Resp(599, current)
                        current = urljoin(current, loc)
                        continue
                    return _Resp(status, current)
                # Too many redirects — do not report a soft 3xx success.
                return _Resp(599, current)
            r = self._client.head(url, timeout=req_timeout)
            return _Resp(r.status_code, url)
        except Exception:
            return _Resp(599, url)

    def close(self):
        self._close_browser()
        try:
            self._client.close()
        except Exception:
            pass

    # --- pooled Chromium ---------------------------------------------------

    @contextlib.contextmanager
    def browser_session(self):
        """Reuse one Chromium for the duration of a crawl.

        Without this every JS-shell page pays a full browser launch (~1s).
        Inside a session the browser is kept alive and only the page context is
        recycled. Nested sessions are refcounted; the browser is torn down when
        the outermost one exits.
        """
        state = self._pw_state
        state.depth = getattr(state, "depth", 0) + 1
        try:
            yield self
        finally:
            state.depth = getattr(state, "depth", 1) - 1
            if state.depth <= 0:
                self._close_browser()

    def _acquire_browser(self):
        """Return a live Chromium for this thread, or None if unavailable."""
        state = self._pw_state
        browser = getattr(state, "browser", None)
        if browser is not None:
            if getattr(state, "pages", 0) < PLAYWRIGHT_PAGES_PER_BROWSER:
                state.pages = getattr(state, "pages", 0) + 1
                return browser
            self._close_browser()  # recycle before it gets fat
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            playwright = sync_playwright().start()
        except Exception:
            return None
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            try:
                playwright.stop()
            except Exception:
                pass
            return None
        state.playwright = playwright
        state.browser = browser
        state.pages = 1
        return browser

    def _release_browser(self):
        """Drop the browser unless a browser_session is holding it open."""
        if getattr(self._pw_state, "depth", 0) <= 0:
            self._close_browser()

    def _close_browser(self):
        state = self._pw_state
        browser = getattr(state, "browser", None)
        playwright = getattr(state, "playwright", None)
        state.browser = None
        state.playwright = None
        state.pages = 0
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    @staticmethod
    def _browser_headers(user_agent: Optional[str] = None,
                         accept: Optional[str] = None) -> dict:
        ua = user_agent or _USER_AGENTS[0]
        return {
            "User-Agent": ua,
            "Accept": accept or (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "DNT": "1",
        }

    def _rotate_identity(self):
        ua = random.choice(_USER_AGENTS)
        with self._ua_lock:
            self._client.headers.update(self._browser_headers(ua))

    def delay_override(self, seconds: float):
        scraper = self

        class _DelayCtx:
            def __enter__(self_inner):
                self_inner._prev = getattr(scraper._delay_override, "value", None)
                scraper._delay_override.value = seconds
                return scraper

            def __exit__(self_inner, *exc):
                if self_inner._prev is None:
                    if hasattr(scraper._delay_override, "value"):
                        del scraper._delay_override.value
                else:
                    scraper._delay_override.value = self_inner._prev

        return _DelayCtx()

    def _effective_delay(self) -> float:
        override = getattr(self._delay_override, "value", None)
        return float(override) if override is not None else float(self.min_delay)

    def _rate_limit(self, url: str):
        domain = urlparse(url).netloc
        delay = self._effective_delay()
        if delay <= 0:
            self.last_request_time[domain] = time.time()
            return
        if domain in self.last_request_time:
            elapsed = time.time() - self.last_request_time[domain]
            if elapsed < delay:
                time.sleep(delay - elapsed)
        self.last_request_time[domain] = time.time()

    def _safe_get(self, url: str, timeout: float = 8.0,
                  headers: Optional[dict] = None,
                  stay_origin: Optional[str] = None) -> Optional[_Fetched]:
        """GET with per-hop SSRF checks. If stay_origin is set, every hop must
        remain same-origin with that URL (for API-first fetches).

        Body is streamed and truncated at MAX_RESPONSE_BYTES so a huge response
        cannot OOM the worker.
        """
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not is_safe_public_url(current):
                return None
            if stay_origin and not _same_origin(stay_origin, current):
                return None
            try:
                with self._client.stream(
                    "GET", current, timeout=timeout, headers=headers or {},
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            return None
                        current = urljoin(current, location)
                        continue
                    chunks: List[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= MAX_RESPONSE_BYTES:
                            break
                    content = b"".join(chunks)[:MAX_RESPONSE_BYTES]
                    # Copy headers as a plain dict; drop encoding so callers never
                    # try to decode an already-decoded body.
                    hdrs = {
                        k: v for k, v in response.headers.items()
                        if k.lower() not in ("content-encoding", "content-length",
                                             "transfer-encoding")
                    }
                    return _Fetched(
                        status_code=response.status_code,
                        headers=hdrs,
                        content=content,
                        final_url=current,
                    )
            except Exception:
                return None
        return None

    def reset_dead_api_probes(self) -> None:
        """Forget which JSON endpoints 404'd. Call once per crawl."""
        with self._dead_api_lock:
            self._dead_api_probes.clear()

    def _sitemap_locs(self, url: str, origin: str) -> List[str]:
        """<loc> values from one sitemap document. [] if it is not one."""
        try:
            response = self._safe_get(
                url, timeout=6.0, stay_origin=origin,
                headers=self._browser_headers(
                    accept="application/xml, text/xml, */*;q=0.1"))
        except Exception:
            return []
        if response is None or response.status_code >= 400:
            return []
        text = response.text or ""
        if "<loc" not in text.lower():
            return []
        return [html_lib.unescape(m.group(1)) for m in _SITEMAP_LOC_RE.finditer(text)]

    def fetch_sitemap_urls(self, base_url: str,
                           limit: int = SITEMAP_MAX_URLS) -> List[str]:
        """Same-origin page URLs advertised by /sitemap.xml. [] when absent.

        Two thirds of sites publish one, and it names real pages — including
        the locale-prefixed, .html-suffixed people pages that no guessed path
        (/team, /people, ...) can reach. Follows a sitemap index one level,
        because large sites publish an index rather than a flat list.
        """
        if not is_safe_public_url(base_url):
            return []
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return []
        origin = f"{parsed.scheme}://{parsed.netloc}"
        root = urljoin(origin + "/", "sitemap.xml")

        out: List[str] = []
        seen: Set[str] = set()
        children: List[str] = []

        def _absorb(locs: List[str], relative_to: str) -> None:
            for loc in locs:
                if len(out) >= limit:
                    return
                try:
                    absolute = urljoin(relative_to, loc.strip()).split("#", 1)[0]
                except ValueError:
                    continue
                if not _same_origin(origin, absolute):
                    continue
                if not is_safe_public_url(absolute):
                    continue
                path = (urlparse(absolute).path or "").lower()
                if path.endswith(".gz"):
                    continue          # compressed: _safe_get hands back bytes
                if path.endswith(".xml"):
                    children.append(absolute)
                    continue
                key = absolute.rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                out.append(absolute)

        _absorb(self._sitemap_locs(root, origin), root)
        for child in children[:SITEMAP_MAX_INDEX_CHILDREN]:
            if len(out) >= limit:
                break
            _absorb(self._sitemap_locs(child, origin), child)
        return out[:limit]

    def fetch_json(self, url: str) -> Optional[object]:
        """GET a JSON endpoint (API-first path). None on failure/non-JSON."""
        if not is_safe_public_url(url):
            return None
        # A 404 earlier in this crawl means the same 404 now, and fetch_json
        # returns None either way. Only 404/410 are recorded (see below), so a
        # 403/429/5xx/timeout still gets retried on the next page. The key is
        # the exact URL, so /api/team never speaks for /api/team/.
        with self._dead_api_lock:
            if url in self._dead_api_probes:
                return None
        self._rate_limit(url)
        headers = self._browser_headers(
            accept="application/json, text/json, */*;q=0.1")
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-origin"
        try:
            response = self._safe_get(
                url, timeout=6.0, headers=headers, stay_origin=url)
            if response is None or response.status_code >= 400:
                # 404/410 mean "this path is not here", which will not change
                # mid-crawl. Every other error is a state a server leaves, and
                # blacklisting a live endpoint over one unlucky request would
                # cost real contacts.
                if response is not None and response.status_code in (404, 410):
                    with self._dead_api_lock:
                        if len(self._dead_api_probes) < MAX_DEAD_API_PROBES:
                            self._dead_api_probes.add(url)
                return None
            if (not _same_origin(url, response.final_url)
                    or not is_safe_public_url(response.final_url)):
                return None
            ctype = (response.headers.get("Content-Type")
                     or response.headers.get("content-type") or "").lower()
            text = response.text or ""
            if "json" not in ctype and not text.lstrip().startswith(("{", "[")):
                return None
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _retry_after_seconds(response: _Fetched) -> float:
        """Parse Retry-After (delta-seconds or HTTP-date). 0 when absent/bad."""
        raw = (response.headers.get("Retry-After")
               or response.headers.get("retry-after") or "").strip()
        if not raw:
            return 0.0
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return 0.0
        if when is None:
            return 0.0
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())

    def _fetch_html_http(self, url: str) -> Tuple[Optional[str], str]:
        """Return (html_or_none, final_public_url). final_url tracks redirects.

        Also records on thread-local state whether a failure was a *refusal*
        (403/429/503, timeout, transport error) rather than a genuine miss, so
        fetch_html knows whether a browser attempt is worth the launch.
        """
        self._http_state.blocked = False
        if not is_safe_public_url(url):
            return None, url
        for attempt in range(FETCH_RETRIES):
            try:
                if attempt:
                    self._rotate_identity()
                    time.sleep(0.15 * attempt)
                self._rate_limit(url)
                headers = {}
                parsed = urlparse(url)
                if parsed.scheme and parsed.netloc:
                    headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
                response = self._safe_get(url, timeout=8.0, headers=headers)
                if response is None:
                    # Timeout / connection error / redirect dead-end.
                    self._http_state.blocked = True
                    continue
                if response.status_code in BLOCK_STATUSES:
                    self._http_state.blocked = True
                    wait = self._retry_after_seconds(response)
                    if wait > 0:
                        time.sleep(min(MAX_RETRY_AFTER_SLEEP, wait))
                    continue
                if response.status_code >= 400:
                    # A real 404/410 — nothing for a browser to render either.
                    self._http_state.blocked = False
                    return None, response.final_url
                ctype = (response.headers.get("Content-Type")
                         or response.headers.get("content-type") or "").lower()
                text = response.text or ""
                final = response.final_url if is_safe_public_url(response.final_url) else url
                if "json" in ctype and text.lstrip().startswith(("{", "[")):
                    try:
                        return json_to_research_html(final, json.loads(text)), final
                    except Exception:
                        return f"<pre>{html_lib.escape(text[:80000])}</pre>", final
                if "html" not in ctype and "<html" not in text[:2000].lower():
                    # Served fine, just not a document — rendering won't help.
                    self._http_state.blocked = False
                    return None, final
                return text, final
            except Exception:
                continue
        return None, url

    def _fetch_html_playwright(self, url: str) -> Tuple[Optional[str], List[str], str]:
        """Render in Chromium and harvest same-origin JSON XHR/fetch responses.

        Returns (html, api_html_chunks, final_url). Soft-fails to (None, [], url)
        when Playwright is not installed or the browser cannot start.

        SSRF: abort only non-public destinations (CDN/scripts allowed). Final
        navigation URL must still be public. JSON bodies are read after settle.
        """
        if not is_safe_public_url(url):
            return None, [], url

        browser = self._acquire_browser()
        if browser is None:
            return None, [], url

        api_chunks: List[str] = []
        html = None
        final = url
        json_responses = []
        try:
            context = browser.new_context(
                user_agent=random.choice(_USER_AGENTS),
                java_script_enabled=True,
            )
            try:
                page = context.new_page()

                def _guard_route(route):
                    # Deny private/metadata targets only — allow CDNs and
                    # public apex↔www redirects so hydration can finish.
                    if not is_safe_public_url(route.request.url):
                        return route.abort()
                    return route.continue_()

                page.route("**/*", _guard_route)

                def _on_response(response):
                    try:
                        if len(json_responses) >= MAX_API_FETCHES:
                            return
                        ctype = (response.headers.get("content-type") or "").lower()
                        req_url = response.url
                        if "json" not in ctype and not req_url.lower().endswith(".json"):
                            return
                        if not is_safe_public_url(req_url):
                            return
                        json_responses.append(response)
                    except Exception:
                        return

                page.on("response", _on_response)
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                final = page.url
                if not is_safe_public_url(final):
                    return None, [], url
                try:
                    page.wait_for_selector(
                        "a[href^='mailto:'], [class*='team'], [class*='people'], "
                        "main, article, h1",
                        timeout=4000,
                    )
                except Exception:
                    pass
                html = page.content()
                # Bodies after settle — avoids response-handler deadlocks.
                for response in json_responses:
                    if len(api_chunks) >= MAX_API_FETCHES:
                        break
                    try:
                        req_url = response.url
                        if not is_safe_public_url(req_url):
                            continue
                        if not _same_origin(final, req_url):
                            continue
                        body = response.text()
                        if not body or len(body) > MAX_RESPONSE_BYTES:
                            continue
                        if not body.lstrip().startswith(("{", "[")):
                            continue
                        payload = json.loads(body)
                        if not _json_payload_useful(payload):
                            continue
                        api_chunks.append(
                            json_to_research_html(req_url, payload))
                    except Exception:
                        continue
            finally:
                try:
                    context.close()
                except Exception:
                    pass
        except Exception:
            # The browser may be wedged — drop it rather than reuse it.
            self._close_browser()
            return None, api_chunks, url
        finally:
            self._release_browser()
        return html, api_chunks, final if is_safe_public_url(final) else url

    def fetch_html(self, url: str) -> Optional[str]:
        """Layered fetch: API endpoints → HTTPX HTML → Playwright if needed."""
        if not is_safe_public_url(url):
            return None

        html, final_url = self._fetch_html_http(url)
        http_blocked = bool(getattr(self._http_state, "blocked", False))
        page_url = final_url if is_safe_public_url(final_url) else url
        api_parts: List[str] = []
        useful_api = False

        # 1) API-first against the post-redirect origin.
        if html:
            for endpoint in discover_api_endpoints(html, page_url):
                payload = self.fetch_json(endpoint)
                if not _json_payload_useful(payload):
                    continue
                api_parts.append(json_to_research_html(endpoint, payload))
                # Only named people suppress Playwright — not a lone info@ inbox.
                if _json_has_people(payload):
                    useful_api = True
                if len(api_parts) >= MAX_API_BEFORE_BROWSER:
                    break

        text = self.extract_text(html) if html else None
        # Two reasons to spend a browser launch:
        #   1. HTTP worked but handed back a JS shell with no people in the API.
        #   2. HTTP was refused outright (403/429/503/timeout) — the page may
        #      well render for a real browser. A 404 never gets here, because
        #      _fetch_html_http only marks genuine refusals as blocked.
        needs_browser = (
            (bool(html) and _looks_like_js_shell(html, text) and not useful_api)
            or (not html and not api_parts and http_blocked)
        )

        if needs_browser:
            pw_html, pw_api, pw_final = self._fetch_html_playwright(page_url)
            if pw_final and is_safe_public_url(pw_final):
                page_url = pw_final
            if pw_html:
                # Always keep SSR + render so cookie walls cannot discard research.
                if html and pw_html.strip() not in html:
                    html = html + "\n" + pw_html
                else:
                    html = pw_html or html
            # Prefer Playwright-harvested XHR people before rediscovery burns
            # the remaining merge budget on the same info@ stubs.
            for chunk in pw_api:
                if len(api_parts) >= MAX_API_FETCHES:
                    break
                api_parts.append(chunk)
            if pw_html:
                for endpoint in discover_api_endpoints(html, page_url):
                    if len(api_parts) >= MAX_API_FETCHES:
                        break
                    payload = self.fetch_json(endpoint)
                    if _json_payload_useful(payload):
                        api_parts.append(json_to_research_html(endpoint, payload))

        if not html and not api_parts:
            return None
        if api_parts:
            merged = (html or f"<html><head><title>{html_lib.escape(page_url)}</title></head><body></body></html>")
            return merged + "\n" + "\n".join(api_parts)
        return html

    def extract_text(self, html: str) -> Optional[str]:
        """Extract cleaned main-content text from an HTML string."""
        if not html:
            return None
        try:
            extracted = trafilatura.extract(html)
            if extracted:
                return self.text_cleaner.clean(extracted)
            try:
                soup = BeautifulSoup(html, "lxml")
            except Exception:
                soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return self.text_cleaner.clean(soup.get_text())
        except Exception:
            return None
