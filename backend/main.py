from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional
from datetime import datetime
import os
import random
import time
import asyncio
from dotenv import load_dotenv

# #region agent log
def _dlog(location: str, message: str, data: dict, hypothesis_id: str = "H1"):
    try:
        with open("/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug-6c4284.log", "a") as f:
            f.write(__import__("json").dumps({"sessionId": "6c4284", "timestamp": int(time.time() * 1000), "location": location, "message": message, "data": data, "runId": "startup", "hypothesisId": hypothesis_id}) + "\n")
    except Exception:
        pass
# #endregion

from models import (
    Contact, CompanyMetadata, GeneratedEmail, 
    EmailGenerationRequest, EmailSendRequest, EmailUpdateRequest, EmailBulkDeleteRequest, UsageStats
)
from csv_processor import CSVProcessor
from company_enrichment_service import CompanyEnrichmentService
from email_generator import EmailGenerator
from email_sender import EmailSender
from rate_limiter import RateLimiter
from email_storage import EmailStorage
from response_checker import ResponseChecker

load_dotenv()
# Load project-root .env when running from backend/ (e.g. start.sh does cd backend)
_project_root_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.isfile(_project_root_env):
    load_dotenv(_project_root_env)

# #region agent log
def _mask_key(k: str) -> str:
    return (k[-4:] if k and len(k) >= 4 else "n/a")
_dlog("main.py:after_imports", "All imports and load_dotenv done", {
    "cwd": os.getcwd(),
    "project_root_env": _project_root_env,
    "project_root_env_exists": os.path.isfile(_project_root_env),
    "GOOGLE_AI_API_KEY_last4": _mask_key(os.getenv("GOOGLE_AI_API_KEY", "")),
    "EMAIL_LLM_PROVIDER": os.getenv("EMAIL_LLM_PROVIDER"),
}, "H1")
# #endregion

# #region agent log
import json
try:
    with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
        f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"main.py:startup","message":"Backend starting","data":{"port":8000},"runId":"run1","hypothesisId":"B"}) + '\n')
except: pass
# #endregion

app = FastAPI(title="AI Cold Emailer API")

# #region agent log
_dlog("main.py:after_app", "FastAPI app created", {}, "H1")
# #endregion
# #region agent log
import json
try:
    with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
        f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"main.py:app_created","message":"FastAPI app created","data":{},"runId":"run1","hypothesisId":"A"}) + '\n')
except: pass
# #endregion

# Stress test mode - simulate slow/failing APIs
STRESS_TEST_MODE = os.getenv('STRESS_TEST_MODE', 'false').lower() == 'true'

@app.middleware("http")
async def stress_test_middleware(request, call_next):
    """Middleware to simulate slow/failing APIs in stress test mode"""
    if STRESS_TEST_MODE:
        # Random delay (0-5 seconds)
        delay = random.uniform(0, 5)
        await asyncio.sleep(delay)
        
        # Randomly fail 10% of requests
        if random.random() < 0.1:
            return JSONResponse(
                status_code=500,
                content={"detail": "Simulated API failure for stress testing"}
            )
    
    response = await call_next(request)
    return response

# CORS middleware (origins from env, comma-separated)
_cors_origins = os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://localhost:3000').strip().split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
# #region agent log
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_resume_env = os.getenv('RESUME_PATH', 'resume.pdf')
_resume_candidate = _resume_env if os.path.isabs(_resume_env) else os.path.join(_project_root, _resume_env)
if not os.path.exists(_resume_candidate):
    for fallback in ("resume28.pdf", "resume29.pdf"):
        _alt = os.path.join(_project_root, fallback)
        if os.path.exists(_alt):
            _resume_candidate = _alt
            break
_resume_for_generator = _resume_candidate
_dlog("main.py:resume_check", "Resume path resolution", {"cwd": os.getcwd(), "project_root": _project_root, "RESUME_PATH_env": _resume_env, "resume_path_used": _resume_for_generator, "exists": os.path.exists(_resume_for_generator), "email_generator_gets_default": False}, "rh1")
# #endregion
project_root = _project_root
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_csv_env = os.getenv('CSV_FILE_PATH')
if _csv_env and os.path.isabs(_csv_env):
    _csv_path = _csv_env
elif _csv_env:
    _csv_path = os.path.normpath(os.path.join(_project_root, _csv_env))
else:
    _csv_path = os.path.join(_backend_dir, 'data', 'contacts.csv')
