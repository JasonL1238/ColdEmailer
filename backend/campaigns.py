"""Campaigns: which run of outreach this company, contact or email came from.

Without them every number in the app is a lifetime total. "18% reply rate" over
nine months of different searches, different framings and different seniorities
is not a fact you can act on — the question is always *which batch worked*, and
the app could not answer it.

Three rules, each of them a rule this codebase already follows elsewhere and
which a campaign page is unusually good at breaking:

**A rate needs a sample.** Reused verbatim from `analytics` — same MIN_SAMPLE,
same `rate_of`. A campaign is exactly where "3 sent, 1 reply" wants to render
as 33%, and a new campaign is *always* small.

**Only verified replies count.** The legacy checker counted bounces,
auto-replies and the user's own messages; those are reported separately and
never inside a rate.

**Unassigned is a real answer.** Everything from before campaigns existed has
`campaign_id IS NULL` and stays that way. Backfilling it — guessing which
campaign a company from three months ago belonged to — would fabricate the one
thing this feature exists to measure. It is reported alongside instead, so the
campaign totals are never mistaken for the whole database.
"""
from typing import Any, Dict, List, Optional

from analytics import MIN_SAMPLE, rate_of


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def summarize(row: Dict[str, Any]) -> Dict[str, Any]:
    """One campaign, scored on the same terms as everything else."""
    sent = _int(row.get("sent"))
    replied = _int(row.get("replied"))
    enough = sent >= MIN_SAMPLE
    return {
        "id": row.get("id"),
        "name": row.get("name") or "Untitled campaign",
        "query": row.get("query") or "",
        "created_at": row.get("created_at"),
        "archived_at": row.get("archived_at"),
        "notes": row.get("notes") or "",
        "companies": _int(row.get("companies")),
        "contacts": _int(row.get("contacts")),
        "drafts": _int(row.get("drafts")),
        "sent": sent,
        "replied": replied,
        "bounced": _int(row.get("bounced")),
        "unverified": _int(row.get("unverified")),
        "last_sent_at": row.get("last_sent_at"),
        # Withheld below the floor rather than rounded down to something
        # confident. The UI shows counts in that case, as it does everywhere.
        "enough_data": enough,
        "rate": rate_of(replied, sent) if enough else None,
    }


def compare(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Best and worst campaign, or nothing.

    Needs two campaigns that each cleared the floor. Naming a winner out of one
    qualifying campaign restates that campaign; out of zero it is a guess. Same
    rule as the analytics headline, for the same reason.
    """
    scored = [s for s in summaries
              if s["enough_data"] and not s["archived_at"]]
    if len(scored) < 2:
        return {"best": None, "worst": None, "spread": None}
    ranked = sorted(scored, key=lambda s: s["rate"], reverse=True)
    best, worst = ranked[0], ranked[-1]
    return {"best": best, "worst": worst,
            "spread": round(best["rate"] - worst["rate"], 1)}


def build(rows: List[Dict[str, Any]],
          unassigned: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """The campaigns page, from one query plus the unassigned counts."""
    summaries = [summarize(row) for row in rows]
    live = [s for s in summaries if not s["archived_at"]]
    unassigned = unassigned or {}
    un_sent = _int(unassigned.get("sent"))
    un_replied = _int(unassigned.get("replied"))
    return {
        "campaigns": summaries,
        "headline": compare(summaries),
        "min_sample": MIN_SAMPLE,
        "totals": {
            "campaigns": len(summaries),
            "active": len(live),
            "sent": sum(s["sent"] for s in summaries),
            "replied": sum(s["replied"] for s in summaries),
        },
        # Reported, never folded in and never backfilled.
        "unassigned": {
            "companies": _int(unassigned.get("companies")),
            "contacts": _int(unassigned.get("contacts")),
            "sent": un_sent,
            "replied": un_replied,
            "enough_data": un_sent >= MIN_SAMPLE,
            "rate": rate_of(un_replied, un_sent) if un_sent >= MIN_SAMPLE else None,
        },
    }
