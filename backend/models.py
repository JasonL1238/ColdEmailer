"""Pydantic request models for the API. Responses are plain dicts from the DB layer."""
import re
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from contact_verify import canonical_linkedin_profile

# Strict single-address pattern: no commas/semicolons/angle-brackets/whitespace,
# so a stored address can never smuggle extra recipients into a To: header.
EMAIL_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+")


def validate_email_address(value: Optional[str]) -> Optional[str]:
    """Empty/None pass through; anything else must be one plain address."""
    if value is None:
        return value
    value = value.strip()
    if value and not EMAIL_ADDRESS_RE.fullmatch(value):
        raise ValueError(f"Invalid email address: {value}")
    return value


def validate_http_url(value: Optional[str]) -> Optional[str]:
    """Company URLs must be plain http(s) on a standard port (SSRF guard;
    the scraper additionally rejects hosts resolving to private IPs)."""
    if value is None:
        return value
    value = value.strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        raise ValueError("URL has an invalid port")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must be a full http:// or https:// address")
    if port not in (None, 80, 443):
        raise ValueError("URL must use a standard http/https port")
    return value


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    school: Optional[str] = None
    affiliations: Optional[str] = None
    website: Optional[str] = None
    background: Optional[str] = None
    signature: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, v):
        # This becomes the From address on every send.
        return validate_email_address(v)


class CadenceUpdate(BaseModel):
    """The follow-up schedule: days of silence before each successive nudge.

    Bounded here as well as in `normalize_follow_up_cadence` — this rejects a
    malformed request outright, the normalizer keeps a settings row that was
    written before these bounds existed (or edited by hand) usable. The caps
    matter because this value decides when real email goes to real people.
    """
    enabled: bool = True
    steps: List[int] = Field(default_factory=list, max_length=4)

    @field_validator("steps")
    @classmethod
    def _check_steps(cls, v):
        for gap in v:
            if not 1 <= gap <= 90:
                raise ValueError("Each follow-up gap must be 1 to 90 days")
        return v


class SendWindowUpdate(BaseModel):
    """Business hours for sending, in the sender's own timezone.

    `enabled` is the first of the two gates that stand between this app and
    unattended mail: with it off, the scheduler thread does nothing and the
    send endpoint refuses to schedule at all.
    """
    enabled: bool = False
    timezone: str = ""
    days: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)
    start_hour: int = Field(8, ge=0, le=23)
    end_hour: int = Field(17, ge=1, le=23)

    @field_validator("days")
    @classmethod
    def _check_days(cls, v):
        for day in v:
            if not 0 <= day <= 6:
                raise ValueError("Days are 0 (Monday) to 6 (Sunday)")
        return v

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v):
        from send_window import resolve_timezone
        value = (v or "").strip()
        if value and resolve_timezone(value) is None:
            raise ValueError(f"Unknown timezone: {value}")
        return value


class DiscoveryRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=300)
    count: int = Field(10, ge=1, le=100)
    mode: str = Field("full", pattern="^(fast|full)$")
    # Add this run to an existing campaign instead of starting a new one. An
    # unknown id is ignored rather than rejected: the run is the valuable part,
    # and failing it over a stale campaign reference would be the wrong trade.
    campaign_id: Optional[str] = Field(None, max_length=64)


class SuppressionCreate(BaseModel):
    value: str = Field(..., min_length=3, max_length=320)
    reason: Optional[str] = Field(None, max_length=500)


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    notes: Optional[str] = Field(None, max_length=2000)
    archived: Optional[bool] = None


class EnrichRequest(BaseModel):
    mode: str = Field("full", pattern="^(fast|full|deep)$")


class DeepResearchRequest(BaseModel):
    """Interview-grade research for one company + criteria-matched contacts."""
    company_name: Optional[str] = Field(None, min_length=1, max_length=120)
    company_id: Optional[str] = None
    url: Optional[str] = None
    contact_criteria: str = Field(
        "",
        max_length=500,
        description="Roles, schools, or traits to prioritize (e.g. 'VP Engineering, Penn alumni')",
    )
    min_contacts: int = Field(5, ge=1, le=50)
    # Keep hunting until this many criteria-matched people are found (or the
    # 30-minute hard stop). None = single-pass floor behavior.
    target_criteria_matches: Optional[int] = Field(
        None, ge=1, le=50,
        description="Stop only after finding this many criteria matches "
                    "(hard-capped at 30 minutes).",
    )
    # Continue an existing dive: contacts | research | both. Omit for a full run.
    continue_mode: Optional[str] = Field(
        None, pattern="^(contacts|research|both)$")

    @field_validator("url")
    @classmethod
    def _check_url(cls, v):
        return validate_http_url(v)

    @field_validator("company_name")
    @classmethod
    def _strip_name(cls, v):
        if v is None:
            return v
        return v.strip() or None


