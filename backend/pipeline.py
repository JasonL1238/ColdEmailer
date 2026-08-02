"""Where every contact actually stands.

The Database page is a list of rows and the Emails page is a list of drafts.
Neither answers the question you ask before deciding what to do next — *how
many people are waiting on me, and how many am I waiting on* — so the honest
answer to "what's my pipeline" was to scroll and count.

The stage is **derived from evidence, never read off `contacts.status`.** That
column is written from four places and reset from none: delete a draft and the
contact stays `drafted` forever, so a board that grouped on it would file
people under "Drafted" who have no draft, and they would never be written to
again precisely because the board said they were handled. Every stage below
comes from the emails that exist.

Two rules carried in from the rest of the app:

**Only a verified reply is a reply.** The legacy checker counted bounces,
auto-replies and the user's own thread messages. A contact whose only reply
flag is unverified stays in `awaiting` and is marked, rather than being filed
under the one stage that means "done".

**A dead address is not a pending one.** A bounced contact sits in its own
column instead of ageing quietly in `awaiting`, where the only visible
difference from a live prospect is that they never answer.

Pure functions over rows: the query lives in db.py, the reasoning lives here
where it can be tested without a database.
"""
from typing import Any, Dict, List, Optional

# Left to right, the order a contact actually moves through. `no_address` is a
# stall rather than a step — it sits where the work is blocked, so it reads in
# place rather than at the end.
STAGES = [
    {"key": "ready", "label": "Ready to write",
     "hint": "Has an address and no draft yet."},
    {"key": "no_address", "label": "No address",
     "hint": "Nothing can be sent until this person has an email address — "
             "including anything already written for them."},
    {"key": "drafted", "label": "Drafted",
     "hint": "Written but not sent. Includes follow-ups drafted for people "
             "you have already emailed."},
    {"key": "awaiting", "label": "Awaiting reply",
     "hint": "Delivered. Waiting on them."},
    {"key": "replied", "label": "Replied",
     "hint": "A reply this app has verified."},
    {"key": "bounced", "label": "Bounced",
     "hint": "The address was rejected as undeliverable. Fix the address to resume."},
    {"key": "archived", "label": "Archived",
     "hint": "Set aside by you. No outreach."},
]
STAGE_KEYS = [s["key"] for s in STAGES]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def stage_of(row: Dict[str, Any]) -> str:
    """The one stage this contact is really in.

    Read top to bottom as "is there anything left to do, and whose move is it".

    Exits come first — archived, replied, bounced — because none of them leaves
    work outstanding. A reply beats a bounce among those: people do answer from
    a second address after the first one fails, the same precedence the reply
    checker already uses.

    Then blockers, then work the user owes, then work they are waiting on.
    Two orderings here were wrong the first time and both hid pending work:

    * `no_address` outranks history. A contact who was written to and whose
      address was later blanked cannot be chased at all; filing them under
      "Awaiting reply" says the ball is in their court, and the user never
      finds out they could not reach them.
    * `drafted` outranks `awaiting`. Every follow-up this app drafts is by
      definition written to somebody already delivered to, so checking
      delivery first put the entire follow-up backlog in "Waiting on them"
      and reported the work as zero.
    """
    if (row.get("status") or "").strip().lower() == "archived":
        return "archived"                       # an explicit decision by the user
    if _int(row.get("verified_reply_count")):
        return "replied"
    if row.get("bounced_at") or _int(row.get("bounced_email_count")):
        return "bounced"
    if not (row.get("email") or "").strip():
        return "no_address"
    if _int(row.get("pending_draft_count")):
        return "drafted"
    # A queued send is written and armed: it belongs beside the other drafts
    # rather than in "Ready to write", which would invite a second one.
    if _int(row.get("queued_count")):
        return "drafted"
    if _int(row.get("delivered_count")) or _int(row.get("unconfirmed_count")):
        return "awaiting"
    return "ready"


