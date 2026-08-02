"""What actually earns replies.

The app has always been able to say how many emails went out and how many came
back. It has never been able to say *which kind* came back — whether the
coffee-chat framing beats the application one, whether writing to a named
person really outperforms info@, whether the second follow-up is worth sending
at all. Without that, every send is a guess repeated.

Two rules run through everything here, and both are about not lying:

**Only verified replies count.** The legacy checker counted bounces,
auto-replies and the user's own messages in the thread. Those flags are
reported as unverified elsewhere and are excluded from every rate below —
a number presented as "reply rate" has to be one.

**A rate needs a sample.** Two sends and one reply is not "50%", it is noise
with a percent sign on it, and at this app's volumes most segments are that
small. Segments below MIN_SAMPLE carry `enough_data: False` and their `rate`
is None, so the UI shows counts instead of inventing a signal. This is the
same discipline the reply-rate headline already follows.

Pure functions over rows: the queries live in db.py, the arithmetic lives here
where it can be tested without a database.
"""
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

# Below this many *sent* emails a segment reports counts only. Ten is low
# enough that a real user sees most of their segments light up within a week of
# sending, and high enough that one lucky reply cannot read as 50%.
MIN_SAMPLE = 10


def _verified_reply(row: Dict[str, Any]) -> bool:
    """A reply this app is willing to state as fact."""
    return bool(row.get("has_response")) and bool(row.get("response_verified_at"))


def segment(rows: List[Dict[str, Any]], key, label=None) -> List[Dict[str, Any]]:
    """Group sent emails by `key(row)` and score each group.

    `key` returning None drops the row — "no data" is a different thing from a
    bucket called "unknown", and mixing them makes a segment look worse or
    better than the evidence supports. The dropped count comes back on every
    entry as `_dropped` so the UI can say "12 of 40 sends could be attributed"
    rather than "nothing sent in this segment", which is false.
    """
    buckets: Dict[Any, Dict[str, Any]] = {}
    dropped = 0
    for row in rows:
        bucket_key = key(row)
        if bucket_key is None:
            dropped += 1
            continue
        entry = buckets.setdefault(bucket_key, {
            "key": bucket_key,
            "label": (label(row) if label else str(bucket_key)),
            "sent": 0, "replied": 0, "unverified": 0,
        })
        entry["sent"] += 1
        if _verified_reply(row):
            entry["replied"] += 1
        elif row.get("has_response"):
            entry["unverified"] += 1

    out = []
    for entry in buckets.values():
        entry["enough_data"] = entry["sent"] >= MIN_SAMPLE
        entry["rate"] = (rate_of(entry["replied"], entry["sent"])
                         if entry["enough_data"] else None)
        entry["dropped"] = dropped
        out.append(entry)
    # Most-sent first, so the segments the user has actually invested in lead.
    out.sort(key=lambda e: (-e["sent"], e["label"]))
    return out


def rate_of(replied: int, sent: int) -> Optional[float]:
    """The single implementation of "what percentage replied".

    Exported so the frontend can render a number rather than recompute one:
    JS rounds halves up and Python rounds them to even, so 1 of 16 came out as
    6.3% in the headline tile and 6.2% in the segment card directly beneath it,
    for the very same rows.
    """
    if not sent:
        return None
    return round(replied / sent * 100, 1)


def hours_to_reply(rows: List[Dict[str, Any]]) -> List[float]:
    """How long each verified reply took, in hours."""
    gaps = []
    for row in rows:
        if not _verified_reply(row):
            continue
        try:
            sent = datetime.fromisoformat(row["sent_at"])
            replied = datetime.fromisoformat(row["response_at"])
        except (TypeError, ValueError, KeyError):
            continue
        delta = (replied - sent).total_seconds() / 3600.0
        # A reply timestamped before its own send is a clock or import artefact,
        # not a fast answer. Dropped rather than shown as a negative wait.
        if delta >= 0:
            gaps.append(round(delta, 2))
    return sorted(gaps)


def distribution(values: List[float]) -> Dict[str, Optional[float]]:
    """Median and quartiles, or Nones when there is nothing to describe.

    Median rather than mean: one reply three weeks late would drag an average
    somewhere no individual reply ever was, and "when should I stop waiting"
    is the question this answers.
    """
    if not values:
        return {"count": 0, "median": None, "p25": None, "p75": None,
                "fastest": None, "slowest": None}
    ordered = sorted(values)

    def _at(fraction: float) -> float:
        # Nearest-rank, half *up*, spelled out. Interpolating between two
        # samples invents a wait no reply had, which is the wrong trade for a
        # handful of points — but plain round() is banker's rounding, so the
        # tie between the two middle samples broke upward or downward with the
        # parity of the index. Four replies gave a "median" with three of them
        # at or below it; six gave the lower middle. Same word, two meanings.
        index = math.floor(fraction * (len(ordered) - 1) + 0.5)
        return ordered[min(len(ordered) - 1, max(0, index))]

    return {"count": len(ordered), "median": _at(0.5), "p25": _at(0.25),
            "p75": _at(0.75), "fastest": ordered[0], "slowest": ordered[-1]}


