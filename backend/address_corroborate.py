"""Has this exact address been seen where only real addresses live?

`mailbox_verify` asks a mail server whether a mailbox exists — the right
question, and unanswerable on most home networks, where outbound TCP/25 is
blocked. This module asks a weaker one that public HTTPS endpoints answer for
free: does this exact string already appear in a corpus that only records
addresses real people actually used?

A hit is strong evidence the address is real, and most sources also return the
NAME attached to it — the half a mailbox probe can never supply. A 250 from
gs.com says a mailbox is there, not that it is *your* John Smith; a commit
signed `ken.hirsch@gs.com` by "Ken Hirsch" says both.

A miss means nothing whatsoever. Every oracle is a literal string lookup, not
an inference, so it can confirm presence and never absence. Measured hit rates,
the zero-false-positive result, and the sources that were tried and rejected
are in docs/decisions.md ("Address corroboration") — re-measure before changing
any of it.

Three call-shape traps, each of which yields a confident wrong answer if
skipped, and each pinned by a test. They are why this file is longer than it
looks like it should be:

  - lore's Atom feed echoes the query inside <title>, so "the address appears
    in the response body" is trivially true for every query ever made. Only
    text inside an <entry> counts.
  - GitHub's search `total_count` is an estimate, not a count — one real
    address reports 1.2M. Every hit is confirmed per item instead.
  - Gravatar's v3 profile API is sha256-only and silently 404s an md5 hash,
    which reads as "unknown address" rather than "you asked wrong".

Deliberately not implemented: any provider login, password-reset or invite
endpoint that leaks whether an account exists. Those are enumeration attacks
against the account holder.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, unquote

import found_email
from contact_verify import name_appears_in, normalize_email
from ttl_cache import cache_clear, cache_get, cache_put

GRAVATAR_PROFILE = "https://api.gravatar.com/v3/profiles"
OPENPGP_BY_EMAIL = "https://keys.openpgp.org/vks/v1/by-email"
UBUNTU_KEYSERVER = "https://keyserver.ubuntu.com/pks/lookup"
LORE_SEARCH = "https://lore.kernel.org/all/"

_TTL_HIT = 3600.0        # corpus presence does not change within an hour
_TTL_MISS = 300.0        # a miss may become a hit as corpora are indexed
_CACHE_MAX = 2048

_CACHE: Dict[str, Tuple[float, "Corroboration"]] = {}


@dataclass(frozen=True)
class Hit:
    source: str
    url: Optional[str] = None
    names: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Corroboration:
    """What the corpora said. `name_match` is tri-state on purpose.

    True  — a source returned a name and it is this person's.
    False — a source returned a name and it belongs to someone else.
    None  — nothing returned a name, so identity is simply unknown. That is
            NOT the same as False, and collapsing the two would either promote
            an anonymous hit or discard a real conflict.
    """
    email: str
    sources: Tuple[str, ...] = ()
    names: Tuple[str, ...] = ()
    name_match: Optional[bool] = None
    evidence_url: Optional[str] = None
    reason: str = ""
    checked_at: float = 0.0

    @property
    def found(self) -> bool:
        return bool(self.sources)

    def as_dict(self) -> Dict:
        return {
            "found": self.found,
            "sources": list(self.sources),
            "names": list(self.names),
            "name_match": self.name_match,
            "evidence_url": self.evidence_url,
            "reason": self.reason,
        }


def _enabled() -> bool:
    return os.getenv("ADDRESS_CORROBORATION", "1").strip() != "0"


def clear_corroboration_cache() -> None:
    cache_clear(_CACHE)


# --------------------------------------------------------------------------
# Oracles. Each returns a Hit only on an item-level confirmed match, or None.
# None always means "no information", never "the address is fake".
# --------------------------------------------------------------------------

def github_commit_hit(email: str, *, token=None, http_get=None,
                      budget=None) -> Optional[Hit]:
    """Did anyone ever author a public commit from this exact address?

    The single highest-coverage source, and the only one that reliably returns
    a human name alongside the address. `author-email:` is an exact-match
    qualifier — proper substrings and one-character truncations of a real
    address both return nothing — but the surrounding `total_count` is an
    estimate, so the match is confirmed per item instead.
    """
    url = (f"{found_email.GITHUB_API}/search/commits"
           f"?q=author-email:{quote(email, safe='')}&per_page=5")
    data = found_email.github_json(url, token=token, http_get=http_get,
                                   budget=budget)
    if not isinstance(data, dict):
        return None
    names: List[str] = []
    evidence = None
    fork_evidence = None
    for item in data.get("items") or []:
        commit = item.get("commit") or {}
        matched = False
        for role in ("author", "committer"):
            who = commit.get(role) or {}
            if normalize_email(who.get("email") or "") != email:
                continue
            matched = True
            name = (who.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        if not matched:
            continue
        # Search returns the same commit from every copy of the repo, so the
        # first match is often a link somewhere the person has never been. The
        # commit is genuinely theirs either way — the match is confirmed per
        # item — this only picks a less confusing citation.
        #
        # Partial by nature: it catches real GitHub forks, and cannot catch
        # re-uploaded scrape mirrors. Measured — storchaka@gmail.com's top hits
        # are all `swe-train/numpy__numpy`, which GitHub reports as fork=False
        # because it was created by re-committing rather than by forking. There
        # is no field that distinguishes those, so the citation is sometimes
        # still an odd repo. Do not add an API call trying to fix it.
        url = item.get("html_url")
        if (item.get("repository") or {}).get("fork"):
            fork_evidence = fork_evidence or url
        else:
            evidence = evidence or url
    evidence = evidence or fork_evidence
    if evidence is None and not names:
        return None
    return Hit("github_commit", evidence, tuple(names))


def github_profile_hit(email: str, *, token=None, http_get=None,
                       budget=None) -> Optional[Hit]:
    """Is this address on someone's public GitHub profile?

    Independent of the commit route — it reads the profile field, not commit
    headers — so it corroborates addresses the commit index has never seen.
    The search result carries no email, so each candidate profile is fetched
    and compared exactly; a search that merely ranked a login highly is not a
    hit.
    """
    url = (f"{found_email.GITHUB_API}/search/users"
           f"?q={quote(email, safe='')}+in:email&per_page=3")
    data = found_email.github_json(url, token=token, http_get=http_get,
                                   budget=budget)
    if not isinstance(data, dict) or not data.get("total_count"):
        return None
    for item in (data.get("items") or [])[:2]:
        login = (item.get("login") or "").strip()
        if not login:
            continue
        profile = found_email.github_json(
            f"{found_email.GITHUB_API}/users/{quote(login, safe='')}",
            token=token, http_get=http_get, budget=budget)
        if not isinstance(profile, dict):
            continue
        if normalize_email(profile.get("email") or "") != email:
            continue
        name = (profile.get("name") or "").strip()
        return Hit("github_profile", profile.get("html_url"),
                   (name,) if name else ())
    return None


def gravatar_hit(email: str, *, http_get=None, budget=None,
                 **_) -> Optional[Hit]:
    """Does a Gravatar profile exist for this address?

    Free and unauthenticated. The v3 endpoint is sha256-ONLY — feeding it the
    md5 hash that the legacy avatar URLs use returns 404 for addresses that
    demonstrably have profiles, which looks exactly like zero coverage.

    The profile endpoint is used rather than the avatar image because a profile
    can exist without a photo, and because it returns a display name worth
    matching against the target.
    """
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    fetched = found_email.guarded_get_json(
        f"{GRAVATAR_PROFILE}/{digest}", http_get=http_get, budget=budget)
    if not fetched or fetched.status_code != 200:
        return None
    data = fetched.data if isinstance(fetched.data, dict) else {}
    name = (data.get("display_name") or "").strip()
    return Hit("gravatar", data.get("profile_url"), (name,) if name else ())


def pgp_hit(email: str, *, http_get=None, budget=None, **_) -> Optional[Hit]:
    """Is there a published PGP key for this address?

    Two servers with different meanings. keys.openpgp.org publishes an identity
    address only after the owner confirms it by clicking a link sent to that
    address (their documented policy, not something measured here), so a hit is
    evidence the mailbox received and acted on mail. keyserver.ubuntu.com is
    SKS-style and accepts unverified uploads, so its hits are weaker — but it
    returns the uid, which carries a name, and it hit addresses openpgp.org
    missed. Both are queried; the stronger one wins.
    """
    q = quote(email, safe="")
    fetched = found_email.guarded_get_text(
        f"{OPENPGP_BY_EMAIL}/{q}", http_get=http_get, budget=budget)
    if (fetched and fetched.status_code == 200
            and "BEGIN PGP PUBLIC KEY BLOCK" in (fetched.text or "")):
        # The key is armored base64, so no name is readable from it.
        return Hit("pgp_verified", f"https://keys.openpgp.org/search?q={q}", ())

    fetched = found_email.guarded_get_text(
        f"{UBUNTU_KEYSERVER}?op=index&options=mr&search={q}",
        http_get=http_get, budget=budget)
    if not fetched or fetched.status_code != 200:
        return None
    body = fetched.text or ""
    if not body.startswith("info:"):
        return None
    names: List[str] = []
    matched = False
    for line in body.splitlines():
        if not line.startswith("uid:"):
            continue
        parts = line.split(":")
        uid = unquote(parts[1]) if len(parts) > 1 else ""
        # Machine-readable index rows are returned for the whole key, which may
        # carry several addresses. Only the queried one counts.
        if email not in uid.lower():
            continue
        matched = True
        name = uid.split("<", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    if not matched:
        return None
    return Hit("pgp_keyserver",
               f"{UBUNTU_KEYSERVER}?op=index&search={q}", tuple(names))


def mailing_list_hit(email: str, *, http_get=None, budget=None,
                     **_) -> Optional[Hit]:
    """Does this address appear in the public-inbox archive at lore.kernel.org?

    Only lore is used. mail-archive.com and lists.debian.org were measured
    alongside it and both produce real false positives — they match on name
    tokens rather than the address, so a plausible invented address on a real
    domain comes back "found". lore phrase-matches and returns HTTP 404 for
    zero results, which is an unambiguous miss.

    No name is extracted: measured, only 8 of 199 matching entries had the
    address in a From header, so a name taken from the entry usually belongs to
    whoever quoted the address rather than whoever owns it.
    """
    # Quoted so public-inbox phrase-matches instead of splitting the address
    # into tokens, which is how the archives that were rejected produce hits
    # for addresses nobody ever used.
    phrase = quote(f'"{email}"', safe="")
    url = f"{LORE_SEARCH}?q={phrase}&x=A"
    fetched = found_email.guarded_get_text(url, http_get=http_get,
                                           budget=budget)
    if not fetched or fetched.status_code != 200:
        return None
    body = fetched.text or ""
    # The feed echoes the query inside <title>, so testing the whole body would
    # report a hit for literally every query. Only entries count.
    lowered = body.lower()
    start = 0
    while True:
        open_at = lowered.find("<entry", start)
        if open_at < 0:
            return None
        close_at = lowered.find("</entry>", open_at)
        if close_at < 0:
            return None
        if email in lowered[open_at:close_at]:
            return Hit("mailing_list", url, ())
        start = close_at + 8


ORACLES: Tuple[Tuple[str, Callable], ...] = (
    ("github_commit", github_commit_hit),
    ("github_profile", github_profile_hit),
    ("gravatar", gravatar_hit),
    ("pgp", pgp_hit),
    ("mailing_list", mailing_list_hit),
)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def corroborate_address(email: str, *, name: Optional[str] = None,
                        token: Optional[str] = None, http_get=None,
                        budget=None,
                        oracles: Optional[Sequence] = None) -> Corroboration:
    """Ask every corpus whether this exact address is real. Read-only."""
    email = normalize_email(email)
    if not email or "@" not in email:
        return Corroboration(email, reason="invalid_address",
                             checked_at=time.time())
    if not _enabled():
        return Corroboration(email, reason="disabled", checked_at=time.time())

    cache_key = f"{email}\x00{(name or '').strip().lower()}"
    cached = cache_get(_CACHE, cache_key)
    if cached is not None:
        return cached

    sources: List[str] = []
    names: List[str] = []
    evidence: Optional[str] = None
    for _key, oracle in (oracles if oracles is not None else ORACLES):
        try:
            hit = oracle(email, token=token, http_get=http_get, budget=budget)
        except Exception:
            # A broken or unreachable oracle degrades coverage. It must never
            # fail the lookup, and it must never be read as a negative.
            continue
        if hit is None:
            continue
        sources.append(hit.source)
        evidence = evidence or hit.url
        for candidate in hit.names:
            if candidate not in names:
                names.append(candidate)

    name_match: Optional[bool] = None
    if names and (name or "").strip():
        name_match = any(name_appears_in(name, candidate)
                         for candidate in names)

    result = Corroboration(
        email=email, sources=tuple(sources), names=tuple(names),
        name_match=name_match, evidence_url=evidence,
        reason="checked" if sources else "not_found", checked_at=time.time())
    cache_put(_CACHE, cache_key, result,
              _TTL_HIT if sources else _TTL_MISS, _CACHE_MAX)
    return result