csv_processor = CSVProcessor(
    csv_path=_csv_path
)
# #region agent log
_dlog("main.py:after_csv_processor", "CSVProcessor initialized", {}, "H2")
# #endregion
enrichment_service = CompanyEnrichmentService(
    cache_path=os.getenv('COMPANY_CACHE_PATH', 'data/company_cache.json')
)
# #region agent log
_dlog("main.py:after_enrichment_service", "CompanyEnrichmentService initialized", {}, "H2")
# #endregion
email_generator = EmailGenerator(
    model=os.getenv('OLLAMA_MODEL', 'llama3.2'),
    base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434'),
    resume_path=_resume_for_generator
)
# Cloud LLM (OpenRouter/OpenAI/Gemini) is configured via env: EMAIL_LLM_PROVIDER, EMAIL_LLM_MODEL, and the corresponding API key. Keys are never logged or sent to the frontend.
# #region agent log
_dlog("main.py:after_email_generator", "EmailGenerator initialized", {"resume_path": _resume_for_generator}, "H2")
# #endregion
# Get project root (parent of backend directory) - use project_root set above
credentials_path = os.getenv('CREDENTIALS_JSON_PATH') or os.path.join(project_root, 'credentials.json')
token_path = os.getenv('TOKEN_JSON_PATH') or os.path.join(project_root, 'token.json')
# #region agent log
import json
import time
log_data = {
    "location": "main.py:email_sender_init",
    "message": "Constructing email sender paths",
    "data": {
        "project_root": project_root,
        "credentials_path": credentials_path,
        "token_path": token_path,
        "cwd": os.getcwd(),
        "credentials_path_abs": os.path.abspath(credentials_path),
        "token_path_abs": os.path.abspath(token_path),
        "credentials_exists": os.path.exists(credentials_path),
        "token_exists": os.path.exists(token_path),
        "__file__": __file__,
    },
    "timestamp": int(time.time() * 1000),
    "runId": "init",
    "hypothesisId": "A"
}
try:
    with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
        f.write(json.dumps(log_data) + '\n')
except: pass
# #endregion
email_sender = EmailSender(credentials_path=credentials_path, token_path=token_path, project_root=project_root)
# #region agent log
_dlog("main.py:after_email_sender", "EmailSender initialized", {}, "H2")
# #endregion
rate_limiter = RateLimiter()

# Persistent storage for generated emails
email_storage = EmailStorage(
    storage_path=os.getenv('EMAIL_STORAGE_PATH', 'data/generated_emails.json')
)

# #region agent log
_dlog("main.py:startup_complete", "All services initialized, module load done", {}, "H3")
# #endregion


# Contact endpoints
@app.get("/api/contacts", response_model=List[Contact])
async def get_contacts(status: Optional[str] = None):
    """Get all contacts, optionally filtered by status"""
    contacts = csv_processor.read_contacts()
    if status:
        contacts = [c for c in contacts if c.status == status]
    return contacts


@app.post("/api/contacts", response_model=Contact)
async def create_contact(contact: Contact):
    """Create a new contact"""
    _dlog("main.py:create_contact", "API create contact", {"contact_id": contact.id, "name": getattr(contact, "name", "")}, "H3")
    return csv_processor.add_contact(contact)


@app.put("/api/contacts/{contact_id}", response_model=Contact)
async def update_contact(contact_id: str, updates: dict):
    """Update an existing contact"""
    contact = csv_processor.update_contact(contact_id, updates)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@app.delete("/api/contacts/{contact_id}")
async def delete_contact(contact_id: str):
    """Delete a contact"""
    _dlog("main.py:delete_contact", "API delete contact", {"contact_id": contact_id}, "H2")
    # #region agent log
    import json
    try:
        with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
            f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"main.py:delete_contact:start","message":"Delete contact endpoint called","data":{"contact_id":contact_id},"runId":"run1","hypothesisId":"B"}) + '\n')
    except: pass
    # #endregion
    success = csv_processor.delete_contact(contact_id)
    # #region agent log
    import json
    try:
        with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
            f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"main.py:delete_contact:result","message":"Delete contact result","data":{"contact_id":contact_id,"success":success},"runId":"run1","hypothesisId":"B"}) + '\n')
    except: pass
    # #endregion
    if not success:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"success": True}


