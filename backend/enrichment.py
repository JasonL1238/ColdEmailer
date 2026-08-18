"""
Company enrichment: scrape a company's site, extract structured metadata
(cloud LLM first, heuristic fallback) and contact email addresses.
"""
import html as html_lib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from contact_enrich import enrich_contacts_outreach
from domain_names import name_tokens as _name_tokens, registered_domain
from contact_verify import (
    annotate_contact,
    canonical_linkedin_profile,
    is_generic_inbox,
    linkedin_matches_person,
    name_tokens,
    select_verified_person_contacts,
)
from web_scraper import WebScraper, is_safe_public_url

try:
    from llm_client import complete_json, get_cloud_llm_provider
except ImportError:
    complete_json = None
    get_cloud_llm_provider = lambda: None

# Fallbacks for sites whose home page exposes no usable navigation. Normal
# crawling follows the site's real links instead of assuming these paths exist.
SUBPAGES = [
    "/about", "/about-us", "/company", "/contact", "/contact-us", "/team",
    "/leadership", "/people", "/careers", "/news", "/press", "/blog",
]

MAX_PAGES_FETCHED = 14
MAX_PAGE_ATTEMPTS = 28
# Warm the next few queued pages while the current one is being parsed. The
# crawl is latency-bound, not politeness-bound (same-domain delay is 50ms), so
# this is close to free. Fast mode opts out: it early-exits often enough that
# prefetching would mostly buy pages we throw away.
PREFETCH_WORKERS = 3
PREFETCH_DEPTH = 3
# Fast path still aims for a quick first contact, but crawls harder when needed.
FAST_MAX_PAGES = 6
FAST_MAX_ATTEMPTS = 12
FAST_DEADLINE_SEC = 90.0
FAST_MIN_DELAY = 0.05
# Deep dive: interview-grade crawl across news/press/careers/policy pages.
DEEP_MAX_PAGES = 28
DEEP_MAX_ATTEMPTS = 56
DEEP_SUBPAGES = [
    "/about", "/about-us", "/company", "/team", "/leadership", "/people",
    "/who-we-are", "/our-team", "/management", "/contact", "/contact-us",
    "/careers", "/jobs", "/news", "/press", "/blog", "/insights",
    "/culture", "/values", "/mission", "/policy", "/policies", "/esg",
    "/sustainability", "/diversity", "/inclusion", "/investors",
]

_LINK_PRIORITY = {
    "leadership": 0, "team": 0, "people": 0, "founder": 0, "management": 0,
    "about": 1, "company": 1, "who-we-are": 1,
    "contact": 2, "careers": 2, "jobs": 2,
    "press": 3, "news": 3, "blog": 4, "insights": 4,
    "culture": 3, "values": 3, "mission": 3, "policy": 3, "policies": 3,
    "diversity": 3, "inclusion": 3, "esg": 4, "sustainability": 4,
}
# Vocabulary for ranking *sitemap* URLs, matched against whole path segments.
#
# The table above is matched as a substring, which is right for the handful of
# anchors on a page but catastrophic against a 16,000-URL sitemap: "team"
# selects val.town/u/fuckyouscratchteam and linear.app/changelog/team-documents,
# "management" selects vercel.com/changelog/spend-management. Requiring the
# segment to *equal* a term rejects all three while keeping
# wsgr.com/en/people/holly-hafford.html and zscaler.com/company/leadership.
#
# People pages only: /about and /company are deliberately absent because they
# are already in preferred_fallbacks, and including them spent the whole budget
# on /company/aup and /company/cookie-policy for sites publishing no people
# page at all.
_SITEMAP_PEOPLE_SEGMENTS = {
    "people": 0, "our-people": 0, "team": 0, "teams": 0, "our-team": 0,
    "leadership": 0, "leadership-team": 0, "management-team": 0,
    "executive-team": 0, "staff": 0, "our-staff": 0, "founders": 0,
    "attorneys": 0, "professionals": 0, "lawyers": 0, "principals": 0,
    "board": 0, "board-of-directors": 0, "directors": 0, "advisors": 0,
    "bios": 0, "profiles": 0,
}


def _path_segments(path: str) -> List[str]:
    """['en', 'people', 'index'] for '/en/people/index.html'."""
    segments = []
    for raw in (path or "").lower().split("/"):
        if not raw:
            continue
        segments.append(raw.rsplit(".", 1)[0] if "." in raw else raw)
    return segments


_SKIP_PATH_WORDS = {
    "login", "signin", "sign-in", "signup", "sign-up", "privacy", "terms",
    "legal", "cookies", "cart", "checkout", "account", "docs", "documentation",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Emails we never want to cold-email
_BAD_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
              "postmaster", "abuse", "privacy", "legal", "unsubscribe", "spam"}
_BAD_DOMAINS = {"example.com", "sentry.io", "wixpress.com", "sentry.wixpress.com",
                "godaddy.com", "domain.com", "yourdomain.com", "email.com",
                "company.com", "sentry-next.wixpress.com", "mysite.com",
                "errors.stripe.com"}
# Error-tracker ingest hosts publish their DSN in client-side JS, and the DSN's
# shape is indistinguishable from an address. The real hosts are always
# subdomains (o415358.ingest.us.sentry.io), so exact-match never caught them.
# Suffix-matched separately from _BAD_DOMAINS because the parent of a bad host
# is not always bad — errors.stripe.com is junk, stripe.com is a real employer.
_BAD_DOMAIN_SUFFIXES = ("sentry.io", "wixpress.com", "ingest.sentry.io")
# A Sentry DSN public key is 32 hex characters. No human picks that as a local
# part, and across a 30-site corpus every one of the 270 machine-generated
# addresses matched this while none of the 162 real ones did.
_MACHINE_LOCAL_RE = re.compile(r"[0-9a-f]{16,}")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

# Preference order for picking the best outreach address
_LOCAL_PRIORITY = [
    "founders", "founder", "ceo", "team", "hello", "hi", "hey", "careers",
    "jobs", "recruiting", "talent", "contact", "info", "press", "sales",
    "support", "admin", "office",
]






def domain_matches_name(company_name: str, domain: Optional[str]) -> bool:
    """True when a domain plausibly belongs to this company.

    'Vellum AI' -> vellum.ai  ✓      'Sabanto' -> epson.com.pe  ✗
    """
    if not domain or not company_name:
        return False
    stem = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())
    if not stem:
        return False
    tokens = _name_tokens(company_name)
    if not tokens:
        return False
    joined = "".join(tokens)
    if joined == stem or joined in stem or stem in joined:
        return True
    # A single distinctive token matching the domain stem is enough
    if any(t == stem or (len(t) >= 5 and (t in stem or stem in t)) for t in tokens):
        return True
    # Tolerate small spelling drift between the saved name and the real domain
    # ("Runaway ML" for runwayml.com) — a typo shouldn't read as a wrong site.
    if len(joined) >= 5 and SequenceMatcher(None, joined, stem).ratio() >= 0.85:
        return True
    return any(len(t) >= 5 and SequenceMatcher(None, t, stem).ratio() >= 0.85
               for t in tokens)