class PersonFindRequest(BaseModel):
    """Everything the user knows about one specific person."""
    name: str = Field(..., min_length=2, max_length=120)
    company_name: Optional[str] = Field(None, max_length=120)
    company_url: Optional[str] = None
    school: Optional[str] = Field(None, max_length=200)
    school_dates: Optional[str] = Field(
        None, max_length=60,
        description="Attendance years, e.g. '2018-2022' or 'class of 2020'")
    past_companies: Optional[str] = Field(None, max_length=500)
    role_hint: Optional[str] = Field(None, max_length=120)
    location: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = Field(None, max_length=1000)

    @field_validator("company_url")
    @classmethod
    def _check_url(cls, v):
        return validate_http_url(v)

    @field_validator("company_name")
    @classmethod
    def _strip_name(cls, v):
        if v is None:
            return v
        return v.strip() or None


class PersonApproveRequest(BaseModel):
    """Save one reviewed person-finder candidate as a contact."""
    candidate_id: str = Field(..., min_length=1, max_length=32)
    email: Optional[str] = None
    include_linkedin: bool = True
    # Required to save a found address whose local part does not contain the
    # person's name (e.g. a handle-style gmail from their GitHub profile).
    confirm_email_ownership: bool = False
    company_id: Optional[str] = None
    company_name: Optional[str] = Field(None, max_length=120)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v):
        return validate_email_address(v)


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v):
        return validate_http_url(v)


class _ContactAddressValidators(BaseModel):
    """The two address checks every contact payload gets, create or update.

    Declared once: a validator that exists on the create model and not the
    update model is a hole, and that is exactly the shape the duplication kept
    inviting.
    """

    # check_fields=False: the fields live on the subclasses, not here.
    @field_validator("email", check_fields=False)
    @classmethod
    def _check_email(cls, v):
        return validate_email_address(v)

    @field_validator("linkedin_url", check_fields=False)
    @classmethod
    def _check_linkedin(cls, v):
        return validate_linkedin_profile_url(v)


class ContactCreate(_ContactAddressValidators):
    name: str = ""
    email: str = ""
    linkedin_url: Optional[str] = None
    role: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None  # create/link company by name
    notes: Optional[str] = None


class ContactUpdate(_ContactAddressValidators):
    name: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    role: Optional[str] = None
    company_id: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    # The only way back from a bounce. Detection can be wrong — a mail system
    # that rejected one message may accept the next — and without this the flag
    # was write-only: one false positive retired a contact permanently, with
    # no route, repair or UI able to undo it.
    clear_bounce: Optional[bool] = None


def validate_linkedin_profile_url(value: Optional[str]) -> Optional[str]:
    """Accept only public LinkedIn member profile URLs, never arbitrary links."""
    if value is None:
        return value
    value = value.strip()
    if not value:
        return None
    return canonical_linkedin_profile(value)


class LinkedInDraftRequest(BaseModel):
    custom_instructions: Optional[str] = Field(None, max_length=1000)


class BulkIds(BaseModel):
    ids: List[str]


class GenerateRequest(BaseModel):
    contact_ids: List[str] = Field(..., min_length=1)
    email_type: str = "application"
    resume_id: Optional[str] = None
    custom_instructions: Optional[str] = Field(None, max_length=2000)
    use_template_only: bool = False
    # Opt in to drafting another first-contact email for someone already emailed
    allow_recontact: bool = False


class EmailUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    status: Optional[str] = None  # draft | approved | trashed


class BulkStatus(BaseModel):
    email_ids: List[str]
    status: str


class SendRequest(BaseModel):
    email_ids: List[str] = Field(..., min_length=1)
    attach_resume: bool = True
    resume_id: Optional[str] = None  # override; default = email's own resume or default resume
    from_email: Optional[str] = None
    # Opt-in to retrying a row whose earlier attempt reached Gmail without a
    # confirmation. Off by default: the recipient may already have that message.
    confirm_resend: bool = False
    # Opt-in to sending a draft whose body promises an attachment these options
    # would remove or swap. Off by default: the email would contradict itself.
    confirm_attachment_change: bool = False
    # The per-batch half of the scheduling opt-in. None (the default) means
    # "hand these to Gmail now", which is the only thing this endpoint has ever
    # done. "next_window" hands them to a background thread instead, to go out
    # when business hours next open — the one path where mail leaves without
    # anyone watching, so it is never the default and never implied.
    schedule: Optional[str] = Field(None, pattern="^(now|next_window)$")

    @field_validator("from_email")
    @classmethod
    def _check_from(cls, v):
        return validate_email_address(v)


class ResumeUpdate(BaseModel):
    label: Optional[str] = None
    is_default: Optional[bool] = None
