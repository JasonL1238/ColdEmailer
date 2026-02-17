from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime


class Contact(BaseModel):
    id: Optional[str] = None
    name: str = ""  # Allow empty for initial creation
    company: str = ""  # Allow empty for initial creation
    email: str = ""  # Allow empty for initial creation, validate later
    status: Optional[str] = "pending"  # pending, trashed, sent


class CompanyMetadata(BaseModel):
    company_name: str
    url: Optional[str] = None
    summary: Optional[str] = None
    industry: Optional[str] = None
    product: Optional[str] = None
    why_engineers_care: Optional[str] = None
    hook_sentence: Optional[str] = None
    confidence_score: Optional[float] = None
    cached_at: Optional[datetime] = None


class GeneratedEmail(BaseModel):
    id: Optional[str] = None
    contact_id: str
    contact_name: str
    contact_email: EmailStr
    company: str
    subject: str
    body: str
    status: str = "pending"  # pending, accepted, trashed, sent
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None  # When email was actually sent
    gmail_message_id: Optional[str] = None  # Gmail API message ID for tracking
    has_response: bool = False  # Whether recipient replied
    response_date: Optional[datetime] = None  # When response was received
    original_email_id: Optional[str] = None  # For follow-ups, link to original email


class EmailGenerationRequest(BaseModel):
    contact_ids: Optional[List[str]] = None  # None means all pending contacts
    user_name: Optional[str] = None  # Your name for email signature
    user_background: Optional[str] = None  # Your background/qualifications
    user_email: Optional[str] = None  # Your email address


class EmailSendRequest(BaseModel):
    email_ids: List[str]


class UsageStats(BaseModel):
    emails_generated_today: int
    emails_sent_today: int
    company_researches_today: int
    daily_limit: int
    remaining_emails: int
