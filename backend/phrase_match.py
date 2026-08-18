"""Token- and phrase-boundary matching, shared by the modules that ask
"does this name appear in this blob".

`contact_enrich`, `deep_research` and `person_finder` each wrote out the same
`(?<![a-z0-9])...(?![a-z0-9])` lookaround, and the separator classes in the
phrase variants had already drifted apart with nothing recording which was
deliberate. The separator stays a per-call-site argument for exactly that
reason: `contact_enrich` accepts commas and dots inside a company name,
`deep_research` does not, and unifying them changes what matches.

Everything here lowercases nothing — callers pass a blob they have already
lowercased, because they lowercase once and match many times.
"""
from __future__ import annotations

import re
from typing import Sequence

# A company/role token must not be glued to another alphanumeric: "ai" should
# match "Acme AI" and never "Chain*ai*nalysis".
_LEFT = r"(?<![a-z0-9])"
_RIGHT = r"(?![a-z0-9])"


def token_in(token: str, blob: str) -> bool:
    """True when `token` appears in `blob` on alphanumeric boundaries."""
    if not token or not blob:
        return False
    return bool(re.search(rf"{_LEFT}{re.escape(token)}{_RIGHT}", blob))


def all_tokens_in(tokens: Sequence[str], blob: str) -> bool:
    """True when EVERY token appears. Empty token list is False, never True —
    `all([])` is True, which would make an empty hint match everything."""
    return bool(tokens) and all(token_in(t, blob) for t in tokens)


def phrase_in(phrase: str, blob: str, separators: str = r"[\s\-_/]+") -> bool:
    """True when a multi-word phrase appears, allowing `separators` between
    its words — so "deep research" also matches "deep-research"."""
    if not phrase or not blob:
        return False
    pattern = re.escape(phrase).replace(r"\ ", separators)
    return bool(re.search(rf"{_LEFT}{pattern}{_RIGHT}", blob))