@app.post("/api/contacts/bulk-delete")
async def bulk_delete_contacts(contact_ids: List[str] = Body(..., embed=False)):
    """Bulk delete contacts. Body: JSON array of contact IDs."""
    _dlog("main.py:bulk_delete_contacts", "API bulk delete", {"contact_ids": contact_ids}, "H2")
    deleted = 0
    for contact_id in contact_ids:
        if csv_processor.delete_contact(contact_id):
            deleted += 1
    return {"success": True, "deleted": deleted}


@app.put("/api/contacts/bulk")
async def bulk_update_contacts(updates: List[dict]):
    """Bulk update contacts"""
    updated = []
    for update in updates:
        contact_id = update.pop('id')
        contact = csv_processor.update_contact(contact_id, update)
        if contact:
            updated.append(contact)
    return {"success": True, "updated": len(updated)}


@app.post("/api/contacts/save")
async def save_contacts():
    """Explicitly save contacts (usually auto-saved, but this ensures persistence)"""
    # CSV processor auto-saves, but this endpoint confirms it
    return {"success": True, "message": "Contacts saved"}


def _safe_str(val, default: str = "") -> str:
    """Coerce value to string and strip; use default for NaN/None."""
    if val is None:
        return default
    try:
        s = str(val).strip()
        if s.lower() == "nan":
            return default
        return s or default
    except Exception:
        return default


@app.post("/api/upload")
async def upload_csv(file: UploadFile = File(...)):
    """Upload CSV to add contacts; merges with existing. Duplicates (by email) are skipped. Required columns: name, company, email."""
    import pandas as pd
    import io
    import uuid

    try:
        contents = await file.read()
        text = contents.decode('utf-8-sig').strip()  # utf-8-sig strips BOM from Excel/Sheets
        df = pd.read_csv(io.StringIO(text))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid CSV: {str(e)}")

    if df.empty or len(df.columns) == 0:
        raise HTTPException(status_code=400, detail="CSV has no columns. Add a header row with: name, company, email")

    # Normalize column names: strip whitespace, lowercase; dedupe by keeping first
    raw_cols = [str(c).strip().lower() for c in df.columns]
    seen = set()
    unique_cols = []
    for c in raw_cols:
        if c not in seen:
            seen.add(c)
            unique_cols.append(c)
    df.columns = unique_cols

    required = {'name', 'company', 'email'}
    missing = required - set(df.columns)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"CSV is missing required column(s): {', '.join(sorted(missing))}. Required: name, company, email (header row must match)."
        )

    try:
        existing = csv_processor.read_contacts()
        seen_emails = {(c.email or "").strip().lower() for c in existing if (c.email or "").strip()}
        merged = list(existing)

        for _, row in df.iterrows():
            email_val = _safe_str(row.get("email"))
            email_norm = email_val.strip().lower() if email_val else ""
            if not email_norm:
                continue  # skip rows with no email to avoid duplicate blank entries
            if email_norm in seen_emails:
                continue  # skip duplicate (includes contacts already in emailed)
            contact = Contact(
                id=str(uuid.uuid4()),
                name=_safe_str(row.get("name")),
                company=_safe_str(row.get("company")),
                email=email_val,
                status=_safe_str(row.get("status"), "pending") or "pending",
            )
            merged.append(contact)
            seen_emails.add(email_norm)

        csv_processor.write_contacts(merged)
        added = len(merged) - len(existing)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    return {"success": True, "count": len(merged), "added": added}


@app.get("/api/contacts/export")
async def export_csv(section: Optional[str] = None):
    """Download CSV file for a specific section or all contacts
    
    section: 'emailed', 'emails_generated', 'no_emails', or None for all
    """
    from fastapi.responses import Response
    import pandas as pd
    import io
    
    if section:
        # Get categorized contacts
        all_contacts = csv_processor.read_contacts()
        all_emails = email_storage.get_all()
        
        email_by_contact = {}
        for email in all_emails:
            contact_id = email.contact_id
            if contact_id not in email_by_contact:
                email_by_contact[contact_id] = email
            else:
                existing = email_by_contact[contact_id]
                if email.status == "sent" and existing.status != "sent":
                    email_by_contact[contact_id] = email
                elif (email.created_at and existing.created_at and 
                      email.created_at > existing.created_at):
                    email_by_contact[contact_id] = email
        
        if section == "emailed":
            contacts = [c for c in all_contacts 
                       if c.id in email_by_contact and email_by_contact[c.id].status == "sent"]
            filename = "contacts_emailed.csv"
        elif section == "emails_generated":
            contacts = [c for c in all_contacts 
                       if c.id in email_by_contact and email_by_contact[c.id].status != "sent"]
            filename = "contacts_emails_generated.csv"
        elif section == "no_emails":
            contacts = [c for c in all_contacts if c.id not in email_by_contact]
            filename = "contacts_no_emails.csv"
        else:
            contacts = all_contacts
            filename = "contacts.csv"
    else:
        contacts = csv_processor.read_contacts()
        filename = "contacts.csv"
    
    df = pd.DataFrame([c.model_dump() for c in contacts])
    
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# Company enrichment endpoints
@app.post("/api/enrich-company", response_model=CompanyMetadata)
async def enrich_company(
    company_name: str = Query(..., alias="company_name"),
    url: Optional[str] = Query(None)
):
    """Enrich a single company"""
    can_research, error = rate_limiter.can_research_company()
    if not can_research:
        raise HTTPException(status_code=429, detail=error)
    
    rate_limiter.record_company_research()
    metadata = enrichment_service.enrich_company(company_name, url)
    return metadata


