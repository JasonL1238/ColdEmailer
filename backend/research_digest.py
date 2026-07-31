"""Consolidate repeated deep dives on one company into a single view.

Deep research used to overwrite `companies.deep_intel` on every run, so a
second dive with different criteria silently erased the first one's findings
and the criteria that produced them. Runs are now appended to
``deep_intel["runs"]`` and merged for display here.

Everything in this module is pure: it takes rows and returns dicts, so the
merge rules can be tested without a database, a network, or an LLM.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# Bullet categories carried per run. These describe the *company*, so they are
# deduped across runs; contacts are the part that genuinely differs by criteria.
INTEL_LISTS = (
    "key_changes",
    "improvements",
    "policy_highlights",
    "differentiators",
    "talking_points",
)

# Enough history to compare a few angles on one company without letting a
# single JSON column grow without bound.
MAX_RUNS_KEPT = 10

_CRITERIA_NOTE_RE = re.compile(r"criteria match:\s*([^;]+)", re.I)
_BARE_CRITERIA_RE = re.compile(r"\bcriteria match\b", re.I)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    """Comparison key for de-duping bullets: case- and punctuation-insensitive."""
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


def _string_list(value: Any, limit: int = 24) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        text = _clean(item)
        if not text:
            continue
        key = _norm(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def matched_terms_from_notes(notes: Any) -> List[str]:
    """Pull the criteria terms deep research stamped onto a contact's notes.

    Notes look like ``"Criteria match: Penn, ML; <other notes>"``. A bare
    "Criteria match" with no terms still counts as a match, just an unlabelled
    one — callers get an empty list and should treat the contact as matched.
    """
    text = _clean(notes)
    if not text:
        return []
    hit = _CRITERIA_NOTE_RE.search(text)
    if not hit:
        return []
    return [t for t in (_clean(p) for p in hit.group(1).split(",")) if t]


def contact_is_criteria_match(notes: Any) -> bool:
    return bool(_BARE_CRITERIA_RE.search(_clean(notes)))


def build_run(
    intel: Dict[str, Any],
    *,
    criteria: Optional[str],
    job_id: Optional[str] = None,
    researched_at: Optional[str] = None,
    contacts_saved: Optional[int] = None,
    criteria_matches: Optional[int] = None,
) -> Dict[str, Any]:
    """One deep dive, recorded so a later dive cannot erase it."""
    run: Dict[str, Any] = {
        "criteria": _clean(criteria) or None,
        "researched_at": researched_at,
        "job_id": job_id,
        "employee_estimate": intel.get("employee_estimate"),
        "news_snippets": _string_list(intel.get("news_snippets"), limit=10),
    }
    for key in INTEL_LISTS:
        run[key] = _string_list(intel.get(key))
    if contacts_saved is not None:
        run["contacts_saved"] = int(contacts_saved)
    if criteria_matches is not None:
        run["criteria_matches"] = int(criteria_matches)
    return run


def append_run(
    prior_intel: Any,
    fresh_intel: Dict[str, Any],
    *,
    criteria: Optional[str],
    job_id: Optional[str] = None,
    researched_at: Optional[str] = None,
    contacts_saved: Optional[int] = None,
    criteria_matches: Optional[int] = None,
) -> Dict[str, Any]:
    """Return `fresh_intel` with this run appended to the company's history.

    The flat top-level keys keep meaning "most recent run" so existing readers
    and stored rows carry on working; `runs` is the part that accumulates.
    Re-persisting the same job_id replaces that run rather than duplicating it,
    because a single job may write the company row more than once.
    """
    merged = dict(fresh_intel or {})
    prior_runs: List[Dict[str, Any]] = []
    if isinstance(prior_intel, dict):
        raw = prior_intel.get("runs")
        if isinstance(raw, list):
            prior_runs = [r for r in raw if isinstance(r, dict)]
        elif prior_intel:
            # Pre-`runs` row: fold the old flat blob in as the first run so
            # upgrading does not throw away what was already researched.
            legacy = build_run(
                prior_intel,
                criteria=prior_intel.get("contact_criteria"),
                researched_at=prior_intel.get("researched_at"),
            )
            if any(legacy.get(k) for k in INTEL_LISTS) or legacy.get("criteria"):
                prior_runs = [legacy]

    current = build_run(
        merged,
        criteria=criteria,
        job_id=job_id,
        researched_at=researched_at or merged.get("researched_at"),
        contacts_saved=contacts_saved,
        criteria_matches=criteria_matches,
    )
    kept = [
        r for r in prior_runs
        if not (job_id and r.get("job_id") == job_id)
    ]
    kept.append(current)
    merged["runs"] = kept[-MAX_RUNS_KEPT:]
    return merged


def runs_of(deep_intel: Any) -> List[Dict[str, Any]]:
    """Every recorded run, oldest first. Falls back to the legacy flat shape."""
    if not isinstance(deep_intel, dict) or not deep_intel:
        return []
    raw = deep_intel.get("runs")
    if isinstance(raw, list):
        runs = [r for r in raw if isinstance(r, dict)]
        if runs:
            return runs
    legacy = build_run(
        deep_intel,
        criteria=deep_intel.get("contact_criteria"),
        researched_at=deep_intel.get("researched_at"),
    )
    if any(legacy.get(k) for k in INTEL_LISTS) or legacy.get("criteria"):
        return [legacy]
    return []


def merge_intel(runs: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Union each bullet category across runs, tagged with what asked for it.

    First occurrence wins the wording; later runs that repeat a bullet only add
    their criteria to its tag list. That is what makes two dives on one company
    read as one section instead of two near-identical ones.
    """
    merged: Dict[str, List[Dict[str, Any]]] = {key: [] for key in INTEL_LISTS}
    index: Dict[str, Dict[str, Dict[str, Any]]] = {key: {} for key in INTEL_LISTS}
    for run in runs:
        label = _clean(run.get("criteria")) or None
        for key in INTEL_LISTS:
            for text in _string_list(run.get(key)):
                norm = _norm(text)
                if not norm:
                    continue
                entry = index[key].get(norm)
                if entry is None:
                    entry = {"text": text, "criteria": []}
                    index[key][norm] = entry
                    merged[key].append(entry)
                if label and label not in entry["criteria"]:
                    entry["criteria"].append(label)
    return merged


