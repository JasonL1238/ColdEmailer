"""Pydantic request models for the API. Responses are plain dicts from the DB layer."""
import re
from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

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


class DiscoveryRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=300)
    count: int = Field(10, ge=1, le=100)
    mode: str = Field("full", pattern="^(fast|full)$")


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


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v):
        return validate_http_url(v)


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    summary: Optional[str] = None
    industry: Optional[str] = None
    product: Optional[str] = None
    hook: Optional[str] = None
    location: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, v):
        return validate_http_url(v)


class ContactCreate(BaseModel):
    name: str = ""
    email: str = ""
    linkedin_url: Optional[str] = None
    role: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None  # create/link company by name
    notes: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, v):
        return validate_email_address(v)

    @field_validator("linkedin_url")
    @classmethod
    def _check_linkedin(cls, v):
        return validate_linkedin_profile_url(v)


class ContactUpdate(BaseModel):
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

    @field_validator("email")
    @classmethod
    def _check_email(cls, v):
        return validate_email_address(v)

    @field_validator("linkedin_url")
    @classmethod
    def _check_linkedin(cls, v):
        return validate_linkedin_profile_url(v)


def validate_linkedin_profile_url(value: Optional[str]) -> Optional[str]:
    """Accept only public LinkedIn member profile URLs, never arbitrary links."""
    if value is None:
        return value
    value = value.strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        raise ValueError("LinkedIn URL is invalid")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {"linkedin.com", "www.linkedin.com"}:
        raise ValueError("Use a full https://www.linkedin.com/in/... profile URL")
    if not parsed.path.lower().startswith("/in/"):
        raise ValueError("LinkedIn URL must point to a member profile")
    return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


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

    @field_validator("from_email")
    @classmethod
    def _check_from(cls, v):
        return validate_email_address(v)


class ResumeUpdate(BaseModel):
    label: Optional[str] = None
    is_default: Optional[bool] = None
