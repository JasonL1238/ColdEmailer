"""Shared validation for contacts entering the DB via scrape, CSV, or manual add."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from contact_verify import (
    annotate_contact,
    is_generic_inbox,
    linkedin_matches_person,
)
import suppression
from models import EMAIL_ADDRESS_RE, validate_linkedin_profile_url


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
