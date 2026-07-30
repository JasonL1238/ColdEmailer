"""
Company enrichment: scrape a company's site, extract structured metadata
(cloud LLM first, heuristic fallback) and contact email addresses.
"""
import html as html_lib
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

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

MAX_PAGES_FETCHED = 8
MAX_PAGE_ATTEMPTS = 16

_LINK_PRIORITY = {
    "leadership": 0, "team": 0, "people": 0, "founder": 0, "management": 0,
    "about": 1, "company": 1, "who-we-are": 1,
    "contact": 2, "careers": 2, "jobs": 2,
    "press": 3, "news": 3, "blog": 4, "insights": 4,
}
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
    """All plausible addresses, including common public obfuscation schemes."""
    if not html:
        return []
    found = []
    seen = set()
    decoded = html_lib.unescape(html)

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
    for addr in found:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
        if _is_valid_outreach_email(addr) and key not in {a.lower() for a in out}:
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


def _linkedin_profile_url(value: str) -> Optional[str]:
    """Normalize a LinkedIn member URL already linked from a first-party page.

    We record the link but never fetch LinkedIn itself.
    """
    try:
        parsed = urlparse((value or "").strip())
    except ValueError:
        return None
    if parsed.scheme != "https":
        return None
    if (parsed.hostname or "").lower() not in {"linkedin.com", "www.linkedin.com"}:
        return None
    if not parsed.path.lower().startswith("/in/"):
        return None
    return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


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


def extract_contact_candidates(
        pages: List[Dict[str, str]], company_domain: Optional[str],
        preferred_school: Optional[str] = None,
        preferred_affiliations: Optional[str] = None) -> List[Dict]:
    """Build evidence-backed people from emails and first-party profile links."""
    merged: Dict[str, Dict] = {}
    for page in pages:
        html = page.get("html") or ""
        page_url = page.get("url") or ""
        soup = BeautifulSoup(html_lib.unescape(html), "html.parser")
        page_text = soup.get_text(" ", strip=True)
        page_emails = extract_emails_from_html(html)
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
            school_match = _school_match(context, preferred_school)
            affinities = _affinity_matches(
                context, preferred_school, preferred_affiliations)
            candidate = {
                "email": addr,
                "linkedin_url": None,
                "name": name,
                "role": role,
                "source_url": page_url,
                "evidence": context[:500],
                "school_match": school_match,
                "school": preferred_school if school_match else None,
                "affinity": affinities,
                "seniority_rank": seniority,
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
            linkedin_url = _linkedin_profile_url(anchor.get("href") or "")
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
            context = min(
                (c for c in contexts if c), key=lambda c: abs(len(c) - 300),
                default=anchor.get_text(" ", strip=True),
            )[:1200]
            role, seniority = _infer_role(context, "")
            name = _infer_name(context, "")
            anchor_name = anchor.get_text(" ", strip=True)
            if not name and re.fullmatch(
                    r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}",
                    anchor_name):
                name = anchor_name
            # A /in/ link proves a person exists, but keep only links the
            # company page associates with a name or a target decision role.
            if not name and not role:
                continue
            school_match = _school_match(context, preferred_school)
            affinities = _affinity_matches(
                context, preferred_school, preferred_affiliations)
            person = {
                "email": "",
                "linkedin_url": linkedin_url,
                "name": name,
                "role": role,
                "source_url": page_url,
                "evidence": context[:500],
                "school_match": school_match,
                "school": preferred_school if school_match else None,
                "affinity": affinities,
                "seniority_rank": seniority,
                "on_domain": True,
            }
            same_name = next((
                existing for existing in merged.values()
                if name and existing.get("name")
                and existing["name"].strip().lower() == name.strip().lower()
            ), None)
            if same_name:
                same_name["linkedin_url"] = linkedin_url
                same_name["affinity"] = list(dict.fromkeys(
                    (same_name.get("affinity") or []) + affinities))
                if school_match:
                    same_name["school_match"] = True
                    same_name["school"] = preferred_school
            else:
                merged[f"linkedin:{linkedin_url.lower()}"] = person

    generic_locals = set(_LOCAL_PRIORITY)
    return sorted(
        merged.values(),
        key=lambda c: (
            0 if c["school_match"] else 1,
            c["seniority_rank"],
            0 if c["on_domain"] else 1,
            0 if c["name"] else 1,
            1 if (c.get("email") or "").split("@", 1)[0].lower() in generic_locals else 0,
            (c.get("email") or c.get("linkedin_url") or "").lower(),
        ),
    )