def best_and_worst(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The comparison worth acting on, or nothing.

    Requires two segments that each cleared MIN_SAMPLE. Naming a "best" out of
    one qualifying group is just restating that group, and naming one out of
    zero is a guess.
    """
    scored = [s for s in segments if s["enough_data"]]
    if len(scored) < 2:
        return {"best": None, "worst": None, "spread": None}
    ranked = sorted(scored, key=lambda s: s["rate"], reverse=True)
    best, worst = ranked[0], ranked[-1]
    return {"best": best, "worst": worst,
            "spread": round(best["rate"] - worst["rate"], 1)}


def _seniority_band(rank: Optional[int]) -> Optional[str]:
    """contacts.seniority_rank is 1 (most senior) upward, default 20."""
    if rank is None:
        return None
    try:
        value = int(rank)
    except (TypeError, ValueError):
        return None
    if value <= 3:
        return "Founder / C-level"
    if value <= 8:
        return "VP / Director"
    if value <= 15:
        return "Manager / Lead"
    return "Individual contributor"


EMAIL_KIND_LABELS = {
    "personal": "Named person",
    "generic": "Role inbox (info@, careers@)",
    "named_unmatched": "Address doesn't match the name",
    "unknown": "Unclassified",
}


def _rung(row: Dict[str, Any]) -> Optional[int]:
    """Which follow-up this was, or None when the row cannot say.

    `follow_up_step` arrived with the cadence feature and its migration
    backfilled every existing row with 0, while the legacy JSON import never
    set it at all. So a follow-up sent before that column existed carries
    step 0 — indistinguishable from a first contact unless the older
    `is_follow_up` flag is consulted. Folding those into "First contact" did
    not just mislabel them: it merged a chased population into the un-chased
    one and reported a rate for the mixture, which is the specific thing this
    module exists not to do. Unknown rungs are dropped instead.
    """
    step = row.get("follow_up_step")
    try:
        step = int(step or 0)
    except (TypeError, ValueError):
        step = 0
    if step > 0:
        return step
    if row.get("is_follow_up") or row.get("original_email_id"):
        return None                     # a follow-up of unknown rung
    return 0


def build(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every segment, from one pass of sent emails."""
    gaps = hours_to_reply(rows)
    by_kind = segment(
        rows,
        key=lambda r: r.get("contact_email_kind") or None,
        label=lambda r: EMAIL_KIND_LABELS.get(r.get("contact_email_kind"),
                                              r.get("contact_email_kind")))
    return {
        "sent": len(rows),
        "replied": sum(1 for r in rows if _verified_reply(r)),
        # Rendered by the page rather than recomputed there, so the tile and
        # the segment card below it cannot round the same rows differently.
        "rate": rate_of(sum(1 for r in rows if _verified_reply(r)), len(rows))
        if len(rows) >= MIN_SAMPLE else None,
        "unverified": sum(1 for r in rows
                          if r.get("has_response") and not _verified_reply(r)),
        "min_sample": MIN_SAMPLE,
        "time_to_reply_hours": {
            **distribution(gaps),
            # Replies whose timestamps could not produce a wait. Without this
            # the page printed "no replies yet" beside "Replied 5".
            "excluded": sum(1 for r in rows if _verified_reply(r)) - len(gaps),
        },
        "segments": {
            "email_type": segment(rows, key=lambda r: r.get("email_type") or None),
            "email_kind": by_kind,
            "seniority": segment(
                rows, key=lambda r: _seniority_band(r.get("seniority_rank"))),
            # Keyed on the id, labelled with the name: this app deliberately
            # keeps two same-named companies on different domains as separate
            # rows, and grouping by name merged their sends into one bucket
            # that cleared the sample floor neither of them reached.
            "company": segment(rows, key=lambda r: r.get("company_id") or None,
                               label=lambda r: r.get("company_name") or "—"),
            "written_by": segment(
                rows,
                key=lambda r: ("Plain template" if r.get("used_template_fallback")
                               else "AI"),
            ),
            # Does chasing actually work? Rung 0 is the first contact.
            "follow_up_step": segment(
                rows, key=_rung,
                label=lambda r: ("First contact" if _rung(r) == 0
                                 else f"Follow-up {_rung(r)}")),
        },
        "headline": {
            "email_type": best_and_worst(
                segment(rows, key=lambda r: r.get("email_type") or None)),
            "email_kind": best_and_worst(by_kind),
        },
    }
