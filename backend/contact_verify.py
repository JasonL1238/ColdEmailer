"""Person-level email and LinkedIn verification for scraped contacts.

Goals:
- Prefer person mailboxes over company inboxes (hello@, info@, careers@).
- Confirm an address is plausibly deliverable (MX) and tied to the person.
- Confirm a LinkedIn /in/ URL slug matches that person's name, not a
  coincidental first-name match or a company page.
"""
from __future__ import annotations

import re
import socket
import threading
import time
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Company / role inboxes — useful for routing, not as a named person contact.
GENERIC_LOCALS = frozenset({
    "founders", "founder", "ceo", "team", "hello", "hi", "hey", "careers",
    "jobs", "recruiting", "talent", "contact", "info", "press", "sales",
    "support", "admin", "office", "hr", "people", "general", "mail",
    "enquire", "enquiry", "inquiries", "inquiry", "help", "partners",
    "partnerships", "media", "marketing", "ops", "operations", "billing",
    "accounts", "account", "reception", "desk", "apply", "applications",
    "work", "join", "joinus", "join-us", "studio", "hello-world",
})

_NAME_STOP = frozenset({
    "jr", "sr", "ii", "iii", "iv", "md", "phd", "mba", "esq",
})


def normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def email_local(email: str) -> str:
    return normalize_email(email).split("@", 1)[0]


def is_generic_inbox(email_or_local: str) -> bool:
    """True for role/company inboxes that are not a specific person."""
    local = email_or_local.strip().lower()
    if "@" in local:
        local = local.split("@", 1)[0]
    local = local.split("+", 1)[0]
    if local in GENERIC_LOCALS:
        return True
    parts = re.split(r"[._+-]+", local)
    return bool(parts) and all(p in GENERIC_LOCALS or len(p) <= 1 for p in parts)