def page_mentions_company(company_name: str, text: str) -> bool:
    """True when scraped page text actually names the company."""
    if not text or not company_name:
        return False
    haystack = re.sub(r"[^a-z0-9]+", " ", text.lower())
    tokens = _name_tokens(company_name)
    if not tokens:
        return False
    joined = "".join(tokens)
    if joined and joined in re.sub(r"\s+", "", haystack):
        return True
    # Every distinctive token has to appear somewhere on the page
    return all(re.search(rf"\b{re.escape(t)}\b", haystack) for t in tokens)


def _decode_js_escapes(text: str) -> str:
    r"""Turn \uXXXX / \xXX escapes into the characters they stand for.

    Sites that embed markup inside a JSON payload write `>` as `>`. The
    backslash is not in EMAIL_RE's character class but `u003e` is alphanumeric,
    so matching started at the `u` and produced `u003ekbrooks@wsgr.com` — which
    passes is_valid_outreach_email, and (because the domain is real) passes the
    MX check too, giving a sendable address that duplicates a real person.
    Decoding first puts the `>` back, and `>` cannot be part of an address.
    """
    if "\\" not in text:
        return text

    def _sub(match: "re.Match") -> str:
        code = int(match.group(1) or match.group(2), 16)
        # Lone surrogates are unencodable; a space is a safe address separator.
        if 0xD800 <= code <= 0xDFFF:
            return " "
        return chr(code)

    return re.sub(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})", _sub, text)


def extract_emails_from_html(html: str) -> List[str]:
    """All plausible addresses, including common public obfuscation schemes."""
    if not html:
        return []
    found = []
    seen = set()
    decoded = _decode_js_escapes(html_lib.unescape(html))

    # Cloudflare's email-protection markup stores an XOR-encoded address.
    for encoded in re.findall(
            r'(?:data-cfemail=["\']|/cdn-cgi/l/email-protection#)([0-9a-f]+)',
            decoded, re.IGNORECASE):
        try:
            key = int(encoded[:2], 16)
            addr = "".join(
                chr(int(encoded[i:i + 2], 16) ^ key)
                for i in range(2, len(encoded), 2)
            )
            if EMAIL_RE.fullmatch(addr):
                found.append(addr)
        except (ValueError, IndexError):
            continue

    # mailto: links first — highest signal
    for m in re.finditer(r'mailto:([^"\'\s?>]+)', decoded, re.IGNORECASE):
        addr = m.group(1).split("?", 1)[0].strip().rstrip(".,;")
        if EMAIL_RE.fullmatch(addr) and addr.lower() not in seen:
            seen.add(addr.lower())
            found.append(addr)

    # "jane [at] acme [dot] com" and the equivalent parenthesized/plain forms.
    # Requiring a full local/domain/TLD shape avoids turning ordinary prose
    # containing the words "at" and "dot" into addresses.
    visible = BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)
    obfuscated_re = re.compile(
        r"\b([A-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\bat\b)\s*"
        r"([A-Z0-9-]+(?:\.[A-Z0-9-]+)*)\s*"
        r"(?:\[dot\]|\(dot\)|\bdot\b)\s*([A-Z]{2,24})\b",
        re.IGNORECASE,
    )
    for m in obfuscated_re.finditer(visible):
        domain = f"{m.group(2)}.{m.group(3)}"
        addr = f"{m.group(1)}@{domain}"
        if EMAIL_RE.fullmatch(addr):
            found.append(addr)

    # then any email-looking text
    for m in EMAIL_RE.finditer(decoded):
        addr = m.group(0).strip().rstrip(".,;")
        if addr.lower() not in seen:
            seen.add(addr.lower())
            found.append(addr)
    out = []
    out_keys = set()
    for addr in found:
        key = addr.lower()
        if key not in out_keys and is_valid_outreach_email(addr):
            out_keys.add(key)
            out.append(addr)
    return out


def discover_internal_links(html: str, base_url: str,
                            company_domain: Optional[str],
                            limit: int = 24) -> List[str]:
    """Return useful same-company links in research priority order."""
    if not html or not base_url:
        return []
    soup = BeautifulSoup(html, "html.parser")
    ranked = []
    seen = set()
    for order, anchor in enumerate(soup.find_all("a", href=True)):
        raw = (anchor.get("href") or "").strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, raw).split("#", 1)[0]
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if company_domain and registered_domain(absolute) != company_domain:
            continue
        path_signal = f"{parsed.path} {anchor.get_text(' ', strip=True)}".lower()
        if any(word in path_signal for word in _SKIP_PATH_WORDS):
            continue
        scores = [score for word, score in _LINK_PRIORITY.items()
                  if word in path_signal]
        if not scores:
            continue
        normalized = absolute.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        ranked.append((min(scores), order, normalized))
    ranked.sort()
    return [url for _, _, url in ranked[:limit]]


def rank_sitemap_pages(urls: List[str], company_domain: Optional[str],
                       limit: int = 12) -> List[str]:
    """People-bearing sitemap URLs, best first. Everything else is dropped.

    The sitemap exists here to reach pages the nav does not link and the
    guessed /team, /people, ... paths cannot spell — a firm's real bios are
    /en/people/<name>.html, and on the captured corpus none of them is
    reachable by crawling. Matching is segment-exact (see
    _SITEMAP_PEOPLE_SEGMENTS); a sitemap is far too large for the substring
    rules that suit a page's handful of anchors.
    """
    ranked = []
    seen = set()
    for order, absolute in enumerate(urls):
        if not absolute:
            continue
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if company_domain and registered_domain(absolute) != company_domain:
            continue
        segments = _path_segments(parsed.path)
        if any(word in (parsed.path or "").lower() for word in _SKIP_PATH_WORDS):
            continue
        scores = [_SITEMAP_PEOPLE_SEGMENTS[seg] for seg in segments
                  if seg in _SITEMAP_PEOPLE_SEGMENTS]
        if not scores:
            continue
        normalized = absolute.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        # Shallower paths first within a tier, so an index page outranks the
        # individual bios beneath it and gets crawled before the budget runs out.
        ranked.append((min(scores), len(segments), order, normalized))
    ranked.sort()
    return [url for _, _, _, url in ranked[:limit]]


def is_valid_outreach_email(addr: str) -> bool:
    addr = addr.lower()
    local, _, domain = addr.partition("@")
    if not local or not domain:
        return False
    if any(addr.endswith(ext) for ext in _IMAGE_EXT):
        return False
    if local in _BAD_LOCAL or any(b in local for b in ("noreply", "no-reply", "donotreply")):
        return False
    if domain in _BAD_DOMAINS:
        return False
    if any(domain == suffix or domain.endswith("." + suffix)
           for suffix in _BAD_DOMAIN_SUFFIXES):
        return False
    if _MACHINE_LOCAL_RE.fullmatch(local):
        return False
    if len(addr) > 80 or addr.count("@") != 1:
        return False
    return True


