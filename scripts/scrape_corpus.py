"""Capture real company sites once, then replay them offline forever.

Why a mirror and not a request log: H7 (sitemap) and H8 (wider link vocabulary)
exist precisely to reach pages the *current* crawler never requests. Replaying a
log of what the current code fetched would return zero new pages for both — a
guaranteed null result that looks like "no benefit". So the capture deliberately
crawls broader than production: every same-domain link it can reach within the
budget, plus `/sitemap.xml`, `/robots.txt`, and the conventional paths the
crawler guesses.

    python scripts/scrape_corpus.py capture       # every site in scripts/corpus_sites.txt
    python scripts/scrape_corpus.py capture "Acme=https://acme.com" --archetype spa
    python scripts/scrape_corpus.py capture --dry-run     # print the plan, fetch nothing
    python scripts/scrape_corpus.py list
    python scripts/scrape_corpus.py survey        # JSON-LD / sitemap prevalence

Replay is not this module's job: `scripts/measure_shipped.py` reads each
captured `site.json` and feeds the stored bodies straight to
`enrichment.extract_*`. Measurement therefore never touches the network or the
SSRF guard — no localhost server, and no test-only bypass that could leak into
production.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

CORPUS_DIR = os.path.join(ROOT, "tests", "fixtures", "corpus")
# The curated site list `capture` falls back to: `archetype<TAB>Name=url` rows.
SITES_FILE = os.path.join(ROOT, "scripts", "corpus_sites.txt")

# Broader than the production crawler on purpose — see module docstring.
CAPTURE_PATHS = (
    "/about", "/about-us", "/company", "/team", "/leadership", "/people",
    "/our-team", "/our-people", "/who-we-are", "/management", "/staff",
    "/contact", "/contact-us", "/careers", "/news", "/press", "/blog",
    "/professionals", "/attorneys", "/board", "/board-of-directors",
    "/advisors", "/directors", "/executives", "/partners", "/bios",
    "/our-story", "/founders",
)
CAPTURE_DELAY_SEC = 0.6     # deliberately polite; this runs once per site
DEFAULT_MAX_PAGES = 40

# A page budget spent on stylesheets and practice-area marketing is a corpus
# that cannot answer the questions. Wilson Sonsini burned 5 slots on
# CSS/favicons and ~28 on /services/practice-areas/*, capturing zero attorney
# bios — which would make H8 (does better vocabulary reach contact-bearing
# pages?) unfalsifiable, because the pages simply would not be present.
_ASSET_EXT = (
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".mp4", ".webm",
    ".mp3", ".xml", ".rss", ".atom", ".webmanifest", ".map",
)
# Where people actually live. Matched against the whole URL path.
_PEOPLE_RE = re.compile(
    r"(people|team|attorney|professional|staff|leadership|lawyer|"
    r"bio|founder|partner|director|management|officer|advisor|board|"
    r"who-we-are|our-story|about)", re.I)
# Marketing surface area that crowds out the pages we need.
_DEMOTE_RE = re.compile(
    r"(practice-area|/services/|/solutions/|/products?/|/pricing|/insights?/|"
    r"/blog/|/news/|/press/|/events?/|/resources?/|/docs?/|/support|/legal|"
    r"/privacy|/terms|/cookie|/login|/signin|/signup|/search|/tag/|/category/)",
    re.I)


def _is_document(url: str) -> bool:
    """False for assets the production fetcher would refuse anyway."""
    path = (url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return not path.endswith(_ASSET_EXT)


def _link_rank(url: str, *, guessed: bool = False) -> int:
    """Lower sorts first. People pages win; marketing pages lose."""
    path = (url or "").split("?", 1)[0].lower()
    if _PEOPLE_RE.search(path):
        return 1 if _DEMOTE_RE.search(path) else 0
    if guessed:
        return 2
    if _DEMOTE_RE.search(path):
        return 4
    return 3


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "site"


def _norm(url: str) -> str:
    """Canonical key for a page: no fragment, no trailing slash."""
    return (url or "").split("#", 1)[0].rstrip("/")


class CorpusSite:
    """One captured site: a URL→response map plus its sitemap and robots."""

    def __init__(self, data: Dict):
        self.name = data.get("name") or ""
        self.url = data.get("url") or ""
        self.domain = data.get("domain") or ""
        self.archetype = data.get("archetype") or "unknown"
        self.captured_at = data.get("captured_at") or ""
        self.pages: Dict[str, Dict] = data.get("pages") or {}
        self.robots: Optional[str] = data.get("robots")
        self.sitemap: Optional[str] = data.get("sitemap")

    @classmethod
    def load(cls, slug: str) -> "CorpusSite":
        path = os.path.join(CORPUS_DIR, slug, "site.json")
        with open(path, "r", encoding="utf-8") as handle:
            return cls(json.load(handle))

    def save(self, slug: str) -> str:
        directory = os.path.join(CORPUS_DIR, slug)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "site.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({
                "name": self.name, "url": self.url, "domain": self.domain,
                "archetype": self.archetype, "captured_at": self.captured_at,
                "robots": self.robots, "sitemap": self.sitemap,
                "pages": self.pages,
            }, handle, indent=1)
        return path


# --- capture ------------------------------------------------------------

def capture_site(name: str, url: str, archetype: str = "unknown",
                 max_pages: int = DEFAULT_MAX_PAGES) -> CorpusSite:
    """Breadth-first mirror of one site. Runs once; polite by construction."""
    import httpx
    from enrichment import registered_domain
    from web_scraper import is_safe_public_url

    domain = registered_domain(url)
    site = CorpusSite({"name": name, "url": url, "domain": domain,
                       "archetype": archetype,
                       "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S")})

    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    client = httpx.Client(follow_redirects=True, timeout=httpx.Timeout(15.0),
                          headers=headers)

    def grab(target: str) -> Optional[str]:
        if not is_safe_public_url(target):
            return None
        time.sleep(CAPTURE_DELAY_SEC)
        try:
            response = client.get(target)
        except Exception as exc:
            print(f"    ! {target} -> {type(exc).__name__}")
            return None
        body = response.text or ""
        site.pages[_norm(target)] = {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "body": body[:2_000_000],
            # Retained because H4's whole size depends on whether real bot
            # walls actually send this header.
            "retry_after": response.headers.get("retry-after"),
            "server": response.headers.get("server", ""),
        }
        print(f"    {response.status_code} {target}")
        return body if response.status_code < 400 else None

    # robots.txt and sitemap.xml: fetched directly, because the production
    # fetcher rejects non-html/json content types outright.
    for extra, attr in (("/robots.txt", "robots"), ("/sitemap.xml", "sitemap")):
        target = urljoin(url.rstrip("/") + "/", extra.lstrip("/"))
        if is_safe_public_url(target):
            time.sleep(CAPTURE_DELAY_SEC)
            try:
                response = client.get(target)
                if response.status_code < 400:
                    setattr(site, attr, response.text[:2_000_000])
                    print(f"    {response.status_code} {target}")
            except Exception:
                pass

    seen = {_norm(url)}
    ranked: List[tuple] = []            # (rank, order, url)

    def offer(link: str, *, guessed: bool = False, bonus: int = 0):
        link = (link or "").split("#", 1)[0]
        if not link or _norm(link) in seen:
            return
        if registered_domain(link) != domain or not _is_document(link):
            return
        if not is_safe_public_url(link):
            return
        seen.add(_norm(link))
        ranked.append((_link_rank(link, guessed=guessed) + bonus,
                       len(ranked), link))

    def links_in(body: str, base: str) -> List[str]:
        out = []
        for match in re.finditer(r'href=["\']([^"\']+)["\']', body or ""):
            out.append(urljoin(base, match.group(1)))
        return out

    homepage = grab(url)
    for link in links_in(homepage or "", url):
        offer(link)
    for path in CAPTURE_PATHS:
        offer(urljoin(url.rstrip("/") + "/", path.lstrip("/")), guessed=True)

    # Depth 1, best-first. People/about pages are fetched before the marketing
    # surface, so a 40-page budget lands on pages that can carry contacts.
    people_bodies: List[tuple] = []
    ranked.sort()
    for _rank, _order, target in ranked:
        if len(site.pages) >= max_pages:
            break
        body = grab(target)
        if body and _PEOPLE_RE.search(target.split("?", 1)[0].lower()):
            people_bodies.append((target, body))

    # Depth 2, from people-ish pages only: this is where individual bios live
    # (/en/people/index.html -> /en/people/jane-doe.html). Without this the
    # corpus contains team *indexes* but no person pages, and every
    # contact-extraction hypothesis is measured against nothing.
    deeper: List[tuple] = []
    for base, body in people_bodies:
        before = len(ranked)
        for link in links_in(body, base):
            offer(link, bonus=-1)       # already people-adjacent; prefer these
        deeper.extend(ranked[before:])
    deeper.sort()
    for _rank, _order, target in deeper:
        if len(site.pages) >= max_pages:
            break
        grab(target)

    client.close()
    return site


# --- survey (answers H9 and H7 prevalence for free) ----------------------

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)


def survey_site(site: CorpusSite) -> Dict:
    """Count what the quality hypotheses depend on, without implementing them."""
    types: Dict[str, int] = {}
    tiers = {"named": 0, "with_role": 0, "with_linkedin": 0,
             "with_email": 0, "strict": 0}
    for page in site.pages.values():
        for block in _JSONLD_RE.findall(page.get("body") or ""):
            try:
                payload = json.loads(block)
            except (ValueError, TypeError):
                continue
            for node in _walk(payload):
                # @type is legitimately a list in schema.org (e.g.
                # ["Person","Employee"]). str()-ing it produced 270 nodes
                # surveyed as empty-typed on wsgr.com and would have hidden
                # every Person behind a multi-type annotation.
                raw_type = node.get("@type")
                node_types = ([str(t) for t in raw_type]
                              if isinstance(raw_type, list)
                              else [str(raw_type or "")])
                for node_type in node_types:
                    types[node_type] = types.get(node_type, 0) + 1
                if "Person" not in node_types:
                    continue
                for key, present in _person_tiers(node).items():
                    if present:
                        tiers[key] += 1
    walled = [p for p in site.pages.values() if p.get("status") in (403, 429, 503)]
    return {
        "name": site.name,
        "archetype": site.archetype,
        "pages": len(site.pages),
        "has_sitemap": bool(site.sitemap),
        "sitemap_urls": len(re.findall(r"<loc>", site.sitemap or "")),
        "robots_sitemap_ref": bool(re.search(r"(?im)^\s*sitemap:", site.robots or "")),
        "jsonld_types": types,
        "jsonld_people": tiers,
        "blocked_pages": len(walled),
        "blocked_with_retry_after": sum(1 for p in walled if p.get("retry_after")),
    }


def _walk(payload):
    if isinstance(payload, dict):
        if "@graph" in payload:
            for item in _walk(payload["@graph"]):
                yield item
        yield payload
        for value in payload.values():
            if isinstance(value, (dict, list)):
                for item in _walk(value):
                    yield item
    elif isinstance(payload, list):
        for entry in payload:
            for item in _walk(entry):
                yield item


def _person_tiers(node: Dict) -> Dict[str, bool]:
    """Which JSON-LD Person signals are present, by what they would repair.

    Deliberately NOT one boolean. The value of JSON-LD is not "it carries an
    email" — the email is already extracted today by the raw-HTML regex. The
    value is the *person structure*: a bare `name` alone repairs a contact whose
    name was rebuilt from the email local-part (`j.okafor@` -> "Okafor"), which
    sets `name_from_email=True` and disqualifies it from `_has_outreach_person`
    and from LinkedIn/Hunter lookups. Scoring only the strict
    name+title+contact tier reports 0 and refutes the hypothesis for the wrong
    reason.
    """
    name = str(node.get("name") or "").strip()
    if not name or len(name.split()) < 2:
        # Single-token names cannot repair name_from_email — that check needs
        # >= 2 tokens to clear the flag (see enrichment.py:1075).
        return {}
    same_as = node.get("sameAs") or []
    if isinstance(same_as, str):
        same_as = [same_as]
    has_linkedin = any("linkedin.com" in str(s).lower() for s in same_as)
    return {
        "named": True,                                   # repairs name_from_email
        "with_role": bool(node.get("jobTitle")),         # repairs role/seniority
        "with_linkedin": has_linkedin,                   # fills linkedin_url
        "with_email": bool(node.get("email")),           # already extracted today
        "strict": bool(node.get("jobTitle")) and (
            bool(node.get("email")) or has_linkedin),
    }


# --- cli ----------------------------------------------------------------

def load_site_list(path: str = SITES_FILE) -> List[tuple]:
    """Parse the curated corpus list into (archetype, "Name=url") pairs."""
    rows: List[tuple] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            archetype, _, target = line.partition("\t")
            if not target:
                raise SystemExit(
                    f"{path}: expected 'archetype<TAB>Name=url', got {line!r}")
            rows.append((archetype.strip(), target.strip()))
    return rows


def list_slugs() -> List[str]:
    if not os.path.isdir(CORPUS_DIR):
        return []
    return sorted(d for d in os.listdir(CORPUS_DIR)
                  if os.path.isfile(os.path.join(CORPUS_DIR, d, "site.json")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="mirror one or more sites")
    cap.add_argument("targets", nargs="*",
                     help="Company=https://company.com; default: every site "
                          "in scripts/corpus_sites.txt")
    cap.add_argument("--archetype", default="unknown",
                     help="archetype for command-line targets; sites from the "
                          "list carry their own")
    cap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    cap.add_argument("--dry-run", action="store_true",
                     help="print the capture plan and exit, fetching nothing")

    sub.add_parser("list", help="show captured sites")
    sub.add_parser("survey", help="JSON-LD / sitemap / bot-wall prevalence")

    args = parser.parse_args()

    if args.command == "capture":
        if args.targets:
            plan = [(args.archetype, t) for t in args.targets]
            source = "command line"
        else:
            plan = load_site_list()
            source = os.path.relpath(SITES_FILE, ROOT)
        # Printed before the first request so a mistyped command cannot start
        # an unattended crawl of third-party servers without saying so.
        print(f"plan: {len(plan)} site(s) from {source}, <= {args.max_pages} "
              f"pages each at {CAPTURE_DELAY_SEC}s/request "
              f"(the full list is ~300MB, one polite pass)")
        if args.dry_run:
            for archetype, target in plan:
                print(f"  [{archetype}] {target}")
            return
        for archetype, target in plan:
            if "=" not in target:
                raise SystemExit("targets look like Company=https://company.com")
            name, url = target.split("=", 1)
            print(f"capturing {name.strip()} ({url.strip()}) [{archetype}]")
            site = capture_site(name.strip(), url.strip(),
                                archetype, args.max_pages)
            path = site.save(slugify(name))
            print(f"  -> {len(site.pages)} pages, {path}")
        return

    if args.command == "list":
        for slug in list_slugs():
            site = CorpusSite.load(slug)
            print(f"  {slug:24} {len(site.pages):3} pages  "
                  f"[{site.archetype}]  {site.url}")
        return

    if args.command == "survey":
        rows = [survey_site(CorpusSite.load(slug)) for slug in list_slugs()]
        print(json.dumps({
            "sites": rows,
            "totals": {
                "sites": len(rows),
                "with_sitemap": sum(1 for r in rows if r["has_sitemap"]),
                # Reported per tier: a site with named Persons but no embedded
                # email still gains outreach-ready contacts under H9.
                "sites_with_named_jsonld_person":
                    sum(1 for r in rows if r["jsonld_people"]["named"]),
                "sites_with_jsonld_linkedin":
                    sum(1 for r in rows if r["jsonld_people"]["with_linkedin"]),
                "sites_with_strict_jsonld_person":
                    sum(1 for r in rows if r["jsonld_people"]["strict"]),
                "blocked_pages":
                    sum(r["blocked_pages"] for r in rows),
                "blocked_with_retry_after":
                    sum(r["blocked_with_retry_after"] for r in rows),
            },
        }, indent=2))


if __name__ == "__main__":
    main()
