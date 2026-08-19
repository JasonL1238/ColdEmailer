"""Shared validation for contacts entering the DB via scrape, CSV, or manual add."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from contact_verify import (
    annotate_contact,
    is_generic_inbox,
    linkedin_matches_person,
)
from db import normalize_linkedin_url
from models import EMAIL_ADDRESS_RE, validate_linkedin_profile_url
import suppression


def sanitize_inbound_contact(
        *,
        name: str = "",
        email: str = "",
        linkedin_url: Optional[str] = None,
        role: Optional[str] = None,
        require_person_email: bool = True,
        check_mx: bool = True,
        suppressions: Optional[list] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    """Normalize and verify an inbound contact.

    Returns (fields_dict, error_reason). error_reason set → reject the row.
    Generic inboxes are rejected when require_person_email is True.
    LinkedIn URLs that do not match the supplied name are dropped (not fatal
    if an email remains).
    """
    name = (name or "").strip()
    email = (email or "").strip()
    role = (role or "").strip() or None
    if email and not EMAIL_ADDRESS_RE.fullmatch(email):
        return None, "invalid_email"
    # Refused at the door on the human-entered paths. The send gate is what
    # guarantees nothing reaches a suppressed address, but letting one into the
    # database means drafts get written for it and every one of them fails at
    # the last step, which reads as the app being broken rather than as the
    # list working.
    if email and suppressions and suppression.match(email, suppressions):
        return None, "suppressed"
    try:
        linkedin_url = validate_linkedin_profile_url(linkedin_url)
    except ValueError:
        return None, "invalid_linkedin_url"

    if email and is_generic_inbox(email) and require_person_email:
        # Company inbox is not a person contact on the strict path.
        if linkedin_url and name and linkedin_matches_person(linkedin_url, name):
            email = ""  # keep LinkedIn-only person
        else:
            return None, "company_or_role_inbox"

    if linkedin_url and not name:
        # Without a name we cannot confirm the /in/ slug matches a person.
        return None, "linkedin_needs_name"
    if linkedin_url and name and not linkedin_matches_person(linkedin_url, name):
        # Keep a usable email; surface the LinkedIn drop as a non-fatal warning.
        linkedin_url = None
        li_warning = "linkedin_does_not_match_name"
    else:
        li_warning = None

    if not email and not linkedin_url:
        return None, "no_usable_contact_method"

    annotated = annotate_contact({
        "name": name,
        "email": email,
        "linkedin_url": linkedin_url,
        "role": role,
        "name_from_email": False,
    }, check_mx=check_mx and bool(email))

    if email and require_person_email and name:
        # Named imports still need the local to match when we have both.
        if not annotated.get("email_person_match") and not annotated.get(
                "email_verified"):
            # Allow LinkedIn-only if email fails person match
            if linkedin_url and annotated.get("linkedin_verified"):
                email = ""
                annotated["email"] = ""
                annotated["email_kind"] = "none"
                annotated["email_verified"] = False
                annotated["email_person_match"] = False
            else:
                return None, "email_does_not_match_name"
    elif email and require_person_email and not name:
        # Without a person name we cannot associate the mailbox. Reject —
        # role inboxes like legal@ / privacy@ otherwise slip through as
        # "named_unmatched".
        return None, "name_required_for_email"

    if not email and not (linkedin_url and annotated.get("linkedin_verified")):
        if not email and linkedin_url and not name:
            return None, "linkedin_needs_name"
        if not email and linkedin_url and not annotated.get("linkedin_verified"):
            return None, "linkedin_does_not_match_name"

    return {
        "name": name,
        "email": email or "",
        "linkedin_url": linkedin_url,
        "role": role,
        "email_kind": annotated.get("email_kind") or "unknown",
        "email_verified": bool(annotated.get("email_verified")),
        "linkedin_verified": bool(annotated.get("linkedin_verified")),
        "person_verified": bool(annotated.get("person_verified")),
        "ingest_warning": li_warning,
    }, None


# --------------------------------------------------------------------------
# Attaching a scraped candidate to a company.
#
# Discovery, deep research, and single-company re-research all walk the same
# path: decide which of a candidate's channels survived verification, look for
# the person under some other company, insert, then confirm what actually
# landed on the row. Each carried its own copy of that path and they had
# already drifted — deep research never re-checked that a LinkedIn URL reached
# the contact it was inserted for, so a same-company insert race silently
# dropped it. One implementation, three callers.
# --------------------------------------------------------------------------

ROLE_BY_LOCAL = {
    "careers": "Careers inbox", "jobs": "Careers inbox",
    "recruiting": "Recruiting", "talent": "Recruiting",
    "founders": "Founders", "founder": "Founders", "ceo": "CEO",
    "hello": "General inbox", "hi": "General inbox", "hey": "General inbox",
    "contact": "General inbox", "info": "General inbox", "team": "Team inbox",
    "press": "Press", "sales": "Sales", "support": "Support",
}


def guess_name_from_email(local: str) -> str:
    """'jane.doe' -> 'Jane Doe'; generic or unparseable locals -> ''.

    Role inboxes are decided by `contact_verify.is_generic_inbox`, which is the
    one vocabulary. A shorter local copy used to shadow it, so compound role
    addresses slipped through and were named as if they were people —
    `sales.support@` became a contact called "Sales Support".
    """
    if is_generic_inbox(local):
        return ""
    words = [p for p in re.split(r"[._-]", local) if p.isalpha() and len(p) > 1]
    if len(words) >= 2:
        return " ".join(w.capitalize() for w in words[:3])
    return ""


def role_for_email(local: str) -> Optional[str]:
    return ROLE_BY_LOCAL.get(local.lower())


def contact_notes(candidate: Dict) -> Optional[str]:
    """Human-readable provenance for affinity matches and risky addresses."""
    notes = []
    if candidate.get("school_match"):
        school = candidate.get("school") or "your school"
        notes.append(
            f"Same-school match: {school} is mentioned in this person's "
            "public company biography. Verify before referencing it.")
    for affinity in candidate.get("affinity") or []:
        if affinity and affinity not in (candidate.get("school") or ""):
            notes.append(f"Warm match: {affinity}. Verify before referencing it.")
    if candidate.get("email_verified"):
        notes.append("Email verified as person-associated (name match + MX confirmed).")
    elif candidate.get("email_person_match") and candidate.get("email_mx_ok") is None:
        notes.append("Email local matches name; MX not confirmed yet.")
    elif candidate.get("email_kind") == "generic":
        notes.append("Company/role inbox — not a specific person.")
    elif candidate.get("email") and not candidate.get("email_person_match"):
        notes.append("Email local-part does not clearly match this person's name.")
    if candidate.get("linkedin_verified"):
        source = candidate.get("linkedin_source")
        if source == "user_provided":
            notes.append(
                "LinkedIn profile supplied directly by you as the identity anchor.")
        elif source == "web_search":
            notes.append("LinkedIn found via web search; slug matches this person's name.")
        else:
            notes.append("LinkedIn URL slug matches this person's name.")
    elif candidate.get("linkedin_url") and not candidate.get("linkedin_person_match"):
        notes.append("LinkedIn URL did not match this person's name — not stored as verified.")
    if candidate.get("email_source") == "hunter":
        notes.append("Email from Hunter.io finder — confirm before sending.")
    if candidate.get("email_guess"):
        if candidate.get("email_guess_smtp_status") == "deliverable":
            notes.append(
                f"Possible email pattern {candidate['email_guess']} accepts "
                "mail, but ownership is not proven — confirm before using.")
        else:
            notes.append(
                f"Possible email pattern {candidate['email_guess']} "
                f"(not verified — confirm before using).")
    if candidate.get("source_url"):
        notes.append(f"Source: {candidate['source_url']}")
    if candidate.get("evidence"):
        notes.append(f'Evidence: "{candidate["evidence"][:240]}"')
    if candidate.get("warning"):
        notes.append(candidate["warning"])
    return " ".join(notes) or None


def verified_channels(candidate: Dict) -> Tuple[str, str]:
    """The candidate's (email, linkedin_url) with unverified channels stripped.

    A LinkedIn URL whose slug did not match the person's name is not that
    person's profile, and a role inbox is not a person at all. Either may be
    dropped while the other still carries the contact — a caller with neither
    left has nothing to attach.
    """
    email = candidate.get("email") or ""
    linkedin_url = candidate.get("linkedin_url") or ""
    if linkedin_url and not candidate.get("linkedin_verified"):
        linkedin_url = ""
    if email and (
            candidate.get("email_kind") == "generic"
            or (candidate.get("name")
                and not candidate.get("email_person_match")
                and not candidate.get("email_verified"))):
        email = ""
    return email, linkedin_url


def find_existing(db, email: str, linkedin_url: str) -> Optional[Dict]:
    """The contact already holding either identifier, by email first."""
    return (
        db.find_contact_by_email(email) if email else None
    ) or (
        db.find_contact_by_linkedin(linkedin_url) if linkedin_url else None
    )


def owned_elsewhere_note(db, other_company_id: Optional[str], label: str,
                         company_name: str) -> str:
    """Why an identifier was not moved onto `company_name`."""
    other = db.get_company(other_company_id) if other_company_id else None
    other_name = (other or {}).get("name") or "another company"
    return (f"{label} already belongs to {other_name} — "
            f"not reassigned to {company_name}")


@dataclass
class Attached:
    """What `attach_candidate` managed to store, and what it could not."""

    contact: Optional[Dict] = None
    conflicts: List[str] = field(default_factory=list)
    # Whether each identifier is actually on the row that came back. An
    # IntegrityError fallback or a concurrent insert can return a row that
    # never received one of them.
    email_landed: bool = False
    linkedin_landed: bool = False

    @property
    def is_new_outreach(self) -> bool:
        """A freshly inserted row reachable through at least one channel."""
        return bool(
            self.contact
            and (self.email_landed or self.linkedin_landed)
            and self.contact.get("_inserted", True)
        )


def attach_candidate(db, *, company: Dict, candidate: Dict, email: str,
                     linkedin_url: str, source: str,
                     notes: Optional[str] = None,
                     campaign_id: Optional[str] = None,
                     linkedin_verified: Optional[bool] = None,
                     log_events: bool = True) -> Attached:
    """Insert `candidate` under `company` and report what actually landed.

    Callers own the decision about existing contacts — discovery skips them,
    re-research enriches them — but everything from the insert onward is the
    same everywhere, including the two races that make the returned row differ
    from what was asked for.
    """
    company_id = company["id"]
    company_name = company.get("name") or "this company"
    local = email.split("@", 1)[0] if email else ""
    result = Attached()

    def conflict(detail: str) -> None:
        result.conflicts.append(detail)
        if log_events:
            db.log_event("company", company_id, "contact_conflict", detail)

    created = db.create_contact(
        company_id=company_id,
        # Stated, not inherited. `create_company` returns the existing row when
        # a candidate turns out to be a company some earlier run already found,
        # correctly leaving that company on its original campaign. Falling back
        # to the company's campaign therefore credited people *this* run
        # discovered to a campaign that never went looking for them.
        # Inheritance is the floor for callers that cannot know.
        campaign_id=campaign_id,
        name=candidate.get("name") or guess_name_from_email(local),
        email=email,
        linkedin_url=linkedin_url or None,
        role=candidate.get("role") or role_for_email(local),
        source=source,
        status="new",
        notes=notes if notes is not None else contact_notes(candidate),
        source_url=candidate.get("source_url"),
        evidence=candidate.get("evidence"),
        affinity=", ".join(candidate.get("affinity") or []) or None,
        seniority_rank=candidate.get("seniority_rank", 20),
        email_kind=candidate.get("email_kind") or "unknown",
        email_verified=bool(candidate.get("email_verified")),
        linkedin_verified=bool(candidate.get("linkedin_verified")
                               if linkedin_verified is None
                               else linkedin_verified),
        person_verified=bool(candidate.get("person_verified")),
    )

    # The IntegrityError fallback in create_contact can hand back another
    # company's row rather than raising.
    if created.get("company_id") and created["company_id"] != company_id:
        conflict(owned_elsewhere_note(
            db, created["company_id"], email or linkedin_url, company_name))
        return result

    result.contact = created

    # Same-company race: confirm the LinkedIn URL reached this row.
    if linkedin_url:
        wanted = normalize_linkedin_url(linkedin_url)
        stored = normalize_linkedin_url(created.get("linkedin_url"))
        if wanted and wanted != stored:
            clash = db.find_contact_by_linkedin(linkedin_url)
            taken = bool(clash and clash.get("id") != created.get("id"))
            other = (db.get_company(clash.get("company_id"))
                     if taken and clash.get("company_id") else None)
            other_name = (other or {}).get("name") or "another contact"
            conflict(
                f"{linkedin_url} could not be attached to {company_name} — "
                f"already held by {other_name}" if taken else
                f"{linkedin_url} could not be attached to {company_name} "
                f"(insert race)")

    created_email = (created.get("email") or "").strip().lower()
    created_li = (created.get("linkedin_url") or "").strip()
    result.email_landed = bool(email and created_email == email.lower())
    result.linkedin_landed = bool(
        linkedin_url and created_li
        and normalize_linkedin_url(created_li)
        == normalize_linkedin_url(linkedin_url)
    )
    return result