def rank_outreach_emails(emails: List[str], company_domain: Optional[str]) -> List[str]:
    """Sort candidate emails: company-domain first, then person locals over generics."""
    def score(addr: str) -> Tuple[int, int, str]:
        local, _, domain = addr.lower().partition("@")
        domain_score = 0 if (company_domain and registered_domain(domain) == company_domain) else 1
        if is_generic_inbox(local):
            try:
                local_score = 100 + _LOCAL_PRIORITY.index(local)
            except ValueError:
                local_score = 150
        else:
            # Named person addresses rank above every generic inbox
            local_score = 0 if ("." in local or len(local) <= 12) else 5
        return (domain_score, local_score, addr)
    return sorted(dict.fromkeys(emails), key=score)


_ROLE_PATTERNS = [
    ("CEO", r"\b(?:chief executive officer|ceo)\b", 0),
    ("Founder", r"\b(?:co-?founder|founder)\b", 1),
    ("President", r"\bpresident\b", 2),
    ("CTO", r"\b(?:chief technology officer|cto)\b", 3),
    ("COO", r"\b(?:chief operating officer|coo)\b", 3),
    ("CFO", r"\b(?:chief financial officer|cfo)\b", 3),
    ("Chief", r"\bchief [a-z -]+ officer\b", 3),
    ("Partner", r"\b(?:managing )?partner\b", 4),
    ("VP", r"\b(?:vice president|vp)\b", 5),
    ("Head", r"\bhead of\b", 6),
    ("Director", r"\bdirector\b", 7),
    ("Hiring manager", r"\bhiring manager\b", 7),
    ("Engineering manager", r"\bengineering manager\b", 7),
    ("Recruiting", r"\b(?:recruiter|recruiting|talent acquisition|talent partner)\b", 8),
]


def _school_aliases(preferred_school: Optional[str]) -> List[str]:
    school = (preferred_school or "").strip()
    if not school:
        return []
    lowered = school.lower()
    aliases = [school]
    if "pennsylvania" in lowered or "upenn" in lowered or "wharton" in lowered:
        aliases += [
            "University of Pennsylvania", "UPenn", "Wharton",
            "Penn Engineering", "Penn alumnus", "Penn alumna", "Penn alumni",
        ]
    return list(dict.fromkeys(a.lower() for a in aliases if a))


def _school_match(context: str, preferred_school: Optional[str]) -> bool:
    text = (context or "").lower()
    if "penn state" in text and not any(
            marker in text for marker in ("university of pennsylvania", "upenn", "wharton")):
        return False
    return any(alias in text for alias in _school_aliases(preferred_school))


def _affiliation_terms(preferred_affiliations: Optional[str]) -> List[str]:
    """User-supplied former employers/communities, one per line or comma."""
    return [
        term.strip()
        for term in re.split(r"[,;\n]+", preferred_affiliations or "")
        if len(term.strip()) >= 3
    ][:30]


def _affinity_matches(context: str, preferred_school: Optional[str],
                      preferred_affiliations: Optional[str]) -> List[str]:
    matches = []
    if _school_match(context, preferred_school):
        matches.append(preferred_school or "Same school")
    lowered = (context or "").lower()
    for term in _affiliation_terms(preferred_affiliations):
        if re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", lowered):
            matches.append(f"Shared: {term}")
    return list(dict.fromkeys(matches))


def _provenance(context: str, page_url: Optional[str], seniority: int,
                preferred_school: Optional[str],
                preferred_affiliations: Optional[str]) -> Dict:
    """Where a scraped person came from and why they rank where they do.

    Shared by the two extraction paths — an address found on a page, and a
    LinkedIn link found on one. They build different records but record their
    evidence identically, and a field added to one and not the other is a
    person the ranker sees differently depending on how they were found.
    """
    school_match = _school_match(context, preferred_school)
    return {
        "source_url": page_url,
        "evidence": context[:500],
        "school_match": school_match,
        "school": preferred_school if school_match else None,
        "affinity": _affinity_matches(
            context, preferred_school, preferred_affiliations),
        "seniority_rank": seniority,
    }


def _linkedin_profile_url(value: str, person_name: Optional[str] = None) -> Optional[str]:
    """Normalize a LinkedIn /in/ URL from a first-party page.

    When person_name is known, reject slugs that do not match that person so
    we never store a same-first-name stranger's profile.
    """
    try:
        url = canonical_linkedin_profile(value)
    except ValueError:
        return None
    if person_name and not linkedin_matches_person(url, person_name):
        return None
    return url


def _infer_role(context: str, local: str) -> Tuple[Optional[str], int]:
    text = context or ""
    for label, pattern, rank in _ROLE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label, rank
    generic = {
        "ceo": ("CEO", 0), "founder": ("Founder", 1),
        "founders": ("Founders", 1), "careers": ("Careers inbox", 10),
        "jobs": ("Careers inbox", 10), "recruiting": ("Recruiting", 8),
        "talent": ("Recruiting", 8),
    }
    return generic.get(local.lower(), (None, 20))


def _infer_name(context: str, local: str) -> str:
    """Best-effort person name, grounded in nearby page text or the address."""
    role_words = (
        r"(?:CEO|Chief Executive Officer|Founder|Co-Founder|President|CTO|"
        r"Chief Technology Officer|COO|CFO|Vice President|VP|Head of [A-Za-z &-]+|"
        r"Director(?: of [A-Za-z &-]+)?)"
    )
    name = r"([A-Z][a-z]+(?:\s+[A-Z][a-z.'-]+){1,3})"
    for pattern in (
        rf"{name}\s*(?:,|–|—|-|\|)\s*{role_words}",
        rf"{role_words}\s*(?:,|:|–|—|-|\|)?\s*{name}",
    ):
        m = re.search(pattern, context or "")
        if m:
            # One pattern captures the role first; select the group that looks
            # like a multi-word capitalized name.
            for value in m.groups():
                if value and re.fullmatch(name, value):
                    return value
    parts = [p for p in re.split(r"[._-]", local) if p.isalpha() and len(p) > 1]
    if len(parts) >= 2:
        return " ".join(p.capitalize() for p in parts[:3])
    if len(parts) == 1 and local.lower() not in set(_LOCAL_PRIORITY) | _BAD_LOCAL:
        # Public startup addresses are often first-name-only (kim@...). This
        # is a modest inference, so keep it to alphabetic non-generic locals.
        return parts[0].capitalize()
    return ""


# A crawl re-runs contact extraction over *every* accumulated page each time it
# refreshes (up to four times, and once per loop iteration in fast mode), so
# page k was parsed k times. This memoizes the per-page parse for the life of
# one crawl. Keyed on the html itself as well as the URL so a re-fetch that
# changed the body can never be served a stale tree.
_PAGE_PARSE_CACHE_MAX = 128


