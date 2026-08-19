"""Fill gaps after site scrape: LinkedIn via search, emails via free lookups.

LinkedIn is treated as a first-class outreach channel. When a company page
names a person but omits their /in/ URL, we search the public web for a
matching profile — only when the search snippet also mentions the company.

When we have a named person + company domain but no email, bounded name@domain
patterns are checked over direct SMTP first. Optional Hunter.io supplies an
on-domain address only after those checks fail or become inconclusive. Pattern
guesses are never auto-attached as sendable emails; they remain guesses even
when the mailbox accepts mail.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Optional, Set
from urllib.parse import urlparse

import mailbox_verify
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
    """Ten common, strongly person-shaped professional email patterns."""
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
        f"{first[0]}.{last}",
        f"{first}.{last[0]}",
        f"{last}.{first}",
        f"{last}{first}",
        f"{last}.{first[0]}",
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


def probe_pattern_guesses(
        name: str,
        domain: str,
        *,
        check_mx: bool = True,
        verify_fn: Optional[Callable] = None,
        exclude: Optional[Set[str]] = None) -> Dict:
    """Try every bounded name pattern over direct SMTP, strongest first.

    Stops at the first deliverable mailbox. An inconclusive result (blocked
    port, greylisting, catch-all, disabled verification) stops the sequence as
    well: asking nine more versions cannot make that transport/domain policy
    informative. Hunter belongs after this function, never inside it.

    The returned address remains a *guess*. SMTP proves only that a mailbox
    accepts mail, not that it belongs to ``name``.
    """
    domain = (domain or "").strip().lower().lstrip("@")
    if check_mx and domain_has_mx(domain) is False:
        return {
            "email": None, "smtp_status": None, "reason": "no_mx",
            "attempts": [], "all_rejected": False,
        }
    patterns = [
        address for address in email_patterns_for(name, domain)
        if normalize_email(address) not in (exclude or set())
    ]
    if not patterns:
        return {
            "email": None, "smtp_status": None, "reason": "no_patterns",
            "attempts": [], "all_rejected": False,
        }

    verifier = verify_fn or mailbox_verify.verify_mailbox
    attempts = []
    for address in patterns:
        try:
            probe = verifier(address, backend="smtp")
            verdict = getattr(probe.verdict, "value", str(probe.verdict))
            reason = probe.reason
        except Exception:
            verdict, reason = "unknown", "probe_error"
        attempts.append({
            "email": address, "smtp_status": verdict, "reason": reason,
        })
        if verdict == "deliverable":
            return {
                "email": address, "smtp_status": verdict, "reason": reason,
                "attempts": attempts, "all_rejected": False,
            }
        if verdict == "undeliverable":
            continue
        return {
            "email": address,
            "smtp_status": None if reason in ("disabled", "no_transport")
            else verdict,
            "reason": reason,
            "attempts": attempts,
            "all_rejected": False,
        }
    return {
        "email": None, "smtp_status": "undeliverable",
        "reason": "all_patterns_rejected", "attempts": attempts,
        "all_rejected": True,
    }


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
        allow_pattern_guesses: Optional[bool] = None,
        mailbox_verify_fn: Optional[Callable] = None) -> List[Dict]:
    """Attach missing LinkedIn and SMTP-first/Hunter-fallback email evidence.

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
            pattern = None
            if allow_pattern_guesses:
                pattern = probe_pattern_guesses(
                    name, domain, check_mx=check_mx,
                    verify_fn=mailbox_verify_fn)
            if pattern and pattern.get("smtp_status") == "deliverable":
                # A live mailbox is still only a guess about ownership. Keep it
                # out of the sendable email field and skip Hunter entirely.
                contact["email_guess"] = pattern["email"]
                contact["email_guess_smtp_status"] = "deliverable"
                contact["email_pattern_attempts"] = len(
                    pattern.get("attempts") or [])
                contact.setdefault(
                    "warning",
                    f"Possible email pattern {pattern['email']} accepts mail "
                    "(ownership not proven — confirm before using).")
                continue

            # Direct SMTP exhausted or could not answer. Only now may the
            # paid/provider path run.
            hunter = hunter_find_email(name, domain, api_key=hunter_key)
            if hunter:
                contact["email"] = hunter["email"]
                contact["email_source"] = "hunter"
                contact["on_domain"] = True
                contact.setdefault(
                    "warning",
                    "Email from Hunter.io finder — confirm before sending.")
            elif allow_pattern_guesses:
                guess = (pattern or {}).get("email")
                if guess:
                    contact["email_guess"] = guess
                    contact["email_guess_smtp_status"] = pattern.get(
                        "smtp_status")
                    contact["email_pattern_attempts"] = len(
                        pattern.get("attempts") or [])
                    contact.setdefault(
                        "warning",
                        f"Possible email pattern {guess} (not verified — "
                        f"confirm before using).")

    return [annotate_contact(c, check_mx=check_mx) for c in out]
