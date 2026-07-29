"""
Company enrichment: scrape a company's site, extract structured metadata
(cloud LLM first, heuristic fallback) and contact email addresses.
"""
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from web_scraper import WebScraper, is_safe_public_url

try:
    from llm_client import complete_json, get_cloud_llm_provider
except ImportError:
    complete_json = None
    get_cloud_llm_provider = lambda: None

# Pages likely to contain company info / contact emails
SUBPAGES = ["/about", "/about-us", "/company", "/contact", "/contact-us", "/team", "/careers"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Emails we never want to cold-email
_BAD_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
              "postmaster", "abuse", "privacy", "legal", "unsubscribe", "spam"}
_BAD_DOMAINS = {"example.com", "sentry.io", "wixpress.com", "sentry.wixpress.com",
                "godaddy.com", "domain.com", "yourdomain.com", "email.com",
                "company.com", "sentry-next.wixpress.com", "mysite.com"}
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

# Preference order for picking the best outreach address
_LOCAL_PRIORITY = [
    "founders", "founder", "ceo", "team", "hello", "hi", "hey", "careers",
    "jobs", "recruiting", "talent", "contact", "info", "press", "sales",
    "support", "admin", "office",
]


def registered_domain(url_or_domain: str) -> Optional[str]:
    """'https://www.foo.co.uk/x' -> 'foo.co.uk' (best effort, no PSL dep)."""
    if not url_or_domain:
        return None
    s = url_or_domain.strip().lower()
    if "//" in s:
        s = urlparse(s).netloc or s.split("//", 1)[-1].split("/", 1)[0]
    s = s.split("@")[-1].split(":")[0]
    if s.startswith("www."):
        s = s[4:]
    parts = [p for p in s.split(".") if p]
    if len(parts) < 2:
        return s or None
    # Handle common two-part TLDs
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"} \
            and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# Words that carry no identifying signal when matching a name to a domain.
_NAME_STOPWORDS = {"inc", "llc", "ltd", "corp", "corporation", "company", "co",
                   "the", "group", "labs", "lab", "technologies", "technology",
                   "tech", "systems", "solutions", "software", "ai", "io"}


def _name_tokens(name: str) -> List[str]:
    tokens = re.split(r"[^a-z0-9]+", (name or "").lower())
    return [t for t in tokens if t and t not in _NAME_STOPWORDS]


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


def extract_emails_from_html(html: str) -> List[str]:
    """All plausible email addresses in a page (mailto links + visible text)."""
    if not html:
        return []
    found = []
    seen = set()
    # mailto: links first — highest signal
    for m in re.finditer(r'mailto:([^"\'\s?>]+)', html, re.IGNORECASE):
        addr = m.group(1).strip().rstrip(".,;")
        if EMAIL_RE.fullmatch(addr) and addr.lower() not in seen:
            seen.add(addr.lower())
            found.append(addr)
    # then any email-looking text
    for m in EMAIL_RE.finditer(html):
        addr = m.group(0).strip().rstrip(".,;")
        if addr.lower() not in seen:
            seen.add(addr.lower())
            found.append(addr)
    return [a for a in found if _is_valid_outreach_email(a)]


def _is_valid_outreach_email(addr: str) -> bool:
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
    if len(addr) > 80 or addr.count("@") != 1:
        return False
    return True


def rank_outreach_emails(emails: List[str], company_domain: Optional[str]) -> List[str]:
    """Sort candidate emails: company-domain first, then by local-part priority."""
    def score(addr: str) -> Tuple[int, int, str]:
        local, _, domain = addr.lower().partition("@")
        domain_score = 0 if (company_domain and registered_domain(domain) == company_domain) else 1
        try:
            local_score = _LOCAL_PRIORITY.index(local)
        except ValueError:
            # Named person addresses (jane@, jsmith@) rank above generic unknown
            local_score = len(_LOCAL_PRIORITY) if "." in local or len(local) <= 12 else len(_LOCAL_PRIORITY) + 5
        return (domain_score, local_score, addr)
    return sorted(dict.fromkeys(emails), key=score)


def select_outreach_emails(emails: List[str], company_domain: Optional[str],
                           limit: int = 2) -> List[Tuple[str, Optional[str]]]:
    """Pick which scraped addresses actually become contacts.

    A company page routinely lists other companies' addresses (vendors,
    agencies, partners, an acquirer). Emailing those pitches the wrong
    company, so prefer the company's own domain and only fall back to
    off-domain addresses when the site exposed none — flagged, so the UI can
    warn before the user sends.
    Returns [(email, note_or_None)].
    """
    ranked = rank_outreach_emails(emails, company_domain)
    if not ranked:
        return []
    if company_domain:
        on_domain = [e for e in ranked
                     if registered_domain(e.split("@")[-1]) == company_domain]
        if on_domain:
            return [(e, None) for e in on_domain[:limit]]
    note = (f"Found on the site but not on {company_domain}. Verify this reaches "
            f"the right company before sending." if company_domain else
            "Company domain unknown — verify this address before sending.")
    return [(e, note) for e in ranked[:1]]