def _parsed_page(page_url: str, html: str,
                 cache: Optional[Dict] = None) -> Tuple[object, str, List[str]]:
    """(soup, page_text, emails) for one page, parsed at most once per crawl.

    Safe to share: every consumer below treats the tree as read-only — it is
    only ever walked (`find_all`, `get_text`, `.parent`, `.previous_siblings`).
    The dicts built from it are new objects on each call, so callers that
    mutate a candidate cannot reach back into the cache.
    """
    key = (page_url, hash(html))
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    soup = BeautifulSoup(html_lib.unescape(html), "html.parser")
    entry = (soup, soup.get_text(" ", strip=True), extract_emails_from_html(html))
    if cache is not None:
        if len(cache) >= _PAGE_PARSE_CACHE_MAX:
            cache.clear()   # only costs a re-parse; never returns a wrong answer
        cache[key] = entry
    return entry


def extract_contact_candidates(
        pages: List[Dict[str, str]], company_domain: Optional[str],
        preferred_school: Optional[str] = None,
        preferred_affiliations: Optional[str] = None,
        cache: Optional[Dict] = None) -> List[Dict]:
    """Build evidence-backed people from emails and first-party profile links.

    Pass `cache` (any dict, owned by the caller and scoped to one crawl) to
    reuse the per-page parse across repeated calls. See `_parsed_page`.
    """
    merged: Dict[str, Dict] = {}
    for page in pages:
        html = page.get("html") or ""
        page_url = page.get("url") or ""
        soup, page_text, page_emails = _parsed_page(page_url, html, cache)
        for addr in page_emails:
            local, _, domain = addr.lower().partition("@")
            contexts = []
            origins = list(soup.find_all(
                href=re.compile(re.escape(addr), re.I)))
            origins += [
                text_node.parent
                for text_node in soup.find_all(string=re.compile(re.escape(addr), re.I))
            ]
            for origin in origins:
                node = origin
                for _ in range(4):
                    if node is None or getattr(node, "name", None) in {
                            "body", "html", "main", "[document]"}:
                        break
                    candidate = node.get_text(" ", strip=True)
                    if candidate:
                        contexts.append(candidate)
                    # Some team pages put a bio paragraph immediately before
                    # its email link without a wrapping card/section. Include
                    # only nearby siblings and stop at another address, so one
                    # leader's education does not leak onto every contact.
                    nearby = []
                    for sibling in list(node.previous_siblings)[:3]:
                        sibling_html = str(sibling)
                        if extract_emails_from_html(sibling_html):
                            break
                        sibling_text = (sibling.get_text(" ", strip=True)
                                        if hasattr(sibling, "get_text")
                                        else str(sibling).strip())
                        if sibling_text:
                            nearby.append(sibling_text)
                    if nearby and candidate:
                        contexts.append(" ".join(reversed(nearby)) + " " + candidate)
                    node = node.parent
            if not contexts:
                # Obfuscated addresses no longer appear literally in the DOM.
                # The page-level text is still grounded evidence, just weaker.
                contexts = [page_text[:1200]]
            context = max(
                (c for c in contexts if c),
                key=lambda c: (
                    1 if _school_match(c, preferred_school) else 0,
                    1 if _infer_role(c, local)[1] < 20 else 0,
                    min(len(c), 1200),
                ),
                default=page_text[:1200],
            )[:1200]
            role, seniority = _infer_role(context, local)
            name = _infer_name(context, local)
            # Track whether the displayed name was invented from the local-part
            # so verification cannot circularly "confirm" that address.
            name_from_email = bool(name) and not _infer_name(context, "")
            candidate = {
                "email": addr,
                "linkedin_url": None,
                "name": name,
                "name_from_email": name_from_email,
                "role": role,
                **_provenance(context, page_url, seniority,
                              preferred_school, preferred_affiliations),
                "on_domain": bool(
                    company_domain and registered_domain(domain) == company_domain),
            }
            old = merged.get(addr.lower())
            if old is None or (
                    candidate["school_match"], -candidate["seniority_rank"],
                    bool(candidate["name"])) > (
                    old["school_match"], -old["seniority_rank"], bool(old["name"])):
                merged[addr.lower()] = candidate

        # A company-owned team page may identify a real leader and link to
        # their public profile without publishing an email. Preserve that
        # useful, auditable lead without crawling the LinkedIn destination.
        for anchor in soup.find_all("a", href=True):
            raw_href = anchor.get("href") or ""
            linkedin_url = _linkedin_profile_url(raw_href)
            if not linkedin_url:
                continue
            contexts = []
            node = anchor
            for _ in range(5):
                if node is None or getattr(node, "name", None) in {
                        "body", "html", "main", "[document]"}:
                    break
                value = node.get_text(" ", strip=True)
                if value:
                    contexts.append(value)
                node = node.parent
            # Flat team pages often put the LinkedIn icon as a sibling of the
            # bio, not inside it. Pull nearby sibling text before giving up.
            nearby = []
            for sibling in list(anchor.previous_siblings)[:5]:
                sibling_text = (sibling.get_text(" ", strip=True)
                                if hasattr(sibling, "get_text")
                                else str(sibling).strip())
                if sibling_text:
                    nearby.append(sibling_text)
            if nearby:
                contexts.append(" ".join(reversed(nearby)))
            parent = anchor.parent
            if parent is not None:
                for sibling in list(parent.previous_siblings)[:3]:
                    sibling_text = (sibling.get_text(" ", strip=True)
                                    if hasattr(sibling, "get_text")
                                    else str(sibling).strip())
                    if sibling_text:
                        contexts.append(sibling_text)
            context = min(
                (c for c in contexts if c and c.lower() not in {
                    "linkedin", "in", "profile", "connect"}),
                key=lambda c: abs(len(c) - 300),
                default="",
            )[:1200]
            if not context:
                context = page_text[:800]
            role, seniority = _infer_role(context, "")
            name = _infer_name(context, "")
            anchor_name = anchor.get_text(" ", strip=True)
            if not name and re.fullmatch(
                    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}",
                    anchor_name):
                name = anchor_name
            # LinkedIn-only contacts need a real name so the /in/ slug can be
            # checked against that person (not a role label alone).
            if not name:
                continue
            linkedin_url = _linkedin_profile_url(raw_href, person_name=name)
            if not linkedin_url:
                continue
            person = {
                "email": "",
                "linkedin_url": linkedin_url,
                "name": name,
                "role": role,
                **_provenance(context, page_url, seniority,
                              preferred_school, preferred_affiliations),
                "on_domain": True,
            }
            same_name = next((
                existing for existing in merged.values()
                if name and existing.get("name")
                and existing["name"].strip().lower() == name.strip().lower()
            ), None)
            if same_name:
                # Only attach LinkedIn when the slug matches this contact's name.
                if linkedin_matches_person(linkedin_url, same_name.get("name") or name):
                    same_name["linkedin_url"] = linkedin_url
                same_name["affinity"] = list(dict.fromkeys(
                    (same_name.get("affinity") or []) + person["affinity"]))
                if person["school_match"]:
                    same_name["school_match"] = True
                    same_name["school"] = preferred_school
            else:
                merged[f"linkedin:{linkedin_url.lower()}"] = person

        # Final pass: attach /in/ links whose slug matches an email contact's
        # name even when DOM proximity was weak.
        page_linkedin = []
        for anchor in soup.find_all("a", href=True):
            url = _linkedin_profile_url(anchor.get("href") or "")
            if url:
                page_linkedin.append(url)
        for contact in list(merged.values()):
            if contact.get("linkedin_url") or not contact.get("name"):
                continue
            for url in page_linkedin:
                if linkedin_matches_person(url, contact["name"]):
                    contact["linkedin_url"] = url
                    break

    return sorted(
        merged.values(),
        key=lambda c: (
            0 if c["school_match"] else 1,
            c["seniority_rank"],
            0 if c["on_domain"] else 1,
            0 if c["name"] else 1,
            1 if is_generic_inbox((c.get("email") or "").split("@", 1)[0]) else 0,
            (c.get("email") or c.get("linkedin_url") or "").lower(),
        ),
    )