def _fold_text(value: str) -> str:
    """ASCII-fold accents and drop apostrophes: José O'Brien → jose obrien."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(c for c in normalized if not unicodedata.combining(c))
    return without_marks.replace("'", "").replace("'", "")


def name_tokens(name: Optional[str]) -> List[str]:
    folded = _fold_text(name or "").lower()
    tokens = re.split(r"[^a-z0-9]+", folded)
    return [t for t in tokens if len(t) >= 2 and t not in _NAME_STOP]


def name_appears_in(name: Optional[str], text: Optional[str]) -> bool:
    """True when both the first and last name token appear in `text`.

    The weakest useful identity check, and it was written three times over —
    once for SERP titles, once for GitHub commit authors, once for paper author
    blocks. One copy, beside the tokenizer it depends on.
    """
    want = name_tokens(name)
    if len(want) < 2:
        return False
    got = set(name_tokens(text))
    return want[0] in got and want[-1] in got


def email_matches_person(email: str, name: Optional[str],
                         *, name_from_email: bool = False) -> bool:
    """True when the local-part is a strong match for the person name.

    Accepts: jane.doe@, jdoe@, janedoe@, doe.jane@ for "Jane Doe".
    Rejects: hello@, doe@ alone, john.smith@ for "Jane Smith", substring
    accidents (shannon@ for Ann), and circular matches where the only name
    evidence was inferred from this same local-part.
    """
    if not email or is_generic_inbox(email):
        return False
    if name_from_email:
        # Name was invented from the address — that is not independent evidence.
        return False
    tokens = name_tokens(name)
    if not tokens:
        return False
    local = email_local(email).split("+", 1)[0]
    local = _fold_text(local).lower()
    local_parts = [p for p in re.split(r"[._+-]+", local) if p]
    local_compact = re.sub(r"[^a-z0-9]", "", local)
    if not local_compact:
        return False

    first, last = tokens[0], tokens[-1]

    if len(tokens) >= 2:
        # first.last / first_last / last.first (exact part membership), plus
        # the common initial-separated forms j.doe / jane.d / doe.j.
        if len(local_parts) >= 2:
            if local_parts[0] == first and local_parts[-1] == last:
                return True
            if local_parts[0] == last and local_parts[-1] == first:
                return True
            if local_parts == [first[0], last]:
                return True
            if local_parts == [first, last[0]]:
                return True
            if local_parts == [last, first[0]]:
                return True
            # middle names allowed between first and last
            if first in local_parts and last in local_parts and local_parts[0] in {
                    first, last} and local_parts[-1] in {first, last}:
                return True
            return False

        # Compact: janedoe / doejane / jdoe
        if local_compact in {first + last, last + first, first[0] + last}:
            return True
        return False

    # Single-token name: local must equal that token exactly (kim@ / Kim)
    return len(local_parts) == 1 and local_parts[0] == first


# Answered MX lookups, keyed by domain. A crawl annotates every contact on
# every refresh pass, so one 30-page site asked the resolver 275 times about 4
# distinct domains — 271 of them redundant. MX records are stable on a scale of
# hours, far longer than the minutes a crawl lasts, so a short TTL keeps the
# answer honest while removing the amplification.
_MX_CACHE: Dict[str, Tuple[float, Optional[bool]]] = {}
_MX_CACHE_TTL = 900.0
_MX_CACHE_MAX = 2048
# "Unknown" is a transient state (timeout, SERVFAIL, VPN flap), so it expires
# much sooner than a real answer — caching it for the full TTL would freeze a
# recoverable failure in place for the rest of the run.
_MX_CACHE_TTL_UNKNOWN = 30.0
_MX_CACHE_LOCK = threading.Lock()


def domain_has_mx(domain: str, timeout: float = 2.0) -> Optional[bool]:
    """True / False / None for "can this domain receive mail?".

    None means the question could not be answered — resolver missing, timeout,
    SERVFAIL, network down. That distinction is the whole point: callers gate
    real sends on this, and an earlier version returned False for every one of
    those cases, so a flapping VPN made gmail.com "has no mail server" and
    refused an entire batch as permanently failed.

    False is reserved for positive evidence of the opposite: the domain does
    not exist, or it publishes RFC 7505 null MX ("."), which is an explicit
    declaration that it accepts no mail.

    Answers are memoized for `_MX_CACHE_TTL` seconds; see `_MX_CACHE`.
    """
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return False
    now = time.monotonic()
    with _MX_CACHE_LOCK:
        hit = _MX_CACHE.get(domain)
        if hit is not None:
            expires_at, value = hit
            if now < expires_at:
                return value
            del _MX_CACHE[domain]
    answer = _resolve_mx(domain, timeout)
    ttl = _MX_CACHE_TTL_UNKNOWN if answer is None else _MX_CACHE_TTL
    with _MX_CACHE_LOCK:
        if len(_MX_CACHE) >= _MX_CACHE_MAX:
            # Dropping the cache only costs re-resolution. Never wrong, just slow.
            _MX_CACHE.clear()
        _MX_CACHE[domain] = (now + ttl, answer)
    return answer


def clear_mx_cache() -> None:
    """Drop every memoized MX answer. For tests and long-lived processes."""
    with _MX_CACHE_LOCK:
        _MX_CACHE.clear()


def _resolve_mx(domain: str, timeout: float) -> Optional[bool]:
    """The uncached lookup. `domain` is already normalized and non-empty."""
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return _domain_resolves(domain, timeout)

    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    try:
        answers = resolver.resolve(domain, "MX")
        targets = [str(getattr(r, "exchange", "")).strip() for r in answers]
        # A single "." target is RFC 7505: this domain accepts no mail at all.
        if targets and all(t in (".", "") for t in targets):
            return False
        if answers:
            return True
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        pass                      # no MX; a bare A record still accepts mail
    except Exception:
        return None               # timeout / SERVFAIL / no route — unknown
    try:
        return bool(resolver.resolve(domain, "A"))
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        return False
    except Exception:
        return None


def _domain_resolves(domain: str, timeout: float) -> Optional[bool]:
    """Fallback when dnspython is absent: an A/AAAA lookup, not an MX one.

    getaddrinfo only resolves — it never opens a connection — so this is cheap,
    but it cannot see MX records and cannot tell a timeout from NXDOMAIN
    beyond the gaierror code. Anything other than a definite "no such host" is
    reported as unknown rather than as undeliverable.
    """
    try:
        return bool(socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP))
    except socket.gaierror as exc:
        name = getattr(exc, "errno", None)
        if name in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", None)):
            return False
        return None
    except OSError:
        return None


def verify_email(email: str, name: Optional[str] = None,
                 check_mx: bool = True,
                 name_from_email: bool = False) -> Dict:
    """Return verification details for a scraped address."""
    addr = normalize_email(email)
    result = {
        "email": addr,
        "email_kind": "unknown",
        "email_person_match": False,
        "email_mx_ok": None,
        "email_verified": False,
        "reason": "",
    }
    if not addr or "@" not in addr:
        result["reason"] = "missing_or_invalid"
        return result
    local, _, domain = addr.partition("@")
    if is_generic_inbox(local):
        result["email_kind"] = "generic"
        result["reason"] = "company_or_role_inbox"
        if check_mx:
            result["email_mx_ok"] = domain_has_mx(domain)
        return result

    person_ok = email_matches_person(
        addr, name, name_from_email=name_from_email)
    result["email_person_match"] = person_ok
    result["email_kind"] = "personal" if person_ok else "named_unmatched"
    mx_ok = domain_has_mx(domain) if check_mx else None
    result["email_mx_ok"] = mx_ok
    # Verified only when the local matches the person AND MX was confirmed.
    # Skipping MX must not claim verification.
    result["email_verified"] = bool(person_ok and mx_ok is True)
    if not person_ok:
        result["reason"] = "local_does_not_match_name"
    elif not check_mx:
        result["reason"] = "mx_not_checked"
    elif mx_ok is False:
        result["reason"] = "no_mx"
    else:
        result["reason"] = "ok"
    return result


def canonical_linkedin_profile(value: Optional[str]) -> str:
    """Canonical `https://www.linkedin.com/in/<slug>`, or ValueError.

    Raising rather than returning None is what lets both callers keep their
    existing behaviour from one implementation: `models` surfaces the message
    to the user as a 422, and `enrichment` catches it and returns None because
    a scraped page is allowed to contain links that are not profiles.
    """
    try:
        parsed = urlparse((value or "").strip())
    except ValueError:
        raise ValueError("LinkedIn URL is invalid")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not is_linkedin_host(host):
        raise ValueError("Use a full https://www.linkedin.com/in/... profile URL")
    parts = [part for part in parsed.path.split("/") if part]
    if (len(parts) != 2 or parts[0].lower() != "in"
            or not parts[1].strip()):
        raise ValueError("LinkedIn URL must point to a member profile")
    return f"https://www.linkedin.com/in/{parts[1]}"


def linkedin_slug(url: Optional[str]) -> Optional[str]:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not is_linkedin_host(host):
        return None
    path = parsed.path.rstrip("/").lower()
    if not path.startswith("/in/"):
        return None
    slug = path[4:].split("/")[0]
    return slug or None


def is_linkedin_host(host: Optional[str]) -> bool:
    """True for LinkedIn itself, including country subdomains such as `in`."""
    value = (host or "").lower().rstrip(".")
    return value == "linkedin.com" or value.endswith(".linkedin.com")


def linkedin_matches_person(url: Optional[str], name: Optional[str]) -> bool:
    """True when the /in/ slug shares identifying name tokens with the person.

    Requires first+last as full slug parts (or exact compact equality).
    Rejects bare first-name slugs when a last name is known, and rejects
    prefix accidents (alex-kim ⊂ alex-kimberly-jones).
    """
    slug = linkedin_slug(url)
    tokens = name_tokens(name)
    if not slug or not tokens:
        return False
    slug_parts = [p for p in re.split(r"[-_]+", _fold_text(slug).lower()) if p]
    # Drop trailing random id fragments like "jane-doe-a1b2c3"
    cleaned = []
    for part in slug_parts:
        if re.fullmatch(r"[a-f0-9]{5,}", part) and part not in tokens:
            continue
        # LinkedIn often appends numeric discriminators: jane-doe-123
        if re.fullmatch(r"\d{2,}", part):
            continue
        cleaned.append(part)
    slug_parts = cleaned or slug_parts
    slug_compact = "".join(slug_parts)

    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        if first in slug_parts and last in slug_parts:
            return True
        if slug_compact in {first + last, last + first, first[0] + last}:
            return True
        return False

    return tokens[0] in slug_parts and (
        slug_parts == [tokens[0]] or slug_compact == tokens[0])


def verify_linkedin(url: Optional[str], name: Optional[str]) -> Dict:
    slug = linkedin_slug(url)
    match = linkedin_matches_person(url, name)
    return {
        "linkedin_url": url,
        "linkedin_slug": slug,
        "linkedin_person_match": match,
        "linkedin_verified": bool(match and slug),
        "reason": "ok" if match else (
            "missing_url" if not slug else "slug_does_not_match_name"),
    }


def annotate_contact(contact: Dict, check_mx: bool = True) -> Dict:
    """Attach verification fields onto a contact candidate dict."""
    out = dict(contact)
    email = out.get("email") or ""
    name = out.get("name") or ""
    linkedin = out.get("linkedin_url") or ""
    # Only trust the extractor's explicit flag. Guessing "name looks like the
    # email local" rejects legitimate page names (Jane Doe + jane.doe@).
    from_email = bool(out.get("name_from_email"))
    on_domain = bool(out.get("on_domain"))

    email_info = verify_email(
        email, name, check_mx=check_mx, name_from_email=from_email,
    ) if email else {
        "email_kind": "none",
        "email_person_match": False,
        "email_mx_ok": None,
        "email_verified": False,
        "reason": "no_email",
    }
    li_info = verify_linkedin(linkedin, name) if linkedin else {
        "linkedin_person_match": False,
        "linkedin_verified": False,
        "reason": "no_linkedin",
    }

    out["email_kind"] = email_info.get("email_kind", "unknown")
    out["email_person_match"] = bool(email_info.get("email_person_match"))
    out["email_mx_ok"] = email_info.get("email_mx_ok")
    out["email_verified"] = bool(email_info.get("email_verified"))
    out["linkedin_person_match"] = bool(li_info.get("linkedin_person_match"))
    out["linkedin_verified"] = bool(li_info.get("linkedin_verified"))
    out["name_from_email"] = from_email

    out["person_verified"] = bool(
        name and not from_email and (
            out["email_verified"] or out["linkedin_verified"]
            # On-domain person-matched email only when MX was confirmed True.
            # Unchecked MX (None) must not promote a contact to verified.
            or (out["email_person_match"] and on_domain
                and out.get("email_mx_ok") is True)
        )
    )
    return out


def select_verified_person_contacts(
        candidates: List[Dict], *, limit: int = 1,
        require_person: bool = True,
        check_mx: bool = True) -> List[Dict]:
    """Pick contacts that are real people (not company inboxes).

    Never returns generic inboxes or name-unmatched locals when require_person.
    """
    annotated = [annotate_contact(c, check_mx=check_mx) for c in (candidates or [])]
    if require_person:
        annotated = [
            c for c in annotated
            if c.get("name") and not c.get("name_from_email") and (
                c.get("person_verified")
                or c.get("linkedin_verified")
                or (c.get("email_person_match") and c.get("email_kind") == "personal")
            )
        ]

    def score(c: Dict) -> Tuple:
        return (
            0 if c.get("person_verified") else 1,
            0 if c.get("email_verified") else 1,
            0 if c.get("linkedin_verified") else 1,
            0 if c.get("email_kind") == "personal" else 1,
            c.get("seniority_rank", 20),
            0 if c.get("school_match") else 1,
            0 if c.get("on_domain") else 1,
            (c.get("email") or c.get("linkedin_url") or "").lower(),
        )

    annotated.sort(key=score)
    return annotated[:limit]