def _contact_summary(contact: Dict[str, Any]) -> Dict[str, Any]:
    notes = contact.get("notes")
    return {
        "id": contact.get("id"),
        "name": _clean(contact.get("name")) or None,
        "role": _clean(contact.get("role")) or None,
        "email": contact.get("email") or None,
        "linkedin_url": contact.get("linkedin_url") or None,
        "status": contact.get("status"),
        "seniority_rank": contact.get("seniority_rank"),
        "person_verified": bool(contact.get("person_verified")),
        "email_verified": bool(contact.get("email_verified")),
        "matched_terms": matched_terms_from_notes(notes),
        "criteria_match": contact_is_criteria_match(notes),
    }


def group_contacts(contacts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Bucket a company's contacts by the criteria term that found them.

    A person matched by two dives appears under both terms — deliberately.
    The whole point of the consolidated view is seeing that overlap.
    """
    summaries = [_contact_summary(c) for c in contacts or []]
    # Keyed case-insensitively so "Northwestern Alum" and "northwestern alum"
    # are one bucket. Terms that merely look similar ("Penn" vs "Penn State")
    # stay separate on purpose — they are not the same criteria.
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    labels: Dict[str, str] = {}
    unmatched: List[Dict[str, Any]] = []

    def _bucket(term: str, summary: Dict[str, Any]) -> None:
        key = term.lower()
        labels.setdefault(key, term)
        people = buckets.setdefault(key, [])
        if not any(p["id"] == summary["id"] for p in people):
            people.append(summary)

    for summary in summaries:
        terms = summary["matched_terms"]
        if terms:
            for term in terms:
                _bucket(term, summary)
        elif summary["criteria_match"]:
            _bucket("Criteria match", summary)
        else:
            unmatched.append(summary)

    def _rank(entry: Dict[str, Any]) -> tuple:
        return (entry.get("seniority_rank") if entry.get("seniority_rank")
                is not None else 99, entry.get("name") or "")

    groups = [
        {"term": labels[key], "contacts": sorted(people, key=_rank)}
        for key, people in sorted(buckets.items())
    ]
    return {
        "total": len(summaries),
        "with_email": sum(1 for s in summaries if s["email"]),
        "with_linkedin": sum(1 for s in summaries if s["linkedin_url"]),
        "matched": sum(1 for s in summaries if s["matched_terms"] or s["criteria_match"]),
        "groups": groups,
        "unmatched": sorted(unmatched, key=_rank),
    }


def consolidate(company: Dict[str, Any],
                contacts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """One company's whole deep-research history as a single section."""
    runs = runs_of(company.get("deep_intel"))
    stamps = [r.get("researched_at") for r in runs if r.get("researched_at")]
    criteria: List[str] = []
    for run in runs:
        label = _clean(run.get("criteria"))
        if label and label not in criteria:
            criteria.append(label)

    news: List[str] = []
    seen_news = set()
    employee_estimate = None
    for run in runs:
        if run.get("employee_estimate"):
            employee_estimate = run["employee_estimate"]
        for snippet in _string_list(run.get("news_snippets"), limit=10):
            key = _norm(snippet)
            if key and key not in seen_news:
                seen_news.add(key)
                news.append(snippet)

    return {
        "company": {
            "id": company.get("id"),
            "name": company.get("name"),
            "domain": company.get("domain"),
            "url": company.get("url"),
            "scrape_status": company.get("scrape_status"),
            "research_quality": company.get("research_quality"),
            "summary": company.get("summary"),
        },
        "run_count": len(runs),
        "criteria": criteria,
        "first_researched_at": min(stamps) if stamps else None,
        "last_researched_at": max(stamps) if stamps else None,
        "employee_estimate": employee_estimate,
        "intel": merge_intel(runs),
        "news_snippets": news[:10],
        "contacts": group_contacts(contacts),
        "runs": [
            {
                "criteria": r.get("criteria"),
                "researched_at": r.get("researched_at"),
                "job_id": r.get("job_id"),
                "contacts_saved": r.get("contacts_saved"),
                "criteria_matches": r.get("criteria_matches"),
                "bullet_count": sum(len(r.get(k) or []) for k in INTEL_LISTS),
            }
            for r in runs
        ],
    }


def has_deep_research(company: Dict[str, Any]) -> bool:
    return bool(runs_of(company.get("deep_intel")))
