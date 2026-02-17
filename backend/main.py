from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional
from datetime import datetime
import os
import random
import time
import asyncio
from dotenv import load_dotenv

from models import (
    Contact, CompanyMetadata, GeneratedEmail, 
    EmailGenerationRequest, EmailSendRequest, UsageStats
)
from csv_processor import CSVProcessor
from company_enrichment_service import CompanyEnrichmentService
from email_generator import EmailGenerator
from email_sender import EmailSender
from rate_limiter import RateLimiter
from email_storage import EmailStorage
from response_checker import ResponseChecker

load_dotenv()

# #region agent log
import json
try:
    with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
        f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"main.py:startup","message":"Backend starting","data":{"port":8000},"runId":"run1","hypothesisId":"B"}) + '\n')
except: pass
# #endregion

app = FastAPI(title="AI Cold Emailer API")

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

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
csv_processor = CSVProcessor(
    csv_path=os.getenv('CSV_FILE_PATH', 'data/contacts.csv')
)
enrichment_service = CompanyEnrichmentService(
    cache_path=os.getenv('COMPANY_CACHE_PATH', 'data/company_cache.json')
)
email_generator = EmailGenerator(
    model=os.getenv('OLLAMA_MODEL', 'llama3.2'),
    base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
)
# Get project root (parent of backend directory)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
credentials_path = os.path.join(project_root, 'credentials.json')
token_path = os.path.join(project_root, 'token.json')
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
rate_limiter = RateLimiter()

# Persistent storage for generated emails
email_storage = EmailStorage(
    storage_path=os.getenv('EMAIL_STORAGE_PATH', 'data/generated_emails.json')
)


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
    # #region agent log
    import json
    import time
    log_data = {
        "location": "main.py:create_contact:start",
        "message": "Create contact endpoint called",
        "data": {
            "contact_data": contact.model_dump() if hasattr(contact, 'model_dump') else str(contact),
        },
        "timestamp": int(time.time() * 1000),
        "runId": "add-contact",
        "hypothesisId": "F"
    }
    try:
        with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
            f.write(json.dumps(log_data) + '\n')
    except: pass
    # #endregion
    try:
        result = csv_processor.add_contact(contact)
        # #region agent log
        log_data2 = {
            "location": "main.py:create_contact:success",
            "message": "Contact created successfully",
            "data": {
                "contact_id": result.id if result else None,
            },
            "timestamp": int(time.time() * 1000),
            "runId": "add-contact",
            "hypothesisId": "G"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data2) + '\n')
        except: pass
        # #endregion
        return result
    except Exception as e:
        # #region agent log
        log_data3 = {
            "location": "main.py:create_contact:error",
            "message": "Contact creation failed",
            "data": {
                "error": str(e),
                "error_type": type(e).__name__,
            },
            "timestamp": int(time.time() * 1000),
            "runId": "add-contact",
            "hypothesisId": "H"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data3) + '\n')
        except: pass
        # #endregion
        raise


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


@app.delete("/api/contacts/bulk")
async def bulk_delete_contacts(contact_ids: List[str]):
    """Bulk delete contacts"""
    for contact_id in contact_ids:
        csv_processor.delete_contact(contact_id)
    return {"success": True, "deleted": len(contact_ids)}


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


@app.post("/api/upload")
async def upload_csv(file: UploadFile = File(...)):
    """Upload CSV file to replace or merge contacts"""
    import pandas as pd
    import io
    
    contents = await file.read()
    df = pd.read_csv(io.StringIO(contents.decode('utf-8')))
    
    contacts = []
    for _, row in df.iterrows():
        contact = Contact(
            name=str(row['name']),
            company=str(row['company']),
            email=str(row['email']),
            status=str(row.get('status', 'pending'))
        )
        contacts.append(contact)
    
    csv_processor.write_contacts(contacts)
    return {"success": True, "count": len(contacts)}


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
            user_email=request.user_email
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


@app.delete("/api/emails/{email_id}")
async def delete_email(email_id: str):
    """Delete an email (removes email, contact moves to 'no emails' section)"""
    email = email_storage.get(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    
    # Delete the email
    email_storage.delete(email_id)
    
    return {"success": True, "message": "Email deleted. Contact moved to 'no emails' section."}


# Email sending endpoints
@app.post("/api/send-emails")
async def send_emails(request: EmailSendRequest):
    """Send accepted emails in batch"""
    # Get emails to send
    emails_to_send = []
    for email_id in request.email_ids:
        email = email_storage.get(email_id)
        if email and email.status == "accepted":
            emails_to_send.append(email)
    
    if not emails_to_send:
        raise HTTPException(status_code=400, detail="No accepted emails to send")
    
    # Check rate limit
    can_send, error = rate_limiter.can_send_email()
    if not can_send:
        raise HTTPException(status_code=429, detail=error)
    
    # Authenticate if needed
    try:
        email_sender.authenticate()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail authentication failed: {str(e)}")
    
    # Send emails
    from_email = "me"  # Gmail API uses 'me' for authenticated user
    # Resume path is already resolved in EmailSender.__init__ using project_root
    results = email_sender.send_batch(emails_to_send, from_email, email_sender.resume_path)
    
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
                email_storage.save(email)
                sent_contact_ids.append(email.contact_id)
                rate_limiter.record_email_sent()
    
    # Remove sent contacts from CSV
    csv_processor.remove_sent_contacts(sent_contact_ids)
    
    return {
        "success": True,
        "sent": len([r for r in results if r['success']]),
        "failed": len([r for r in results if not r['success']]),
        "results": results
    }


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
    
    # Link to original
    follow_up.original_email_id = original_email.id
    follow_up.status = "pending"
    
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
    emailed = []  # email.status == "sent"
    emails_generated = []  # has email but status != "sent" (includes pending, accepted, trashed)
    no_emails = []  # no email exists
    
    for contact in all_contacts:
        email = email_by_contact.get(contact.id)
        if email and email.status == "sent":
            # Only "sent" emails go to emailed section
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