def select_outreach_contacts(candidates: List[Dict], emails: List[str],
                             company_domain: Optional[str],
                             limit: int = 5,
                             person_only: bool = True,
                             check_mx: bool = True) -> List[Dict]:
    """Prefer verified people (personal email / matching LinkedIn).

    When person_only is True (default), company inboxes like hello@ / info@
    are not selected — they are not a specific contact.
    """
    by_email = {c.get("email", "").lower(): dict(c)
                for c in candidates or [] if c.get("email")}
    ranked = rank_outreach_emails(emails, company_domain)
    on_domain = [
        addr for addr in ranked
        if company_domain
        and registered_domain(addr.split("@", 1)[-1]) == company_domain
    ]
    safe_pool = on_domain if on_domain else ranked[:1]
    warning = None
    if safe_pool and not on_domain:
        warning = (
            f"Found on the site but not on {company_domain}. Verify this reaches "
            f"the right company before sending." if company_domain else
            "Company domain unknown — verify this address before sending."
        )
    pool: List[Dict] = []
    for addr in safe_pool:
        candidate = by_email.get(addr.lower(), {
            "email": addr, "name": "", "role": None, "source_url": None,
            "school_match": False, "school": None, "linkedin_url": None,
            "seniority_rank": 20, "on_domain": bool(company_domain),
            "affinity": [],
        })
        candidate["warning"] = warning
        pool.append(candidate)

    selected_keys = {
        ((c.get("email") or "").lower(), (c.get("linkedin_url") or "").lower())
        for c in pool
    }
    for c in candidates or []:
        if not c.get("linkedin_url"):
            continue
        key = ((c.get("email") or "").lower(), (c.get("linkedin_url") or "").lower())
        if key in selected_keys:
            continue
        # Merge LinkedIn onto an email row with the same person name when possible
        same = next((
            p for p in pool
            if c.get("name") and p.get("name")
            and p["name"].strip().lower() == c["name"].strip().lower()
        ), None)
        if same and linkedin_matches_person(c["linkedin_url"], same.get("name")):
            same["linkedin_url"] = c["linkedin_url"]
            continue
        if c.get("email") and key[0] in by_email:
            continue
        pool.append(dict(c))
        selected_keys.add(key)

    if person_only:
        verified = select_verified_person_contacts(
            pool, limit=limit, require_person=True, check_mx=check_mx)
        return verified

    annotated = [annotate_contact(c, check_mx=check_mx) for c in pool]
    annotated.sort(key=lambda c: (
        0 if c.get("school_match") else 1,
        c.get("seniority_rank", 20),
        0 if c.get("name") else 1,
    ))
    return annotated[:limit]


def heuristic_metadata(company_name: str, text: str) -> Dict[str, Optional[str]]:
    """No-LLM fallback: first meaningful sentences become the summary."""
    if not text:
        return {}
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    meaningful = [s.strip() for s in sentences if len(s.strip()) > 40][:3]
    summary = " ".join(meaningful)[:400] if meaningful else None
    return {"summary": summary, "industry": None, "product": None,
            "hook": None, "why_care": None, "recent_news": None}


def llm_metadata(company_name: str, text: str,
                 known_emails: Optional[List[str]] = None,
                 preferred_school: Optional[str] = None) -> Optional[Dict]:
    """Cloud-LLM structured extraction. Returns None when no provider/parse failure."""
    if not complete_json or not get_cloud_llm_provider():
        return None
    prompt = f"""Analyze this company's website text and extract structured facts.

Company name: {company_name}

Website text from multiple first-party pages:
{text[:12000]}

Known emails actually present in the supplied pages:
{", ".join(known_emails or []) or "none"}

Preferred school/affiliation: {preferred_school or "none"}

Return ONLY a JSON object with these keys (use null when the text doesn't say):
{{
  "summary": "1-2 sentence plain-English description of what the company does",
  "industry": "industry/sector, e.g. 'Fintech', 'AI/ML', 'Healthcare SaaS'",
  "product": "their main product or service, named if possible",
  "hook": "one specific, compelling detail usable to personalize a cold email",
  "why_care": "why a candidate or customer would find this company exciting",
  "recent_news": "recent launch/announcement if mentioned, else null",
  "location": "HQ city if mentioned, else null",
  "contacts": [
    {{
      "email": "must be one of the known emails above",
      "name": "person's full name if the page states it, else null",
      "role": "their role if the page states it, else null",
      "source_url": "the SOURCE URL containing the evidence",
      "evidence": "an exact short quote from that source supporting name/role/education"
    }}
  ]
}}"""
    data = complete_json(prompt, max_tokens=1024)
    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("summary", "industry", "product", "hook", "why_care",
                "recent_news", "location"):
        val = data.get(key)
        out[key] = str(val).strip() if val and str(val).strip().lower() != "null" else None
    known = {e.lower(): e for e in (known_emails or [])}
    normalized_text = re.sub(r"\s+", " ", text).lower()
    grounded_contacts = []
    for contact in data.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        email = str(contact.get("email") or "").strip().lower()
        evidence = re.sub(r"\s+", " ", str(contact.get("evidence") or "")).strip()
        source_url = str(contact.get("source_url") or "").strip()
        # The model may organize facts, but it may not invent an address,
        # biography, or source. Exact evidence grounding keeps this auditable.
        if email not in known or len(evidence) < 20:
            continue
        if evidence.lower() not in normalized_text:
            continue
        if source_url and source_url not in text:
            continue
        grounded_contacts.append({
            "email": known[email],
            "name": str(contact.get("name") or "").strip(),
            "role": str(contact.get("role") or "").strip() or None,
            "source_url": source_url or None,
            "evidence": evidence,
            "school_match": _school_match(evidence, preferred_school),
            "school": (preferred_school
                       if _school_match(evidence, preferred_school) else None),
        })
    out["_contacts"] = grounded_contacts
    return out if any(out.values()) else None