def card(row: Dict[str, Any]) -> Dict[str, Any]:
    """One contact, reduced to what a board card can show and say why."""
    stage = stage_of(row)
    unverified = _int(row.get("unverified_reply_count"))
    drafts = _int(row.get("pending_draft_count"))
    return {
        "id": row.get("id"),
        "name": row.get("name") or row.get("email") or "Unnamed contact",
        "email": row.get("email") or "",
        "role": row.get("role") or "",
        "company_id": row.get("company_id"),
        "company_name": row.get("company_name") or "",
        "stage": stage,
        "last_sent_at": row.get("last_sent_at"),
        "drafts": drafts,
        "sent": _int(row.get("delivered_count")),
        "queued": _int(row.get("queued_count")),
        "unconfirmed": _int(row.get("unconfirmed_count")),
        # Shown on an `awaiting` card, not counted as a reply: an unverified
        # flag came from the checker that also counted bounces and our own
        # messages, so it is a reason to re-verify rather than a result.
        "reply_unverified": bool(unverified and not _int(row.get("verified_reply_count"))),
        # Reported whatever the stage, not just on `no_address`. A card sitting
        # under Bounced or Archived with finished writing attached to it is the
        # case where an unsendable draft is most thoroughly hidden.
        "blocked_draft": bool(drafts and not (row.get("email") or "").strip()),
        "follow_ups_sent": _int(row.get("follow_up_count")),
    }


def _ordered(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Most recent activity first, never-contacted last, ties alphabetical.

    Two stable passes rather than one key, because the two fields sort in
    opposite directions. `last_sent_at` is None for everyone before their
    first send and so cannot be compared against a timestamp at all — the
    empty string stands in, which lands those cards at the bottom of a
    descending sort, exactly where they belong.
    """
    out = sorted(cards, key=lambda e: e["name"].lower())
    out.sort(key=lambda e: str(e["last_sent_at"] or ""), reverse=True)
    return out


def build(rows: List[Dict[str, Any]], limit: int = 100) -> Dict[str, Any]:
    """Every contact, filed under the stage the evidence puts them in.

    `limit` bounds each column, not the whole board: a thousand contacts in
    `ready` must not stop the four cards in `drafted` from rendering. The
    remainder is reported per column rather than dropped silently.
    """
    columns: Dict[str, List[Dict[str, Any]]] = {key: [] for key in STAGE_KEYS}
    drift = 0
    for row in rows:
        entry = card(row)
        columns[entry["stage"]].append(entry)
        # How far `contacts.status` has wandered from the evidence. Reported,
        # not repaired: this endpoint reads, and something that rewrote real
        # rows as a side effect of opening a page would be the wrong shape of
        # fix even when the new value is right.
        if _disagrees(row, entry["stage"]):
            drift += 1

    out = []
    for stage in STAGES:
        cards = _ordered(columns[stage["key"]])
        out.append({**stage, "total": len(cards), "cards": cards[:limit],
                    "hidden": max(0, len(cards) - limit)})
    return {
        "stages": out,
        "total": len(rows),
        "status_drift": drift,
        # The two numbers the board exists to put side by side. A queued send
        # is deliberately in neither: the scheduler will send it without the
        # user, and it has not reached the recipient either.
        "waiting_on_you": sum(1 for c in columns["ready"] + columns["drafted"]
                              if not (c["queued"] and not c["drafts"])),
        "waiting_on_them": len(columns["awaiting"]),
        "queued": sum(c["queued"] for c in columns["drafted"]),
    }


# What the stored column would have said, for drift accounting only. `new` and
# `sent` are the two values it uses that this module spells differently.
_STORED_EQUIVALENT = {
    "new": "ready", "drafted": "drafted", "sent": "awaiting",
    "replied": "replied", "archived": "archived",
}

# Stages `contacts.status` has no word for. A contact with no address, or one
# whose address bounced, cannot be represented in that column at all, so
# counting every one of them as a disagreement made drift a tally of how many
# people had no email address — and the banner that reports it claims the
# column is wrong, which for these is not true.
_COMPATIBLE = {
    "no_address": {"ready", "drafted"},
    "bounced": {"awaiting", "ready", "drafted"},
}


def _stored_stage(row: Dict[str, Any]) -> Optional[str]:
    return _STORED_EQUIVALENT.get((row.get("status") or "").strip().lower())


def _disagrees(row: Dict[str, Any], stage: str) -> bool:
    stored = _stored_stage(row)
    if stored is None:                  # a value this module cannot translate
        return False
    return stored not in _COMPATIBLE.get(stage, {stage})
