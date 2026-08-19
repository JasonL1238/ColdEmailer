"""Conservatively identify the domain a company uses for employee email.

A company's public website and its mailboxes can live on different domains.
Search-result frequency is useful discovery evidence, but it is not enough to
authorize name-pattern guesses on a rival domain.  An override therefore also
has to be recognisably company-related, MX-backed, and share a tenant-specific
mail host with the website domain.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from contact_verify import domain_has_mx, normalize_email


DOMAIN_MIN_ABSOLUTE = 4
DOMAIN_MIN_RATIO = 2.0

_HARVEST_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# People-data aggregators quote addresses from every company at once; their
# own domains must never be mistaken for the employer's.
_AGGREGATOR_DOMAINS = re.compile(
    r"(rocketreach|zoominfo|signalhire|lusha|apollo\.io|contactout|wiza|"
    r"muraena|leadiq|hunter\.io|snov\.io|clearbit|example\.|yourcompany|"
    r"sentry\.io|wixpress)", re.I)


def _plausible_domain(domain: str) -> Optional[str]:
    cleaned = (domain or "").lower().strip().strip(".")
    if "." not in cleaned or _AGGREGATOR_DOMAINS.search(cleaned):
        return None
    return cleaned


def harvest_email_domains(results: List[Dict]) -> Dict[str, int]:
    """Count plausible employer mail domains across search-result text."""
    counts: Counter = Counter()
    for result in results or []:
        blob = f"{result.get('title') or ''} {result.get('body') or ''}"
        for raw_domain in _HARVEST_RE.findall(blob):
            domain = _plausible_domain(raw_domain)
            if domain:
                counts[domain] += 1
    return dict(counts)


def count_observed_email_domains(addresses: Iterable[str]) -> Dict[str, int]:
    """Count unique addresses found on the company site by their domain."""
    counts: Counter = Counter()
    seen = set()
    for raw in addresses or []:
        address = normalize_email(raw)
        if not address or address in seen or "@" not in address:
            continue
        seen.add(address)
        domain = _plausible_domain(address.rsplit("@", 1)[-1])
        if domain:
            counts[domain] += 1
    return dict(counts)


def contacts_need_mail_domain(contacts: Iterable[Dict]) -> bool:
    """Whether named contacts need a domain before an address can be found."""
    return any(
        contact.get("name")
        and not contact.get("name_from_email")
        and not (contact.get("email") or "").strip()
        for contact in contacts or []
    )


def _company_acronym(name: str) -> str:
    bits = [b for b in re.split(r"[^A-Za-z0-9]+", name or "") if b]
    return "".join(b[0] for b in bits).lower()


# Multi-tenant MX hosts shared by millions of unrelated organisations. Two
# domains both pointing at aspmx.l.google.com proves only that both bought
# Google Workspace. A tenant-specific host can establish a common mail estate.
_GENERIC_MX = re.compile(
    r"^(?:alt\d+\.)?aspmx[0-9]*\.l\.google\.com$|"
    r"^aspmx\d*\.googlemail\.com$|"
    r"^mx\.zoho\.(?:com|eu)$|^mail\.protonmail\.ch$|^mailsec\.protonmail\.ch$|"
    r"^(?:in\d?-smtp|mx)\.mail\.icloud\.com$|^smtp\.secureserver\.net$|"
    r"^mailstore\d*\.secureserver\.net$|^mx\.yandex\.net$",
    re.I)


def _mx_hosts(domain: str) -> set:
    try:
        import dns.resolver
        return {
            str(record.exchange).lower().rstrip(".")
            for record in dns.resolver.resolve(domain, "MX")
        }
    except Exception:
        return set()


def shares_mail_tenancy(domain_a: str, domain_b: str) -> bool:
    """True when both domains publish an identical tenant-specific MX host."""
    if not domain_a or not domain_b or domain_a == domain_b:
        return False
    shared = _mx_hosts(domain_a) & _mx_hosts(domain_b)
    return any(not _GENERIC_MX.match(host) for host in shared)


def infer_email_domain(
        company_name: str,
        website_domain: Optional[str],
        counts: Dict[str, int],
        *,
        check_mx: bool = True) -> Tuple[Optional[str], str]:
    """Return ``(mail_domain, reason)``, retaining the website-domain prior."""
    if not website_domain:
        return website_domain, "no website domain"
    website_domain = website_domain.lower().strip().strip(".")
    web_n = counts.get(website_domain, 0)
    company_tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", (company_name or "").lower())
        if len(token) >= 3
    ]
    acronym = _company_acronym(company_name)
    web_stem = website_domain.split(".")[0]
    for domain, sightings in sorted(counts.items(), key=lambda item: -item[1]):
        domain = (domain or "").lower().strip().strip(".")
        if domain == website_domain:
            continue
        stem = domain.split(".")[0]
        # A bare substring would make camping-arize.com look related to Arize.
        if not (
            any(stem == token or stem.startswith(token)
                for token in company_tokens)
            or stem == acronym
            or web_stem.startswith(stem)
        ):
            continue
        if (sightings < DOMAIN_MIN_ABSOLUTE
                or sightings < DOMAIN_MIN_RATIO * max(web_n, 1)):
            continue
        if check_mx and domain_has_mx(domain) is False:
            continue
        if check_mx and not shares_mail_tenancy(website_domain, domain):
            continue
        return domain, (
            f"{domain} seen {sightings}x vs {website_domain} {web_n}x, "
            "and both share mail tenancy (MX)"
        )
    return website_domain, f"kept website domain ({web_n} sightings)"


def discover_mail_domain(
        company_name: str,
        website_domain: Optional[str],
        *,
        search_fn: Callable,
        observed_emails: Optional[Iterable[str]] = None,
        check_mx: bool = True) -> Tuple[Optional[str], str, Dict[str, int]]:
    """Gather bounded public evidence, then infer the employee-mail domain.

    The two searches are intentionally template-dense. Their results are only
    evidence for domain selection, never proof that any displayed address is a
    real person's mailbox.
    """
    counts: Counter = Counter(count_observed_email_domains(
        observed_emails or []))
    if website_domain and company_name:
        queries = (
            f'"@{website_domain}" email contact',
            f"{company_name} email address format",
        )
        for query in queries:
            try:
                results = search_fn(query, max_results=8) or []
            except Exception:
                results = []
            counts.update(harvest_email_domains(results))
    domain, reason = infer_email_domain(
        company_name, website_domain, dict(counts), check_mx=check_mx)
    return domain, reason, dict(counts)