def select_outreach_contacts(candidates: List[Dict], emails: List[str],
                             company_domain: Optional[str],
                             limit: int = 5) -> List[Dict]:
    """Prefer same-school senior leaders, then other named/on-domain contacts."""
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
    selected = []
    for addr in safe_pool:
        candidate = by_email.get(addr.lower(), {
            "email": addr, "name": "", "role": None, "source_url": None,
            "school_match": False, "school": None,
        })
        candidate["warning"] = warning
        selected.append(candidate)

    # select_outreach_emails ranks only addresses. Re-rank its safe, on-domain
    # subset using the richer person evidence.
    selected.sort(key=lambda c: (
        0 if c.get("school_match") else 1,
        c.get("seniority_rank", 20),
        0 if c.get("name") else 1,
    ))
    selected_keys = {
        ((c.get("email") or "").lower(), (c.get("linkedin_url") or "").lower())
        for c in selected
    }
    linkedin_only = [
        dict(c) for c in (candidates or [])
        if c.get("linkedin_url") and not c.get("email")
        and ((c.get("email") or "").lower(),
             (c.get("linkedin_url") or "").lower()) not in selected_keys
    ]
    linkedin_only.sort(key=lambda c: (
        0 if c.get("school_match") else 1,
        0 if c.get("affinity") else 1,
        c.get("seniority_rank", 20),
        0 if c.get("name") else 1,
    ))
    selected.extend(linkedin_only)
    return selected[:limit]


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
               preferred_affiliations: Optional[str] = None) -> Dict:
        """Full enrichment: returns dict with url, domain, metadata fields,
        evidence-backed contacts, crawl diagnostics, and an ok flag."""
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
        }
        if not url:
            return result

        domain = result["domain"]
        texts: List[str] = []
        page_records: List[Dict[str, str]] = []
        queue = [url]
        queued = {url.rstrip("/")}
        # Keep path guesses as fallbacks. Real navigation discovered from the
        # home page is inserted ahead of them.
        fallback_pages = [
            urljoin(url.rstrip("/") + "/", p.lstrip("/")) for p in SUBPAGES
        ]
        attempted = 0
        while queue and attempted < MAX_PAGE_ATTEMPTS \
                and len(page_records) < MAX_PAGES_FETCHED:
            page_url = queue.pop(0)
            attempted += 1
            html = self.scraper.fetch_html(page_url)
            if not html:
                if not queue:
                    for fallback in fallback_pages:
                        key = fallback.rstrip("/")
                        if key not in queued:
                            queued.add(key)
                            queue.append(fallback)
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
                for fallback in fallback_pages:
                    key = fallback.rstrip("/")
                    if key not in queued:
                        queued.add(key)
                        queue.append(fallback)

        # Alumni bios are often orphaned from the current nav. Search may find
        # them, but only verified same-domain pages are fetched or trusted.
        school_pages = self._school_research_pages(
            company_name, domain, preferred_school)
        if school_pages and not any(
                _school_match(p.get("text") or "", preferred_school)
                for p in page_records):
            for page_url in school_pages:
                if attempted >= MAX_PAGE_ATTEMPTS or len(page_records) >= MAX_PAGES_FETCHED:
                    break
                if any(p["url"].rstrip("/") == page_url.rstrip("/") for p in page_records):
                    continue
                attempted += 1
                html = self.scraper.fetch_html(page_url)
                if not html:
                    continue
                text = self.scraper.extract_text(html)
                page_records.append({"url": page_url, "html": html, "text": text or ""})
                if text and len(text) > 80:
                    texts.append(f"SOURCE: {page_url}\n{text[:5000]}")

        combined = "\n\n".join(texts)[:24000]
        result["pages_attempted"] = attempted
        result["pages_scraped"] = len(page_records)
        result["research_sources"] = [p["url"] for p in page_records]

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

        all_emails = []
        for page in page_records:
            all_emails.extend(extract_emails_from_html(page["html"]))
        result["emails"] = rank_outreach_emails(all_emails, result["domain"])[:12]
        result["contacts"] = extract_contact_candidates(
            page_records, result["domain"], preferred_school,
            preferred_affiliations)
        if combined:
            meta = llm_metadata(
                company_name, combined, result["emails"], preferred_school)
            llm_contacts = []
            if meta:
                llm_contacts = meta.pop("_contacts", []) or []
            meta = meta or heuristic_metadata(company_name, combined)
            result.update({k: v for k, v in (meta or {}).items()})
            result["ok"] = bool((meta or {}).get("summary"))
            by_email = {
                contact["email"].lower(): contact
                for contact in result["contacts"]
            }
            for grounded in llm_contacts:
                contact = by_email.get(grounded["email"].lower())
                if not contact:
                    continue
                if grounded.get("name"):
                    contact["name"] = grounded["name"]
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
            result["contacts"].sort(key=lambda c: (
                0 if c.get("school_match") else 1,
                c.get("seniority_rank", 20),
                0 if c.get("on_domain") else 1,
                0 if c.get("name") else 1,
                (c.get("email") or c.get("linkedin_url") or "").lower(),
            ))
        result["research_quality"] = self._research_quality(
            result["pages_scraped"], result.get("summary"), result["contacts"])
        return result