def scrape_status_for(enriched: dict) -> str:
    """Map an enrichment result onto the status the UI shows."""
    if not enriched.get("url"):
        return "no_website"
    if not enriched.get("identity_verified", True):
        return "wrong_site"      # found a site, but it isn't this company's
    if enriched.get("ok"):
        return "scraped"
    return "scrape_failed"


class _PagePrefetcher:
    """Warms upcoming crawl URLs on a small thread pool.

    Purely a latency optimisation: the crawl loop still pops, processes and
    discovers in exactly the order it always did — this only means the page it
    asks for is usually already in hand. A URL that gets warmed but never
    popped is wasted bandwidth, bounded by PREFETCH_DEPTH.
    """

    def __init__(self, scraper, workers: Optional[int] = None):
        # Read the module global at call time so tests (and future config) can
        # move it without the default arg having frozen the old value.
        workers = max(1, PREFETCH_WORKERS if workers is None else workers)
        self._scraper = scraper
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="reach-prefetch")
        self._inflight: Dict[str, object] = {}
        self._max_inflight = workers * 2

    def submit(self, urls: List[str]) -> None:
        for url in urls:
            if len(self._inflight) >= self._max_inflight:
                return
            if url in self._inflight:
                continue
            try:
                self._inflight[url] = self._pool.submit(
                    self._scraper.fetch_html, url)
            except RuntimeError:  # pool already shut down
                return

    def get(self, url: str) -> Optional[str]:
        future = self._inflight.pop(url, None)
        if future is None:
            return self._scraper.fetch_html(url)
        try:
            return future.result()
        except Exception:
            return None

    def close(self) -> None:
        for future in self._inflight.values():
            future.cancel()
        self._inflight.clear()
        self._pool.shutdown(wait=False)