def heuristic_metadata(company_name: str, text: str) -> Dict[str, Optional[str]]:
    """No-LLM fallback: first meaningful sentences become the summary."""
    if not text:
        return {}
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    meaningful = [s.strip() for s in sentences if len(s.strip()) > 40][:3]
    summary = " ".join(meaningful)[:400] if meaningful else None
    return {"summary": summary, "industry": None, "product": None,
            "hook": None, "why_care": None, "recent_news": None}


def llm_metadata(company_name: str, text: str) -> Optional[Dict[str, Optional[str]]]:
    """Cloud-LLM structured extraction. Returns None when no provider/parse failure."""
    if not complete_json or not get_cloud_llm_provider():
        return None
    prompt = f"""Analyze this company's website text and extract structured facts.

Company name: {company_name}

Website text:
{text[:6000]}

Return ONLY a JSON object with these keys (use null when the text doesn't say):
{{
  "summary": "1-2 sentence plain-English description of what the company does",
  "industry": "industry/sector, e.g. 'Fintech', 'AI/ML', 'Healthcare SaaS'",
  "product": "their main product or service, named if possible",
  "hook": "one specific, compelling detail usable to personalize a cold email",
  "why_care": "why a candidate or customer would find this company exciting",
  "recent_news": "recent launch/announcement if mentioned, else null",
  "location": "HQ city if mentioned, else null"
}}"""
    data = complete_json(prompt, max_tokens=1024)
    if not isinstance(data, dict):
        return None
    out = {}
    for key in ("summary", "industry", "product", "hook", "why_care",
                "recent_news", "location"):
        val = data.get(key)
        out[key] = str(val).strip() if val and str(val).strip().lower() != "null" else None
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
        skip = ["linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
                "indeed.com", "glassdoor.com", "crunchbase.com", "wikipedia.org",
                "bloomberg.com", "yelp.com", "youtube.com", "github.com", "medium.com",
                "angel.co", "wellfound.com", "pitchbook.com", "zoominfo.com",
                "apollo.io", "owler.com", "cbinsights.com", "reddit.com"]
        try:
            from ddg_search import ddg_text_search
            from discovery import is_junk_site
            results = ddg_text_search(f"{company_name} company official website", max_results=6)
            usable = [
                (r.get("href") or r.get("url") or "") for r in results
                if (r.get("href") or r.get("url"))
                and not any(s in (r.get("href") or r.get("url") or "").lower() for s in skip)
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

    def enrich(self, company_name: str, url: Optional[str] = None) -> Dict:
        """Full enrichment: returns dict with url, domain, metadata fields,
        emails (ranked), text_sample, and ok flag."""
        if not url:
            url = self.find_website(company_name)
        result: Dict = {
            "url": url,
            "domain": registered_domain(url) if url else None,
            "emails": [],
            "ok": False,
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
        }
        if not url:
            return result

        texts: List[str] = []
        emails: List[str] = []
        pages = [url] + [urljoin(url.rstrip("/") + "/", p.lstrip("/")) for p in SUBPAGES]
        fetched = 0
        for page_url in pages:
            if fetched >= 4 and (texts and emails):
                break  # enough signal; don't hammer the site
            html = self.scraper.fetch_html(page_url)
            if not html:
                continue
            fetched += 1
            emails += extract_emails_from_html(html)
            text = self.scraper.extract_text(html)
            if text and len(text) > 100:
                texts.append(text)
            if fetched >= 6:
                break

        combined = "\n\n".join(texts)[:12000]

        # Confirm this site is actually the company's before believing anything
        # on it. A search result can easily be an unrelated business; scraping
        # it produces a confident, entirely fabricated profile that would then
        # be quoted back to a real person in a cold email.
        verified = (domain_matches_name(company_name, result["domain"])
                    or page_mentions_company(company_name, combined))
        result["identity_verified"] = verified
        if not verified:
            result["ok"] = False
            result["mismatch"] = (
                f"The page at {result['domain'] or url} never mentions "
                f"{company_name}, so it is probably a different company.")
            # Emails from an unrelated domain belong to someone else entirely.
            result["emails"] = []
            return result

        result["emails"] = rank_outreach_emails(emails, result["domain"])[:8]
        if combined:
            meta = llm_metadata(company_name, combined) or heuristic_metadata(company_name, combined)
            result.update({k: v for k, v in (meta or {}).items()})
            result["ok"] = bool((meta or {}).get("summary"))
        return result
