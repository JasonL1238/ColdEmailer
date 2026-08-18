"""Company-name and domain normalisation, shared by the modules that match them.

Stdlib only, and imported at module level everywhere. It exists because db.py,
enrichment.py, contact_enrich.py and person_finder.py each grew their own copy
of these rules and had already drifted — a second `registered_domain` that did
not know about two-part TLDs made two unrelated `.co.uk` employers compare
equal. Keeping the rules here means "same company" means one thing.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

# Words that carry no identifying signal when matching a company name.
COMPANY_NAME_STOPWORDS = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "the",
    "group", "labs", "lab", "technologies", "technology", "tech", "systems",
    "solutions", "software", "ai", "io",
}

# Second-level labels that are really part of the suffix ("foo.co.uk").
_TWO_PART_TLD_HEADS = {"co", "com", "org", "net", "ac", "gov"}


def name_tokens(name: Optional[str]) -> List[str]:
    """Identifying lowercase tokens of a company name, stopwords dropped."""
    tokens = re.split(r"[^a-z0-9]+", (name or "").lower())
    return [t for t in tokens if t and t not in COMPANY_NAME_STOPWORDS]


def company_name_key(name: Optional[str]) -> Optional[str]:
    """Normalize a company name for duplicate detection ('Acme Inc' == 'acme')."""
    if not name:
        return None
    return "".join(name_tokens(name)) or None


def registered_domain(url_or_domain: str) -> Optional[str]:
    """'https://www.foo.co.uk/x' -> 'foo.co.uk' (best effort, no PSL dep).

    Accepts a URL, a bare host, or an address — the local part is dropped —
    and returns None for input with no host at all.
    """
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
    if (len(parts) >= 3 and parts[-2] in _TWO_PART_TLD_HEADS
            and len(parts[-1]) == 2):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])