@app.get("/api/company-metadata/{company_name}", response_model=CompanyMetadata)
async def get_company_metadata(company_name: str):
    """Get cached company metadata"""
    metadata = enrichment_service.cache.get(company_name)
    if not metadata:
        raise HTTPException(status_code=404, detail="Company not found in cache")
    return metadata


# Email generation endpoints
@app.post("/api/generate-emails", response_model=List[GeneratedEmail])
async def generate_emails(request: EmailGenerationRequest):
    """Generate emails for contacts
    
    If contact_ids provided: generates for those specific contacts
    If contact_ids is None: generates for contacts with no emails (from 'no emails' section)
    """
    # Get contacts to process
    if request.contact_ids:
        contacts = [csv_processor.get_contact(cid) for cid in request.contact_ids]
        contacts = [c for c in contacts if c]
    else:
        # Generate for contacts with no emails (from "no emails" section)
        all_contacts = csv_processor.read_contacts()
        all_emails = email_storage.get_all()
        emails_by_contact = {e.contact_id: e for e in all_emails}
        
        # Get contacts that don't have any emails
        contacts = [c for c in all_contacts if c.id not in emails_by_contact]
    
    if not contacts:
        return []
    
    generated = []
    
    for contact in contacts:
        # Check rate limit
        can_generate, error = rate_limiter.can_generate_email()
        if not can_generate:
            raise HTTPException(status_code=429, detail=error)
        
        rate_limiter.record_email_generation()
        
        # Enrich company
        metadata = enrichment_service.enrich_company(contact.company)
        
        # Generate email
        email = email_generator.generate(
            contact,
            metadata,
            user_name=request.user_name,
            user_background=request.user_background,
            user_email=request.user_email,
            use_template_only=request.use_template_only or False,
        )
        email_storage.save(email)
        generated.append(email)
    
    return generated


# Email review endpoints
@app.get("/api/emails", response_model=List[GeneratedEmail])
async def get_emails(status: Optional[str] = None):
    """Get all generated emails, optionally filtered by status"""
    emails = email_storage.get_all()
    if status:
        emails = [e for e in emails if e.status == status]
    return emails


@app.put("/api/emails/{email_id}")
async def update_email_status(email_id: str, status: str = Query(...)):
    """Update email status (accept/trash)"""
    email = email_storage.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    email.status = status
    email_storage.save(email)
    
    # If trashed, update contact status
    if status == "trashed":
        contact = csv_processor.get_contact(email.contact_id)
        if contact:
            csv_processor.update_contact(email.contact_id, {"status": "trashed"})
    
    return {"success": True}


@app.patch("/api/emails/{email_id}")
async def update_email_content(email_id: str, payload: EmailUpdateRequest):
    """Update email subject and/or body (for generated, not-yet-sent emails)."""
    email = email_storage.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.status == "sent":
        raise HTTPException(status_code=400, detail="Cannot edit an email that has already been sent")
    if payload.subject is not None:
        email.subject = payload.subject
    if payload.body is not None:
        email.body = payload.body
    email_storage.save(email)
    return {"success": True, "email": email}


@app.delete("/api/emails/{email_id}")
async def delete_email(email_id: str):
    """Delete an email (removes email, contact moves to 'no emails' section)"""
    email = email_storage.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    # Delete the email
    email_storage.delete(email_id)
    
    return {"success": True, "message": "Email deleted. Contact moved to 'no emails' section."}