class EnrichmentService:
    """Scrape + extract for a single company. Stateless besides the scraper session."""

    def __init__(self):
        self.scraper = WebScraper()

    def find_website(self, company_name: str) -> Optional[str]:
        """Find a company's website: DDG search first, then domain guessing.

        Search results are ranked, not taken first-come: the top hit for an
        obscure company is routinely an unrelated site, and accepting it
        fabricates a company profile that then flows into real emails.
        """
        # No local skip list: every entry it held is in
        # discovery.AGGREGATOR_DOMAINS, which is_junk_site() already rejects.
        # Worse, it matched by substring, so "x.com" silently discarded
        # netflix.com, matrix.com, equinix.com and citrix.com.
        try:
            from ddg_search import ddg_text_search
            from discovery import is_junk_site
            results = ddg_text_search(f"{company_name} company official website", max_results=6)
            usable = [
                (r.get("href") or r.get("url") or "") for r in results
                if (r.get("href") or r.get("url"))
                and not is_junk_site(r.get("href") or r.get("url"))
                and is_safe_public_url(r.get("href") or r.get("url"))
            ]
            # A domain that echoes the company name is the strong signal.
            for url in usable:
                if domain_matches_name(company_name, registered_domain(url)):
                    return url
            if usable:
                return usable[0]
        except Exception:
            pass
        # Guess domains and probe
        slug = re.sub(r"[^a-z0-9]+", "", company_name.lower())
        slug_dash = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
        candidates = []
        for s in dict.fromkeys([slug, slug_dash]):
            if s:
                candidates += [f"https://www.{s}.com", f"https://{s}.com",
                               f"https://{s}.io", f"https://{s}.ai", f"https://{s}.co"]
        for url in candidates:
            try:
                if not is_safe_public_url(url):
                    continue
                resp = self.scraper.session.head(url, timeout=4, allow_redirects=True)
                if resp.status_code < 400 and is_safe_public_url(str(resp.url)):
                    return str(resp.url)
            except Exception:
                continue
        return None

    def _school_research_pages(self, company_name: str, domain: Optional[str],
                               preferred_school: Optional[str]) -> List[str]:
        """Find first-party bios the site's own navigation may not expose.

        Search results are used only as a map back into the verified company
        domain. We never turn a search snippet or an off-domain profile into a
        contact or claim.
        """
        if not domain or not preferred_school:
            return []
        try:
            from ddg_search import ddg_text_search
            school_terms = " OR ".join(
                f'"{alias}"' for alias in _school_aliases(preferred_school)[:5])
            query = (
                f'site:{domain} "{company_name}" '
                f'({school_terms}) '
                f'(CEO OR founder OR leadership OR team)'
            )
            out = []
            for result in ddg_text_search(query, max_results=6):
                candidate = result.get("href") or result.get("url") or ""
                if (candidate and registered_domain(candidate) == domain
                        and is_safe_public_url(candidate)):
                    out.append(candidate.split("#", 1)[0].rstrip("/"))
            return list(dict.fromkeys(out))[:4]
        except Exception:
            return []

    @staticmethod
    def _research_quality(pages_scraped: int, summary: Optional[str],
                          contacts: List[Dict]) -> str:
        if summary and pages_scraped >= 4 and any(
                c.get("name") or c.get("role") for c in contacts):
            return "high"
        if summary and pages_scraped >= 2:
            return "medium"
        return "low"

    def enrich(self, company_name: str, url: Optional[str] = None,
               preferred_school: Optional[str] = None,
               preferred_affiliations: Optional[str] = None,
               mode: str = "full",
               deadline_sec: Optional[float] = None) -> Dict:
        """Enrich a company from its site.

        mode='full' (default): deeper crawl (more pages / attempts).
        mode='fast': stop once we have identity + an about summary +
        at least one verified person contact, within the fast deadline.
        mode='deep': interview-grade crawl (news/press/careers/policy).
        """
        mode_l = (mode or "full").lower()
        fast = mode_l == "fast"
        deep = mode_l == "deep"
        if deep:
            max_pages = DEEP_MAX_PAGES
            max_attempts = DEEP_MAX_ATTEMPTS
        elif fast:
            max_pages = FAST_MAX_PAGES
            max_attempts = FAST_MAX_ATTEMPTS
        else:
            max_pages = MAX_PAGES_FETCHED
            max_attempts = MAX_PAGE_ATTEMPTS
        budget = deadline_sec if deadline_sec is not None else (
            FAST_DEADLINE_SEC if fast else None)
        started = time.monotonic()

        if not url:
            url = self.find_website(company_name)
        result: Dict = {
            "url": url,
            "domain": registered_domain(url) if url else None,
            "emails": [],
            "contacts": [],
            "ok": False,
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "pages_attempted": 0,
            "pages_scraped": 0,
            "research_sources": [],
            "research_quality": "low",
            "enrich_mode": "deep" if deep else ("fast" if fast else "full"),
            "elapsed_sec": 0.0,
        }
        if not url:
            result["elapsed_sec"] = round(time.monotonic() - started, 2)
            return result

        # Always run with the aggressive same-domain delay; SSRF checks stay on.
        delay_cm = None
        if hasattr(self.scraper, "delay_override"):
            delay_cm = self.scraper.delay_override(FAST_MIN_DELAY)
            delay_cm.__enter__()
        # One Chromium for the whole crawl instead of one per JS-shell page.
        browser_cm = None
        if hasattr(self.scraper, "browser_session"):
            browser_cm = self.scraper.browser_session()
            browser_cm.__enter__()
        # Fast mode early-exits too often for prefetching to pay off.
        prefetch = (None if fast or PREFETCH_WORKERS < 2
                    else _PagePrefetcher(self.scraper))
        # Scoped to this crawl and dropped with it, so nothing leaks between
        # companies. See _parsed_page for why sharing the tree is safe.
        page_cache: Dict = {}
        # Speculative /api/... guesses that answered 404 on this crawl's origin.
        if hasattr(self.scraper, "reset_dead_api_probes"):
            self.scraper.reset_dead_api_probes()

        def timed_out() -> bool:
            return budget is not None and (time.monotonic() - started) >= budget

        def _refresh_contacts_and_meta(page_records, texts, *, run_llm: bool,
                                        do_outreach: bool = False):
            combined = "\n\n".join(texts)[:24000]
            result["pages_attempted"] = attempted
            result["pages_scraped"] = len(page_records)
            result["research_sources"] = [p["url"] for p in page_records]
            verified = (domain_matches_name(company_name, result["domain"])
                        or page_mentions_company(company_name, combined))
            result["identity_verified"] = verified
            if not verified:
                result["ok"] = False
                result["mismatch"] = (
                    f"The page at {result['domain'] or url} never mentions "
                    f"{company_name}, so it is probably a different company.")
                result["emails"] = []
                result["contacts"] = []
                return False

            all_emails = []
            for page in page_records:
                all_emails.extend(
                    _parsed_page(page["url"], page["html"], page_cache)[2])
            result["emails"] = rank_outreach_emails(all_emails, result["domain"])[:12]
            contacts = extract_contact_candidates(
                page_records, result["domain"], preferred_school,
                preferred_affiliations, cache=page_cache)
            if combined:
                meta = None
                llm_contacts = []
                if run_llm and not timed_out():
                    meta = llm_metadata(
                        company_name, combined, result["emails"], preferred_school)
                    if meta:
                        llm_contacts = meta.pop("_contacts", []) or []
                meta = meta or heuristic_metadata(company_name, combined)
                result.update({k: v for k, v in (meta or {}).items()
                               if not str(k).startswith("_")})
                result["ok"] = bool((meta or {}).get("summary"))
                by_email = {
                    contact["email"].lower(): contact
                    for contact in contacts if contact.get("email")
                }
                for grounded in llm_contacts:
                    contact = by_email.get(grounded["email"].lower())
                    if not contact:
                        continue
                    if grounded.get("name"):
                        grounded_name = grounded["name"]
                        contact["name"] = grounded_name
                        # Only clear name_from_email when the model supplies an
                        # independent multi-token name (not "Kim" for kim@).
                        if len(name_tokens(grounded_name)) >= 2:
                            contact["name_from_email"] = False
                    if grounded.get("role"):
                        contact["role"] = grounded["role"]
                    if grounded.get("source_url"):
                        contact["source_url"] = grounded["source_url"]
                    if grounded.get("evidence"):
                        contact["evidence"] = grounded["evidence"]
                    if grounded.get("school_match"):
                        contact["school_match"] = True
                        contact["school"] = preferred_school
                    _, contact["seniority_rank"] = _infer_role(
                        f"{contact.get('role') or ''} {grounded.get('evidence') or ''}",
                        (contact.get("email") or "").split("@", 1)[0],
                    )
            else:
                result["ok"] = False

            # Always run MX when annotating — DNS is cheap vs an HTTP page and
            # "email_verified" must mean MX was actually confirmed.
            # LinkedIn/Hunter outreach enrichment runs once after the crawl
            # (see do_outreach) to avoid DDG rate-limit blowups mid-loop.
            result["contacts"] = [
                annotate_contact(c, check_mx=True)
                for c in contacts
            ]
            if do_outreach:
                result["contacts"] = enrich_contacts_outreach(
                    result["contacts"],
                    company_name=company_name,
                    domain=result.get("domain"),
                    check_mx=True,
                    max_linkedin_lookups=10 if deep else 3,
                    max_email_lookups=10 if deep else 3,
                )
            result["contacts"].sort(key=lambda c: (
                0 if c.get("person_verified") else 1,
                0 if c.get("linkedin_verified") else 1,
                0 if c.get("email_verified") else 1,
                0 if c.get("school_match") else 1,
                c.get("seniority_rank", 20),
                0 if c.get("on_domain") else 1,
                0 if c.get("name") else 1,
                (c.get("email") or c.get("linkedin_url") or "").lower(),
            ))
            result["research_quality"] = self._research_quality(
                result["pages_scraped"], result.get("summary"), result["contacts"])
            return True

        def _has_outreach_person(contacts_local=None) -> bool:
            """Person email or verified LinkedIn — either is outreach-ready."""
            for c in (contacts_local if contacts_local is not None
                      else result.get("contacts") or []):
                if not c.get("name") or c.get("name_from_email"):
                    continue
                if c.get("linkedin_verified"):
                    return True
                if (
                    c.get("email")
                    and c.get("email_kind") != "generic"
                    and c.get("on_domain")
                    and (
                        c.get("email_verified")
                        or (c.get("email_person_match")
                            and c.get("email_mx_ok") is True)
                    )
                ):
                    return True
            return False

        def _has_fast_success(page_records_local, queue_local=None) -> bool:
            about_ok = bool(result.get("summary"))
            dedicated_about = about_page_present(page_records_local)
            person = _has_outreach_person()
            pending_about = any(
                _ABOUT_PATH_RE.search((u or "").lower())
                for u in (queue_local or [])
            )
            # Homepage alone counts as the "about" site once identity + summary
            # exist. Pending guessed /about probes must not block early exit
            # when we already have a verified outreach person.
            about_page = dedicated_about or (
                about_ok and result.get("identity_verified")
                and (not pending_about or person)
            )
            return bool(
                result.get("identity_verified")
                and about_ok
                and about_page
                and person
            )

        try:
            domain = result["domain"]
            texts: List[str] = []
            page_records: List[Dict[str, str]] = []
            queue = [url]
            queued = {url.rstrip("/")}
            # Prefer about/team early so "what they do" + people land quickly.
            preferred_fallbacks = [
                "/about", "/about-us", "/company", "/team", "/leadership",
                "/people", "/contact", "/contact-us",
            ]
            deep_extra = [p for p in DEEP_SUBPAGES if p not in preferred_fallbacks]
            other_fallbacks = [p for p in SUBPAGES if p not in preferred_fallbacks]
            if deep:
                path_pool = preferred_fallbacks + deep_extra + other_fallbacks
            elif fast:
                path_pool = preferred_fallbacks
            else:
                path_pool = preferred_fallbacks + other_fallbacks
            # Preserve order, drop dupes
            path_pool = list(dict.fromkeys(path_pool))
            fallback_pages = [
                urljoin(url.rstrip("/") + "/", p.lstrip("/"))
                for p in path_pool
            ]
            attempted = 0
            fallback_failures = 0
            max_fast_fallback_failures = 2
            sitemap_seeded = False

            def seed_from_sitemap() -> None:
                """Put real people URLs ahead of the blind path guesses.

                Costs one request, and only when the crawl has run out of
                linked pages and is about to start guessing — so a site whose
                nav already led somewhere never pays for it.
                """
                nonlocal sitemap_seeded
                if sitemap_seeded or fast:
                    return
                if not hasattr(self.scraper, "fetch_sitemap_urls"):
                    return
                sitemap_seeded = True
                try:
                    found = self.scraper.fetch_sitemap_urls(url)
                except Exception:
                    return
                for candidate in reversed(rank_sitemap_pages(found, domain)):
                    if candidate.rstrip("/") in queued:
                        continue
                    fallback_pages.insert(0, candidate)
            while queue and attempted < max_attempts \
                    and len(page_records) < max_pages and not timed_out():
                page_url = queue.pop(0)
                attempted += 1
                # Warm the head of the queue for the *next* iterations. Done
                # after the pop so the current page is never queued twice.
                if prefetch is not None:
                    prefetch.submit(queue[:PREFETCH_DEPTH])
                was_fallback = page_url.rstrip("/") in {
                    f.rstrip("/") for f in fallback_pages
                } and not any(
                    p["url"].rstrip("/") == page_url.rstrip("/")
                    for p in page_records
                )
                html = (prefetch.get(page_url) if prefetch is not None
                        else self.scraper.fetch_html(page_url))
                if not html:
                    if was_fallback:
                        fallback_failures += 1
                    if not queue:
                        # In fast mode, stop guessing paths after a couple of
                        # 404s — homepage may already be enough.
                        if not (fast and (
                                fallback_failures >= max_fast_fallback_failures
                                or about_page_present(page_records))):
                            seed_from_sitemap()
                            for fallback in fallback_pages:
                                key = fallback.rstrip("/")
                                if key not in queued:
                                    if fast and fallback_failures >= max_fast_fallback_failures:
                                        break
                                    queued.add(key)
                                    queue.append(fallback)
                                    if fast:
                                        break  # one fallback at a time
                    continue
                text = self.scraper.extract_text(html)
                page_records.append({"url": page_url, "html": html, "text": text or ""})
                if text and len(text) > 80:
                    texts.append(f"SOURCE: {page_url}\n{text[:5000]}")

                discovered = discover_internal_links(html, page_url, domain)
                insert_at = 0
                for discovered_url in discovered:
                    key = discovered_url.rstrip("/")
                    if key not in queued:
                        queued.add(key)
                        queue.insert(insert_at, discovered_url)
                        insert_at += 1
                if not queue:
                    # Keep probing fallbacks until we hit the fast failure cap
                    # or already have person-email success. An /about page alone
                    # must not skip /team when contacts live there.
                    if page_records:
                        _refresh_contacts_and_meta(
                            page_records, texts, run_llm=False)
                    if not (fast and (
                            fallback_failures >= max_fast_fallback_failures
                            or _has_fast_success(page_records, []))):
                        seed_from_sitemap()
                        for fallback in fallback_pages:
                            key = fallback.rstrip("/")
                            if key not in queued:
                                queued.add(key)
                                queue.append(fallback)
                                if fast:
                                    break

                # Early exit once we have about signal + verified person from
                # the company site. One outreach pass still runs so Hunter can
                # fill emails for LinkedIn-only people before we stop crawling.
                if fast and page_records:
                    if _refresh_contacts_and_meta(
                            page_records, texts, run_llm=False) and _has_fast_success(
                                page_records, queue):
                        _refresh_contacts_and_meta(
                            page_records, texts, run_llm=False, do_outreach=True)
                        result["page_texts"] = texts[:24]
                        result["elapsed_sec"] = round(time.monotonic() - started, 2)
                        return result

            # Alumni bios are often orphaned from the current nav. Search may find
            # them, but only verified same-domain pages are fetched or trusted.
            if not fast and not timed_out():
                school_pages = self._school_research_pages(
                    company_name, domain, preferred_school)
                if school_pages and not any(
                        _school_match(p.get("text") or "", preferred_school)
                        for p in page_records):
                    for page_url in school_pages:
                        if attempted >= max_attempts or len(page_records) >= max_pages \
                                or timed_out():
                            break
                        if any(p["url"].rstrip("/") == page_url.rstrip("/")
                               for p in page_records):
                            continue
                        attempted += 1
                        html = self.scraper.fetch_html(page_url)
                        if not html:
                            continue
                        text = self.scraper.extract_text(html)
                        page_records.append(
                            {"url": page_url, "html": html, "text": text or ""})
                        if text and len(text) > 80:
                            texts.append(f"SOURCE: {page_url}\n{text[:5000]}")

            if page_records:
                # Deep mode always asks the LLM when a provider exists — heuristic
                # mid-crawl summaries must not skip interview-grade extraction.
                need_llm = deep or (
                    not result.get("summary") or not _has_fast_success(
                        page_records, [])
                )
                # One outreach pass after the crawl fills LinkedIn/Hunter gaps.
                _refresh_contacts_and_meta(
                    page_records, texts,
                    run_llm=need_llm and not timed_out(),
                    do_outreach=not timed_out(),
                )
                result["page_texts"] = texts[:24]
            else:
                result["pages_attempted"] = attempted
                result["identity_verified"] = False
                result["page_texts"] = []

            result["elapsed_sec"] = round(time.monotonic() - started, 2)
            return result
        finally:
            if prefetch is not None:
                prefetch.close()
            if browser_cm is not None:
                browser_cm.__exit__(None, None, None)
            if delay_cm is not None:
                delay_cm.__exit__(None, None, None)


_ABOUT_PATH_RE = re.compile(
    r"/(about|about-us|company|team|leadership|people|who-we-are)(/|$)",
    re.I,
)


def about_page_present(page_records: List[Dict[str, str]]) -> bool:
    """True when crawl already fetched a dedicated about/team-style page."""
    return any(
        _ABOUT_PATH_RE.search((p.get("url") or "").lower())
        for p in page_records
    )
