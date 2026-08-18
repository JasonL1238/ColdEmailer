"""Fill gaps after site scrape: LinkedIn via search, emails via free lookups.

LinkedIn is treated as a first-class outreach channel. When a company page
names a person but omits their /in/ URL, we search the public web for a
matching profile — only when the search snippet also mentions the company.

When we have a named person + company domain but no email, optional Hunter.io
(free-tier key) may supply an on-domain address. Name@domain pattern guesses
are never auto-attached as sendable emails; they are recorded as guesses only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from domain_names import registered_domain
from phrase_match import all_tokens_in, phrase_in, token_in
from contact_verify import (
    annotate_contact,
    domain_has_mx,
    email_matches_person,
    is_generic_inbox,
    linkedin_matches_person,
    name_tokens,
    normalize_email,
)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


_LINKEDIN_IN_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?",
    re.I,
)


def _normalize_linkedin(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if host not in {"linkedin.com", "www.linkedin.com"}:
        return None
    path = (parsed.path or "").rstrip("/")
    if not path.lower().startswith("/in/"):
        return None
    slug = path.split("/in/", 1)[-1].split("/")[0]
    if not slug or len(slug) < 2:
        return None
    return f"https://www.linkedin.com/in/{slug}"


def extract_linkedin_urls(*texts: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for text in texts:
        for match in _LINKEDIN_IN_RE.findall(text or ""):
            url = _normalize_linkedin(match)
            if url and url.lower() not in seen:
                seen.add(url.lower())
                found.append(url)
    return found


def _company_mentioned(company_name: str, title: str, body: str) -> bool:
    """True when title/body positively name this company.

    URL slugs are ignored — `/in/jane-doe-acme` must not count as evidence.
    A shared token like \"Acme\" alone is not enough to bind \"Acme AI\" to
    \"Acme Analytics\"; prefer the full company phrase or all distinctive tokens.
    """
    blob = f"{title or ''} {body or ''}".lower()
    if not blob.strip():
        return False
    cleaned = re.sub(
        r"\b(incorporated|inc|llc|ltd|corp|corporation|co|company)\b\.?",
        " ",
        (company_name or "").lower(),
    )
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
    if len(cleaned) < 3:
        return False
    # Commas and dots count as separators here — a company name is written
    # "Acme, Inc." on a page. deep_research deliberately excludes them.
    if phrase_in(cleaned, blob, r"[\s\-_,./]+"):
        return True
    bits = [t for t in cleaned.split() if len(t) >= 3]
    if len(bits) >= 2 and all_tokens_in(bits, blob):
        return True
    # Single long token only (Stripe, Datadog) — short generics like Acme/AI need
    # the full phrase path above.
    if len(bits) == 1 and len(bits[0]) >= 6:
        return token_in(bits[0], blob)
    return False


def find_linkedin_via_search(
        name: str,
        company_name: str,
        *,
        search_fn=None) -> Optional[str]:
    """Public web search for a person LinkedIn matching name + company.

    Requires positive company evidence in the search title/snippet —
    a same-name stranger at another employer must not be accepted.
    """
    tokens = name_tokens(name)
    if len(tokens) < 2 or not (company_name or "").strip():
        return None
    search = search_fn
    if search is None:
        try:
            from ddg_search import ddg_text_search
            search = ddg_text_search
        except Exception:
            return None
    query = f'"{name}" "{company_name}" site:linkedin.com/in'
    try:
        results = search(query, max_results=5) or []
    except Exception:
        return None

    for result in results:
        href = result.get("href") or result.get("url") or ""
        title = result.get("title") or ""
        body = result.get("body") or ""
        if not _company_mentioned(company_name, title, body):
            continue
        for url in extract_linkedin_urls(href, title, body):
            if linkedin_matches_person(url, name):
                return url
    return None


def email_patterns_for(name: str, domain: str) -> List[str]:
    """Common professional patterns — first.last@, flast@, etc."""
    tokens = name_tokens(name)
    domain = (domain or "").strip().lower().lstrip("@")
    if len(tokens) < 2 or not domain or "." not in domain:
        return []
    first, last = tokens[0], tokens[-1]
    locals_ = [
        f"{first}.{last}",
        f"{first}{last}",
        f"{first[0]}{last}",
        f"{first}_{last}",
        f"{first}-{last}",
        f"{last}.{first}",
    ]
    out: List[str] = []
    seen = set()
    for local in locals_:
        addr = f"{local}@{domain}".lower()
        if addr in seen or is_generic_inbox(addr):
            continue
        if not email_matches_person(addr, name):
            continue
        seen.add(addr)
        out.append(addr)
    return out


def hunter_find_email(
        name: str,
        domain: str,
        *,
        api_key: Optional[str] = None,
        http_get=None) -> Optional[Dict]:
    """Hunter.io email-finder (free tier). No-op without HUNTER_API_KEY.

    Only accepts addresses on the requested company domain.
    """
    key = (api_key if api_key is not None
           else os.getenv("HUNTER_API_KEY", "").strip())
    if not key:
        return None
    tokens = name_tokens(name)
    domain = (domain or "").strip().lower()
    if len(tokens) < 2 or not domain:
        return None
    first, last = tokens[0], tokens[-1]
    getter = http_get
    if getter is None:
        if requests is None:
            return None
        getter = requests.get
    try:
        resp = getter(
            "https://api.hunter.io/v2/email-finder",
            params={
                "domain": domain,
                "first_name": first,
                "last_name": last,
                "api_key": key,
            },
            timeout=8,
        )
        if getattr(resp, "status_code", 0) != 200:
            return None
        data = (resp.json() or {}).get("data") or {}
        email = normalize_email(data.get("email") or "")
        if not email or is_generic_inbox(email):
            return None
        if registered_domain(email) != registered_domain(
                f"x@{domain}"):
            return None
        if not email_matches_person(email, name):
            return None
        return {
            "email": email,
            "email_source": "hunter",
            "hunter_score": data.get("score"),
            "on_domain": True,
        }
    except Exception:
        return None


def pick_pattern_guess(name: str, domain: str,
                       *, check_mx: bool = True) -> Optional[str]:
    """Best pattern guess for notes only — never auto-attached as verified email."""
    mx = domain_has_mx(domain) if check_mx else True
    if mx is False:
        return None
    patterns = email_patterns_for(name, domain)
    return patterns[0] if patterns else None


def enrich_contacts_outreach(
        contacts: List[Dict],
        *,
        company_name: str,
        domain: Optional[str],
        search_fn=None,
        hunter_key: Optional[str] = None,
        check_mx: bool = True,
        max_linkedin_lookups: int = 3,
        max_email_lookups: int = 3,
        allow_pattern_guesses: Optional[bool] = None) -> List[Dict]:
    """Attach missing LinkedIn / Hunter emails onto named people from research.

    Pattern guesses (if enabled) are stored on `email_guess` only — they do not
    become sendable `email` fields and never receive email_verified.
    """
    if allow_pattern_guesses is None:
        allow_pattern_guesses = (
            os.getenv("EMAIL_PATTERN_INFERENCE", "1").strip() != "0"
        )
    out = [dict(c) for c in (contacts or [])]
    li_lookups = 0
    email_lookups = 0

    for contact in out:
        name = (contact.get("name") or "").strip()
        if not name or contact.get("name_from_email"):
            continue
        if len(name_tokens(name)) < 2:
            continue

        if not contact.get("linkedin_url") and li_lookups < max_linkedin_lookups:
            li_lookups += 1
            found = find_linkedin_via_search(
                name, company_name, search_fn=search_fn)
            if found:
                contact["linkedin_url"] = found
                contact["linkedin_source"] = "web_search"
                if not contact.get("source_url"):
                    contact["source_url"] = found

        has_email = bool((contact.get("email") or "").strip())
        if (not has_email and domain and email_lookups < max_email_lookups):
            email_lookups += 1
            hunter = hunter_find_email(
                name, domain, api_key=hunter_key)
            if hunter:
                contact["email"] = hunter["email"]
                contact["email_source"] = "hunter"
                contact["on_domain"] = True
                contact.setdefault(
                    "warning",
                    "Email from Hunter.io finder — confirm before sending.")
            elif allow_pattern_guesses:
                guess = pick_pattern_guess(
                    name, domain, check_mx=check_mx)
                if guess:
                    contact["email_guess"] = guess
                    contact.setdefault(
                        "warning",
                        f"Possible email pattern {guess} (not verified — "
                        f"confirm before using).")

    return [annotate_contact(c, check_mx=check_mx) for c in out]