@app.post("/api/emails/bulk-delete")
async def bulk_delete_emails(request: EmailBulkDeleteRequest):
    """Delete multiple generated (not sent) emails. Contacts move to 'no emails' section."""
    deleted = 0
    for email_id in request.email_ids:
        email = email_storage.get(email_id)
        if email:
            email_storage.delete(email_id)
            deleted += 1
    return {"success": True, "deleted": deleted}


# Allowed resume filenames for attachment (must exist in project root)
# Default resume to send: resume28.pdf in project root
ALLOWED_RESUME_FILES = ("resume28.pdf", "resume29.pdf")

# Email sending endpoints
@app.post("/api/send-emails")
async def send_emails(request: EmailSendRequest):
    """Send accepted, pending, or trashed (generated) emails in batch. Optional attach_resume: 'resume28.pdf' or 'resume29.pdf'."""
    # Get emails to send: include accepted, pending, and trashed (so Contacts "Emails Generated - Not Sent" can send any)
    emails_to_send = []
    for email_id in request.email_ids:
        email = email_storage.get(email_id)
        if email and email.status in ("accepted", "pending", "trashed"):
            emails_to_send.append(email)

    if not emails_to_send:
        raise HTTPException(status_code=400, detail="No emails to send (select accepted or pending emails)")
    
    # Check rate limit
    can_send, error = rate_limiter.can_send_email()
    if not can_send:
        raise HTTPException(status_code=429, detail=error)
    
    # Resolve resume path only if client requested one of the allowed files
    resume_path = None
    if request.attach_resume and request.attach_resume in ALLOWED_RESUME_FILES:
        candidate = os.path.join(project_root, request.attach_resume)
        if os.path.isfile(candidate):
            resume_path = candidate
    
    # Authenticate if needed
    try:
        email_sender.authenticate()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail authentication failed: {str(e)}")
    
    # Send emails
    from_email = (request.from_email and request.from_email.strip()) or "me"
    results = email_sender.send_batch(emails_to_send, from_email, resume_path)
    
    # Update email statuses and contacts
    sent_contact_ids = []
    for result in results:
        if result['success']:
            email_id = result['email_id']
            email = email_storage.get(email_id)
            if email:
                email.status = "sent"
                email.sent_at = datetime.now()
                email.gmail_message_id = result.get('gmail_message_id') or result.get('message_id')
                # Track follow-up sent time
                if email.is_follow_up:
                    email.follow_up_sent_at = datetime.now()
                email_storage.save(email)
                sent_contact_ids.append(email.contact_id)
                rate_limiter.record_email_sent()
    
    # Mark contacts as sent in CSV so they appear in the Emailed tab (do not remove them)
    for contact_id in sent_contact_ids:
        csv_processor.update_contact(contact_id, {"status": "sent"})
    
    return {
        "success": True,
        "sent": len([r for r in results if r['success']]),
        "failed": len([r for r in results if not r['success']]),
        "results": results
    }


@app.post("/api/gmail-disconnect")
async def gmail_disconnect():
    """Remove saved Gmail token. Next time you send, the browser will open to sign in again."""
    removed = email_sender.disconnect()
    return {"success": True, "token_removed": removed, "message": "Gmail disconnected. Next time you send, your browser will open to sign in again."}


# Usage stats endpoint
@app.get("/api/usage", response_model=UsageStats)
async def get_usage_stats():
    """Get current usage statistics"""
    stats = rate_limiter.get_usage_stats()
    return UsageStats(**stats)


# Follow-up reminder endpoint
@app.get("/api/follow-up-reminders")
async def get_follow_up_reminders():
    """Get contacts that need follow-up (sent 1+ weeks ago, no response)"""
    candidates = email_storage.get_follow_up_candidates()
    
    # Check for responses if Gmail service is available
    if email_sender.service:
        try:
            response_checker = ResponseChecker(email_sender.service)
            response_checker.check_all_responses(candidates)
            # Save updated emails
            for email in candidates:
                email_storage.save(email)
        except Exception as e:
            print(f"Error checking responses: {e}")
    
    # Filter out any that now have responses
    follow_ups = [e for e in candidates if not e.has_response]
    
    # Get contact info for each
    contacts = []
    for email in follow_ups:
        contact = csv_processor.get_contact(email.contact_id)
        if contact:
            days_since = (datetime.now() - email.sent_at).days if email.sent_at else 0
            contacts.append({
                'contact': contact,
                'email': email,
                'days_since_sent': days_since
            })
    
    return contacts


