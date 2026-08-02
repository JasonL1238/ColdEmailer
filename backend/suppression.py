"""Addresses and domains this app must never write to.

A bounce says an address cannot receive mail. This says something different
and more important: it can, and it must not. Someone asked not to be
contacted, a company said route everything through one person, a colleague
went on the list by mistake — none of that is visible to any other check, and
until now the only way to honour it was to remember.

Three properties shape the design.

**It fails closed.** Every other gate in this app is deliberately optimistic —
`domain_has_mx` returning None means "could not check", and the send proceeds
so a DNS blip cannot block real mail. This one is the opposite. If the check
cannot be made, nothing goes out: the cost of a wrongly blocked email is an
error message, and the cost of a wrongly sent one is a person who asked to be
left alone hearing from you again.

**It is checked twice.** Once when a contact enters the database, so the
address never reaches a draft; and again in the send job, immediately before
the network call. The first is a convenience. The second is the guarantee —
rows predate the list, CSVs arrive with addresses already in them, and item 3
established that the send path is the one place that has to ask for itself.

**A domain covers its subdomains.** Blocking `acme.com` and then mailing
`careers.mail.acme.com` would be an obvious hole. Matching is on the labels,
not on substrings: `notacme.com` does not match `acme.com`, which a naive
`endswith` gets wrong in the direction that matters least — but `evil-acme.com`
matching would be a false block, and false blocks erode trust in the list
until someone turns it off.
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ADDRESS = "address"
DOMAIN = "domain"

# Exactly what the send path can emit — the lowercased, anchored form of
# email_sender._RECIPIENT_RE, deliberately character-for-character.
#
# Two failures sit either side of this pattern and both put mail in somebody's
# inbox. Too loose and `mailto:dana@acme.com` or `dana@acme.com,` is accepted
# and stored verbatim, matching no recipient that can ever exist — the entry
# looks like protection while that person keeps getting mail. Too strict and
# there is an address the sender will happily deliver to but the list cannot
# hold: a first attempt here rejected leading-hyphen domains as malformed,
# which made `dana@-acme.com` sendable and unblockable. Tidiness that creates
# an address you cannot block is exactly backwards for this feature.
#
# So: whatever the sender accepts, this accepts. If that pattern is ever
# loosened, loosen this one with it.
_SENDABLE = r"[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)+"
_ADDRESS_RE = re.compile(f"^{_SENDABLE}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
# The artefacts of copying an address out of a sentence, a list, or a link.
_PASTE_JUNK = ".,;:!?<>()[]{}\"'"


def normalize(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """`raw` → (value, kind), or (None, None) when it is neither.

    Accepts an address, a bare domain, `@domain`, or `*@domain` — every form
    people actually type or paste when they mean "stop writing to this person"
    or "stop writing to anyone here".
    """
    value = (raw or "").strip().lower()

    # More than one recipient is a refusal, never a silent pick.
    #
    # Selecting two people in Gmail and copying gives you
    # `Dana Lee <dana@acme.com>, Sam Ray <sam@acme.com>`. Taking the last
    # bracketed address — which an end-anchored search does — accepted that
    # with a 200, showed one row in Settings, and left Dana mailable. The
    # hand-typed `dana@acme.com, sam@acme.com` was already rejected outright,
    # so the form people actually paste was the one that failed quietly.
    bracketed = re.findall(r"<([^<>]+)>", value)
    if len({b.strip() for b in bracketed}) > 1:
        return None, None
    if bracketed:
        # A display name may itself contain an address ("dana@acme.com
        # <sam@acme.com>"); if the text outside the brackets names a different
        # one, that is two recipients again.
        outside = re.sub(r"<[^<>]*>", " ", value)
        others = {a for a in re.findall(_SENDABLE, outside)
                  if a != bracketed[0].strip()}
        if others:
            return None, None
        value = bracketed[0].strip()

    value = value.strip("<>").strip()
    if value.startswith("mailto:"):
        value = value[len("mailto:"):].strip()

    # The raw value gets first refusal, and stripping is only a fallback for
    # something that did not parse. Stripping unconditionally turned the
    # (absurd but sendable) address `.@acme.com` into a block on the whole of
    # acme.com — trading an unblockable address for an over-block, which is
    # the worse of the two.
    for candidate in (value, value.strip(_PASTE_JUNK).strip()):
        found = _classify(candidate)
        if found[0]:
            return found
    return None, None


def _classify(value: str) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    # `@acme.com` and `*@acme.com` are unambiguous and mean the whole domain.
    if value.startswith("@") or value.startswith("*@") or value.startswith("*."):
        bare = value.lstrip("*").lstrip("@.").strip()
        return (bare, DOMAIN) if _DOMAIN_RE.match(bare) else (None, None)
    if "@" in value:
        return (value, ADDRESS) if _ADDRESS_RE.match(value) else (None, None)
    return (value, DOMAIN) if _DOMAIN_RE.match(value) else (None, None)


def domain_of(address: str) -> str:
    return (address or "").strip().lower().rsplit("@", 1)[-1]


def _domain_chain(domain: str) -> List[str]:
    """`a.b.example.com` → itself and every parent, so one lookup set covers
    subdomains without a scan of the whole list."""
    parts = [p for p in (domain or "").split(".") if p]
    return [".".join(parts[i:]) for i in range(len(parts) - 1)] if len(parts) > 1 \
        else ([domain] if domain else [])


# Providers where dots in the local part are not part of the address.
_DOTLESS_LOCAL_DOMAINS = {"gmail.com"}
# Two names for one mailbox. googlemail.com was the domain Google issued in
# Germany, the UK and Russia, and the two remain interchangeable — so blocking
# dana@gmail.com and then mailing dana@googlemail.com reaches the same person
# at an address they never gave you.
_DOMAIN_ALIASES = {"googlemail.com": "gmail.com"}


def canonical(address: str) -> str:
    """The mailbox this address actually delivers to.

    `dana+jobs@acme.com` is `dana@acme.com` almost everywhere, and
    `d.a.n.a@gmail.com` is `dana@gmail.com` at Google. Both are the same human
    receiving the same message, so blocking one and mailing the other is not a
    technicality — it is the person who asked you to stop hearing from you
    again, from an address they never gave you.

    Dot-folding is limited to the domains where it is a documented provider
    rule. Applying it everywhere would block `j.smith@acme.com` because
    `jsmith@acme.com` is on the list, which is a different person.
    """
    value = (address or "").strip().lower()
    if "@" not in value:
        return value
    local, _, domain = value.partition("@")
    domain = _DOMAIN_ALIASES.get(domain, domain)
    # Only when something survives the tag. `+tag@acme.com` is a real (if odd)
    # mailbox, and folding it to `@acme.com` made every all-tag address at that
    # domain collapse to one key — cross-blocking unrelated people.
    head = local.split("+", 1)[0]
    if head:
        local = head
    if domain in _DOTLESS_LOCAL_DOMAINS:
        local = local.replace(".", "")
    return f"{local}@{domain}"


def match(address: str, entries: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The suppression entry that blocks `address`, or None.

    Returns the entry rather than a bool so the caller can say *why* — "you
    blocked this address on 3 June" is actionable, "blocked" is not.

    Addresses are compared as mailboxes, not as strings, so a tag or a dot
    cannot walk past an entry. The stored value stays exactly what the user
    typed, because that is what the refusal quotes back to them.
    """
    value = (address or "").strip().lower()
    if not value or "@" not in value:
        return None
    by_address: Dict[str, Dict[str, Any]] = {}
    by_domain: Dict[str, Dict[str, Any]] = {}
    for entry in entries or []:
        kind = (entry.get("kind") or "").lower()
        key = (entry.get("value") or "").strip().lower()
        if not key:
            continue
        if kind == ADDRESS:
            by_address.setdefault(canonical(key), entry)
        else:
            by_domain.setdefault(key, entry)
    hit = by_address.get(canonical(value))
    if hit:
        return hit
    for candidate in _domain_chain(domain_of(value)):
        if candidate in by_domain:
            return by_domain[candidate]
    return None


def blocked_reason(entry: Optional[Dict[str, Any]], address: str) -> str:
    """What to tell the user, naming the entry that did it.

    Never a bare "blocked": the user has to be able to find and undo it, and
    a domain entry blocking one address is the case they will not expect.
    """
    if not entry:
        return ""
    kind = (entry.get("kind") or "").lower()
    note = (entry.get("reason") or "").strip()
    what = (f"the whole domain @{entry.get('value')}" if kind == DOMAIN
            else f"{entry.get('value')}")
    when = (entry.get("created_at") or "")[:10]
    tail = f" — “{note}”" if note else ""
    return (f"{address} is on your do-not-contact list, via {what}"
            + (f" (added {when})" if when else "") + tail
            + ". Remove it in Settings if that is wrong.")


def index(entries: Iterable[Dict[str, Any]]) -> Set[str]:
    """A flat set for callers that only need yes/no over many addresses."""
    return {f"{(e.get('kind') or '').lower()}:{(e.get('value') or '').lower()}"
            for e in entries or []}