# Follow-up email generation endpoint
@app.post("/api/generate-follow-up", response_model=GeneratedEmail)
async def generate_follow_up(request: dict):
    """Generate follow-up email for a contact"""
    email_id = request.get('email_id')
    original_email = email_storage.get(email_id)
    
    if not original_email or original_email.status != "sent":
        raise HTTPException(status_code=404, detail="Original email not found")
    
    contact = csv_processor.get_contact(original_email.contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    
    # Get company metadata
    metadata = enrichment_service.enrich_company(contact.company)
    
    # Generate follow-up email (reference original)
    follow_up = email_generator.generate_follow_up(
        contact,
        metadata,
        original_email,
        user_name=request.get('user_name'),
        user_background=request.get('user_background'),
        user_email=request.get('user_email')
    )
    
    # Link to original - follow-up stays in "emailed" section but with pending status
    follow_up.original_email_id = original_email.id
    follow_up.status = "pending"  # Generated but not sent yet
    follow_up.is_follow_up = True
    follow_up.follow_up_generated_at = datetime.now()
    
    email_storage.save(follow_up)
    return follow_up


# Contact categorization endpoint
@app.get("/api/contacts/categorized")
async def get_categorized_contacts():
    """Get contacts organized by email status"""
    all_contacts = csv_processor.read_contacts()
    all_emails = email_storage.get_all()
    
    # Create mapping: contact_id -> email (most recent sent email, or any email)
    email_by_contact = {}
    for email in all_emails:
        contact_id = email.contact_id
        if contact_id not in email_by_contact:
            email_by_contact[contact_id] = email
        else:
            # Prefer sent emails, then most recent
            existing = email_by_contact[contact_id]
            if email.status == "sent" and existing.status != "sent":
                email_by_contact[contact_id] = email
            elif (email.created_at and existing.created_at and 
                  email.created_at > existing.created_at):
                email_by_contact[contact_id] = email
    
    # Categorize contacts
    emailed = []  # email.status == "sent" OR is_follow_up (follow-ups stay in emailed section)
    emails_generated = []  # has email but status != "sent" and not a follow-up (includes pending, accepted, trashed)
    no_emails = []  # no email exists
    
    # Build set of contact IDs that have sent emails (for follow-up detection)
    sent_email_contact_ids = {e.contact_id for e in all_emails if e.status == "sent"}
    
    for contact in all_contacts:
        email = email_by_contact.get(contact.id)
        if email and (email.status == "sent" or email.is_follow_up):
            # Sent emails OR follow-ups (even if not sent yet) go to emailed section
            emailed.append(contact)
        elif email:
            # All other email statuses (pending, accepted, trashed) go here
            # Trashed emails are kept but not sent - they belong in this section
            emails_generated.append(contact)
        else:
            # No email generated yet
            no_emails.append(contact)
    
    # Serialize emails with datetime handling
    emails_dict = {}
    for email in all_emails:
        email_dict = email.model_dump()
        # Convert datetime objects to ISO format strings for JSON serialization
        if email_dict.get('created_at'):
            email_dict['created_at'] = email.created_at.isoformat() if email.created_at else None
        if email_dict.get('sent_at'):
            email_dict['sent_at'] = email.sent_at.isoformat() if email.sent_at else None
        if email_dict.get('response_date'):
            email_dict['response_date'] = email.response_date.isoformat() if email.response_date else None
        if email_dict.get('follow_up_generated_at'):
            email_dict['follow_up_generated_at'] = email.follow_up_generated_at.isoformat() if email.follow_up_generated_at else None
        if email_dict.get('follow_up_sent_at'):
            email_dict['follow_up_sent_at'] = email.follow_up_sent_at.isoformat() if email.follow_up_sent_at else None
        emails_dict[email.contact_id] = email_dict
    
    return {
        "emailed": emailed,
        "emails_generated": emails_generated,
        "no_emails": no_emails,
        "emails": emails_dict  # Include all emails for tracking
    }


@app.get("/")
async def root():
    """Health check"""
    # #region agent log
    import json
    try:
        with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
            f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"main.py:root","message":"Backend health check endpoint called","data":{"status":"ok"},"runId":"run1","hypothesisId":"B"}) + '\n')
    except: pass
    # #endregion
    return {"status": "ok", "message": "AI Cold Emailer API"}
