"""
Cold Emailer API — discovery, database, resumes, generation, sending, tracking.
"""
import io
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

load_dotenv()
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
_root_env = os.path.join(_PROJECT_ROOT, ".env")
if os.path.isfile(_root_env):
    load_dotenv(_root_env)

from db import (Database, body_claims_attachment, migrate_legacy_data, now_iso,
                repair_contact_reply_status, repair_delivered_email_status,
                repair_mismatched_company_sites,
                repair_offdomain_contact_warnings,
                repair_speculative_company_summaries,
                repair_unverified_legacy_replies)
from discovery import DiscoveryService
from email_composer import (EmailComposer, EMAIL_TYPES, DEFAULT_TYPE,
                            TemplateUnavailable)
from email_sender import EmailSender
from enrichment import EnrichmentService, scrape_status_for
from generation import GenerationBusy, GenerationService
from models import (
    EMAIL_ADDRESS_RE, BulkIds, BulkStatus, CompanyCreate, CompanyUpdate,
    ContactCreate, ContactUpdate, DiscoveryRequest, EmailUpdate,
    GenerateRequest, ProfileUpdate, ResumeUpdate, SendRequest,
)
from rate_limiter import RateLimiter
from resume_service import ResumeService
from response_checker import ResponseChecker

try:
    from llm_client import get_cloud_llm_provider
except ImportError:
    get_cloud_llm_provider = lambda: None

app = FastAPI(title="Cold Emailer API", version="2.0")

_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
_allowed_origins = {o.strip() for o in _cors_origins if o.strip()}
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _block_cross_site_writes(request: Request, call_next):
    """CSRF guard. CORS only hides responses — it does not stop cross-site
    'simple' requests (form posts, multipart, body-less POSTs) from executing.
    Reject any state-changing request whose Origin header is present and not
    an allowed frontend origin. Non-browser clients send no Origin and pass."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin not in _allowed_origins:
            return JSONResponse(status_code=403,
                                content={"detail": "Cross-site request blocked"})
    return await call_next(request)

# ---------- service wiring ----------

db = Database()
enrichment = EnrichmentService()
resumes = ResumeService(db)
composer = EmailComposer(db, resumes)
rate_limiter = RateLimiter(db)
discovery = DiscoveryService(db, enrichment)
generation = GenerationService(db, composer, enrichment, rate_limiter)
email_sender = EmailSender(
    credentials_path=os.getenv("CREDENTIALS_JSON_PATH") or os.path.join(_PROJECT_ROOT, "credentials.json"),
    token_path=os.getenv("TOKEN_JSON_PATH") or os.path.join(_PROJECT_ROOT, "token.json"),
)

_send_lock = threading.Lock()  # one send batch at a time


def _seed_profile_once():
    """Seed profile from the legacy hardcoded values / skills.md so existing
    users keep working emails; everything is editable in Settings."""
    if db.get_setting("profile_seeded"):
        return
    profile = db.get_profile()
    if not any(profile.values()):
        background = ""
        skills_path = os.getenv("SKILLS_FILE_PATH") or os.path.join(_PROJECT_ROOT, "skills.md")
        if os.path.isfile(skills_path):
            try:
                with open(skills_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if "## Email one-liner" in content:
                    section = content.split("## Email one-liner", 1)[-1]
                    section = section.split("##")[0].strip()
                    for line in section.splitlines():
                        if line.strip() and not line.strip().startswith("#"):
                            background = line.strip()
                            break
            except OSError:
                pass
        db.update_profile({
            "full_name": "Jason Li",
            "school": "University of Pennsylvania",
            "email": "li59@seas.upenn.edu",
            "phone": "847-907-0871",
            "website": (os.getenv("PERSONAL_WEBSITE_URL")
                        or "https://personal-site-zeta-ashy-80.vercel.app/").strip(),
            "background": background,
        })
    db.set_setting("profile_seeded", True)


def _reap_orphaned_jobs():
    """Jobs run in threads that die with the process. Anything still marked
    'running' at startup is a leftover from a crash or restart — leave it
    that way and every new discovery/generation request 409s forever."""
    orphans = db.query("SELECT id, type FROM jobs WHERE status='running'")
    for job in orphans:
        db.finish_job(job["id"], status="failed",
                      error="Interrupted — the server restarted while this job was running.")
    if orphans:
        print(f"[startup] reaped {len(orphans)} interrupted job(s)")


migrate_legacy_data(db, _BACKEND_DIR)
repair_delivered_email_status(db)
repair_unverified_legacy_replies(db)
repair_contact_reply_status(db)
repair_mismatched_company_sites(db)
repair_speculative_company_summaries(db)
repair_offdomain_contact_warnings(db)
resumes.migrate_legacy_resumes(_PROJECT_ROOT)
_seed_profile_once()
_reap_orphaned_jobs()


def _parse_job(job: Optional[dict]) -> Optional[dict]:
    if not job:
        return None
    out = dict(job)
    for key in ("payload", "result"):
        if out.get(key):
            try:
                out[key] = json.loads(out[key])
            except Exception:
                pass
    return out


# ---------- health / settings ----------

@app.get("/")
async def root():
    return {"status": "ok", "message": "Cold Emailer API v2"}


@app.get("/api/settings")
async def get_settings():
    profile = db.get_profile()
    # Every email is written as this person. Missing pieces don't fail loudly,
    # they just produce weak, unsigned emails — so say so up front.
    missing = [label for key, label in (
        ("full_name", "your name"),
        ("email", "your email address"),
        ("background", "a background one-liner"),
    ) if not (profile.get(key) or "").strip()]
    return {
        "profile": profile,
        "profile_incomplete": missing,
        "llm_provider": get_cloud_llm_provider(),
        "gmail_connected": email_sender.is_connected(),
        "gmail_credentials_present": os.path.isfile(email_sender.credentials_path),
        "limits": rate_limiter.daily_limits,
    }


@app.put("/api/settings")
async def update_settings(payload: ProfileUpdate):
    profile = db.update_profile(payload.model_dump(exclude_none=True))
    return {"success": True, "profile": profile}


# ---------- discovery ----------

@app.post("/api/discovery")
async def start_discovery(payload: DiscoveryRequest):
    running = [j for j in db.list_jobs("discovery", limit=5) if j["status"] == "running"]
    if running:
        raise HTTPException(409, "A discovery search is already running. "
                                 "Wait for it to finish or cancel it first.")
    job = discovery.start(payload.query.strip(), payload.count)
    return _parse_job(db.get_job(job["id"]))


@app.get("/api/discovery")
async def list_discovery_jobs():
    return [_parse_job(j) for j in db.list_jobs("discovery", limit=20)]


@app.get("/api/discovery/{job_id}")
async def get_discovery_job(job_id: str):
    job = db.get_job(job_id)
    if not job or job["type"] != "discovery":
        raise HTTPException(404, "Discovery run not found")
    out = _parse_job(job)
    # Include companies found so far for live results display
    out["companies"] = db.query(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM contacts ct WHERE ct.company_id=c.id) AS contact_count
           FROM companies c WHERE c.job_id=? ORDER BY c.created_at ASC""",
        (job_id,),
    )
    return out


@app.post("/api/discovery/{job_id}/cancel")
async def cancel_discovery(job_id: str):
    job = db.get_job(job_id)
    if not job or job["type"] != "discovery":
        raise HTTPException(404, "Discovery run not found")
    if not discovery.cancel(job_id):
        raise HTTPException(409, "That search already finished")
    return {"success": True}


# ---------- jobs (generic polling) ----------

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = _parse_job(db.get_job(job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ---------- companies ----------

@app.get("/api/companies")
async def list_companies(search: Optional[str] = None):
    return db.list_companies(search=search)


@app.get("/api/companies/{company_id}")
async def get_company(company_id: str):
    company = db.get_company(company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    company["contacts"] = db.list_contacts(company_id=company_id)
    company["emails"] = db.query(
        """SELECT e.*, ct.name AS contact_name, ct.email AS contact_email
           FROM emails e JOIN contacts ct ON e.contact_id = ct.id
           WHERE ct.company_id=? ORDER BY e.created_at DESC""",
        (company_id,),
    )
    return company


def _enrich_company_async(company_id: str):
    company = db.get_company(company_id)
    if not company:
        return
    db.update_company(company_id, {"scrape_status": "scraping"})
    try:
        enriched = enrichment.enrich(company["name"], company.get("url"))
        updates = {k: v for k, v in enriched.items()
                   if k in ("url", "domain", "summary", "industry", "product", "hook",
                            "recent_news", "why_care", "location", "scraped_at") and v}
        updates["scrape_status"] = scrape_status_for(enriched)
        db.update_company(company_id, updates)
        # Add newly found contacts (max 2, skip duplicates)
        from discovery import _guess_name_from_email, _role_for_email
        from enrichment import select_outreach_emails
        domain = (db.get_company(company_id) or {}).get("domain")
        for addr, note in select_outreach_emails(enriched.get("emails") or [], domain):
            if db.find_contact_by_email(addr):
                continue
            local = addr.split("@", 1)[0]
            db.create_contact(company_id=company_id, email=addr,
                              name=_guess_name_from_email(local),
                              role=_role_for_email(local),
                              source="discovery", status="new", notes=note)
        # Log what actually happened — an unconditional "researched" here made
        # the activity feed claim success for wrong-site and failed scrapes.
        status = updates["scrape_status"]
        if status == "scraped":
            db.log_event("company", company_id, "enriched", company["name"])
        elif status == "wrong_site":
            db.log_event("company", company_id, "research_failed",
                         f"{company['name']}: {enriched.get('mismatch') or 'wrong site'}")
        else:
            db.log_event("company", company_id, "research_failed",
                         f"{company['name']}: no usable information found")
    except Exception as e:
        db.update_company(company_id, {"scrape_status": "scrape_failed"})
        db.log_event("company", company_id, "research_failed", company["name"])
        print(f"[enrich] failed for {company_id}: {e}")


@app.post("/api/companies")
async def create_company(payload: CompanyCreate):
    existing = db.find_company_by_name(payload.name)
    if existing:
        raise HTTPException(409, f"'{payload.name}' is already in your database")
    company = db.create_company(payload.name, url=payload.url, source="manual")
    can, limit_msg = rate_limiter.can_research_company()
    if can:
        rate_limiter.record_company_research()
        threading.Thread(target=_enrich_company_async, args=(company["id"],),
                         daemon=True).start()
    # Tell the caller whether research actually started; the UI used to
    # promise "researching it now" even when the limiter silently skipped it.
    row = db.get_company(company["id"])
    row["research_started"] = can
    row["research_skipped_reason"] = None if can else limit_msg
    return row


@app.post("/api/companies/{company_id}/enrich")
async def enrich_company(company_id: str):
    company = db.get_company(company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    can, err = rate_limiter.can_research_company()
    if not can:
        raise HTTPException(429, err)
    rate_limiter.record_company_research()
    threading.Thread(target=_enrich_company_async, args=(company_id,),
                     daemon=True).start()
    return {"success": True, "message": "Research started"}


@app.put("/api/companies/{company_id}")
async def update_company(company_id: str, payload: CompanyUpdate):
    if not db.get_company(company_id):
        raise HTTPException(404, "Company not found")
    db.update_company(company_id, payload.model_dump(exclude_none=True))
    return db.get_company(company_id)


@app.delete("/api/companies/{company_id}")
async def delete_company(company_id: str, force: bool = Query(False)):
    """Deleting cascades companies -> contacts -> emails, delivered ones
    included. Every other delete path refuses to erase sent mail, so this one
    has to as well: that record is what stops the same person being emailed
    twice, and losing it also refunds today's send cap."""
    company = db.get_company(company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    if not force:
        counts = db.query_one(
            """SELECT
                 SUM(CASE WHEN e.status='sent' OR (e.gmail_message_id IS NOT NULL
                          AND e.gmail_message_id <> '') THEN 1 ELSE 0 END) AS sent,
                 SUM(CASE WHEN e.has_response=1 THEN 1 ELSE 0 END) AS replies
               FROM emails e JOIN contacts ct ON e.contact_id = ct.id
               WHERE ct.company_id=?""", (company_id,)) or {}
        sent = counts.get("sent") or 0
        replies = counts.get("replies") or 0
        if sent:
            reply_note = (f" and {replies} repl{'y' if replies == 1 else 'ies'}"
                          if replies else "")
            raise HTTPException(
                409, f"{company['name']} has {sent} sent email(s){reply_note}. "
                     f"Deleting removes that record, which is what stops the same "
                     f"person being emailed twice — archive the contacts instead, "
                     f"or confirm to delete anyway.")
    db.delete_company(company_id)
    return {"success": True}


# ---------- contacts ----------

@app.get("/api/contacts")
async def list_contacts(company_id: Optional[str] = None, search: Optional[str] = None):
    return db.list_contacts(company_id=company_id, search=search)


CONTACT_STATUSES = ("new", "drafted", "sent", "replied", "archived")


@app.post("/api/contacts")
async def create_contact(payload: ContactCreate):
    email = (payload.email or "").strip()
    if email and db.find_contact_by_email(email):
        raise HTTPException(409, f"A contact with email {email} already exists")
    company_id = payload.company_id
    if company_id and not db.get_company(company_id):
        raise HTTPException(404, "That company no longer exists")
    if not company_id and payload.company_name and payload.company_name.strip():
        existing = db.find_company_by_name(payload.company_name)
        company_id = existing["id"] if existing else db.create_company(
            payload.company_name, source="manual")["id"]
    contact = db.create_contact(
        name=payload.name, email=email, role=payload.role,
        company_id=company_id, notes=payload.notes, source="manual",
    )
    return db.get_contact(contact["id"])


@app.put("/api/contacts/{contact_id}")
async def update_contact(contact_id: str, payload: ContactUpdate):
    if not db.get_contact(contact_id):
        raise HTTPException(404, "Contact not found")
    updates = payload.model_dump(exclude_none=True)
    if updates.get("company_id") and not db.get_company(updates["company_id"]):
        raise HTTPException(404, "That company no longer exists")
    if "status" in updates and updates["status"] not in CONTACT_STATUSES:
        raise HTTPException(400, f"Status must be one of: {', '.join(CONTACT_STATUSES)}")
    new_email = (updates.get("email") or "").strip()
    if new_email:
        clash = db.find_contact_by_email(new_email)
        if clash and clash["id"] != contact_id:
            raise HTTPException(409, f"Another contact already uses {new_email}")
    db.update_contact(contact_id, updates)
    return db.get_contact(contact_id)


@app.delete("/api/contacts/{contact_id}")
async def delete_contact(contact_id: str, force: bool = Query(False)):
    """Deleting cascades to that contact's emails. Contacts with sent history
    are protected unless force=true, so a stray click can't erase the record
    of what you actually sent."""
    contact = db.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    if not force:
        sent = db.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE contact_id=? AND status='sent'",
            (contact_id,))["n"]
        if sent:
            raise HTTPException(
                409, f"This contact has {sent} sent email(s). Deleting removes that "
                     f"history too — archive them instead, or confirm to delete anyway.")
    db.delete_contact(contact_id)
    return {"success": True}


@app.post("/api/contacts/bulk-delete")
async def bulk_delete_contacts(payload: BulkIds, force: bool = Query(False)):
    deleted, protected = 0, []
    for cid in payload.ids:
        if not force:
            row = db.query_one(
                "SELECT COUNT(*) AS n FROM emails WHERE contact_id=? AND status='sent'",
                (cid,))
            if row and row["n"]:
                contact = db.get_contact(cid)
                protected.append((contact or {}).get("email") or cid)
                continue
        if db.delete_contact(cid):
            deleted += 1
    return {"success": True, "deleted": deleted, "protected": protected}


MAX_CSV_BYTES = 5 * 1024 * 1024


def _decode_csv(raw: bytes) -> str:
    """Decode an uploaded CSV. Excel on Windows still exports cp1252 and
    Excel on Mac exports mac_roman, so UTF-8-only decoding rejects the most
    common real-world exports.

    UTF-16 is only attempted when a BOM says so: `bytes.decode('utf-16')`
    assumes little-endian without one and happily turns ordinary cp1252 text
    with an even byte count into CJK mojibake instead of raising — which
    surfaced to the user as a bogus "missing required column" error.
    """
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except (UnicodeDecodeError, UnicodeError):
            pass
    for encoding in ("utf-8-sig", "cp1252", "mac_roman", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    # latin-1 accepts any byte sequence, so reaching here means empty/binary
    raise ValueError("Could not read this file as text. Export it as CSV and try again.")


@app.post("/api/contacts/import")
async def import_contacts_csv(file: UploadFile = File(...)):
    """CSV import. Columns: name, company, email (header required). Extra
    columns 'role' and 'notes' are picked up when present."""
    import csv as _csv
    try:
        raw = await file.read()
        if len(raw) > MAX_CSV_BYTES:
            raise ValueError(f"CSV is too large (max {MAX_CSV_BYTES // (1024 * 1024)} MB)")
        text = _decode_csv(raw)
        reader = _csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        cols = {c.strip().lower(): c for c in reader.fieldnames}
        missing = {"name", "company", "email"} - set(cols)
        if missing:
            raise ValueError(
                f"Missing required column(s): {', '.join(sorted(missing))}. "
                "Header must include: name, company, email")
        # Report why rows were dropped separately — "duplicates skipped" is a
        # misleading thing to tell someone whose addresses were malformed.
        added = duplicates = invalid = 0
        invalid_samples = []
        for row in reader:
            get = lambda k: (row.get(cols[k]) or "").strip() if k in cols else ""
            email = get("email")
            if not email or not EMAIL_ADDRESS_RE.fullmatch(email):
                invalid += 1
                if len(invalid_samples) < 5:
                    invalid_samples.append(email or "(blank)")
                continue
            if db.find_contact_by_email(email):
                duplicates += 1
                continue
            company_id = None
            company_name = get("company")
            if company_name:
                existing = db.find_company_by_name(company_name)
                company_id = existing["id"] if existing else db.create_company(
                    company_name, source="csv")["id"]
            db.create_contact(
                name=get("name"), email=email, company_id=company_id,
                role=get("role") or None, notes=get("notes") or None, source="csv",
            )
            added += 1
        db.log_event("contact", None, "imported", f"{added} contacts from CSV")
        return {"success": True, "added": added,
                "duplicates": duplicates, "invalid": invalid,
                "invalid_samples": invalid_samples,
                "skipped": duplicates + invalid}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Invalid CSV: {e}")


def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection. Scraped names and roles reach
    this file, and Excel/Sheets execute a cell that starts with = + - @."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@app.get("/api/contacts/export")
async def export_contacts_csv():
    import csv as _csv
    contacts = db.list_contacts()
    output = io.StringIO()
    writer = _csv.writer(output)
    writer.writerow(["name", "email", "company", "role", "status", "source", "created_at"])
    for c in contacts:
        writer.writerow([_csv_safe(c.get(k)) for k in
                         ("name", "email", "company_name", "role", "status",
                          "source", "created_at")])
    return Response(
        content=output.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


# ---------- resumes ----------

@app.get("/api/resumes")
async def list_resumes():
    return resumes.list()


@app.post("/api/resumes")
async def upload_resume(file: UploadFile = File(...), label: str = Form("")):
    try:
        data = await file.read()
        row = resumes.save_upload(data, file.filename or "resume.pdf", label)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return resumes.public(row)


@app.put("/api/resumes/{resume_id}")
async def update_resume(resume_id: str, payload: ResumeUpdate):
    row = resumes.update(resume_id, label=payload.label, is_default=payload.is_default)
    if not row:
        raise HTTPException(404, "Resume not found")
    return resumes.public(row)


@app.delete("/api/resumes/{resume_id}")
async def delete_resume(resume_id: str, force: bool = Query(False)):
    """Refuse by default when unsent drafts still reference this resume.

    Those drafts were written around this specific PDF (and may say "my resume
    is attached"). Deleting it silently substitutes a different file at send
    time, so the recipient gets a resume the email was never written for.
    """
    row = resumes.get(resume_id)
    if not row:
        raise HTTPException(404, "Resume not found")
    if not force:
        counted = db.query_one(
            "SELECT COUNT(*) AS n FROM emails "
            "WHERE resume_id = ? AND status IN ('draft', 'approved')",
            (resume_id,))
        pending = counted["n"] if counted else 0
        # Drafts saved before resume_id was recorded (and every send that used
        # the default) carry no resume_id at all, so matching on the column
        # alone missed exactly the drafts written around the default resume.
        if row.get("is_default"):
            pending += sum(
                1 for e in db.query(
                    "SELECT body FROM emails WHERE resume_id IS NULL "
                    "AND status IN ('draft', 'approved')")
                if body_claims_attachment(e["body"]))
        if pending:
            raise HTTPException(
                409, f"{pending} unsent draft(s) were written around this resume. "
                     f"Deleting it would attach a different PDF than the email "
                     f"describes. Regenerate or send those drafts first.")
    resumes.delete(resume_id)
    return {"success": True}


@app.get("/api/resumes/{resume_id}/file")
async def get_resume_file(resume_id: str):
    row = resumes.get(resume_id)
    if not row or not os.path.isfile(row["path"]):
        raise HTTPException(404, "Resume file not found")
    return FileResponse(row["path"], media_type="application/pdf",
                        filename=row["filename"])


# ---------- email types ----------

@app.get("/api/email-types")
async def list_email_types():
    return [{"id": key, "label": spec["label"], "goal": spec["goal"]}
            for key, spec in EMAIL_TYPES.items()]


# ---------- email generation / review ----------

@app.post("/api/emails/generate")
async def generate_emails(payload: GenerateRequest):
    running = [j for j in db.list_jobs("generation", limit=5) if j["status"] == "running"]
    if running:
        raise HTTPException(409, "Email generation is already running. "
                                 "Wait for it to finish or cancel it first.")
    if payload.resume_id and not db.get_resume(payload.resume_id):
        raise HTTPException(404, "Selected resume not found")
    if payload.email_type == "custom":
        # Fail fast rather than starting a job that can only skip every
        # contact: there is no offline template that follows instructions.
        if not (payload.custom_instructions or "").strip():
            raise HTTPException(400, "Custom emails need instructions describing "
                                     "what the AI should write")
        if payload.use_template_only or not get_cloud_llm_provider():
            raise HTTPException(400, 'Custom emails need AI — the plain template '
                                     'cannot follow your instructions. Uncheck '
                                     '"Skip AI", or set an AI provider in Settings.')
    try:
        job = generation.start(
            contact_ids=payload.contact_ids,
            email_type=payload.email_type,
            resume_id=payload.resume_id,
            custom_instructions=payload.custom_instructions,
            use_template_only=payload.use_template_only,
            allow_recontact=payload.allow_recontact,
        )
    except GenerationBusy as e:
        # A cancelled job flips its row to 'cancelled' while the worker is still
        # composing, so the check above cannot see it. Starting now would race
        # that thread into two identical drafts for the same contact.
        raise HTTPException(409, str(e))
    return _parse_job(db.get_job(job["id"]))


@app.post("/api/emails/generate/{job_id}/cancel")
async def cancel_generation(job_id: str):
    job = db.get_job(job_id)
    if not job or job["type"] != "generation":
        raise HTTPException(404, "Generation run not found")
    if not generation.cancel(job_id):
        raise HTTPException(409, "That generation already finished")
    return {"success": True}


@app.get("/api/emails")
async def list_emails(status: Optional[str] = None):
    return db.list_emails(status=status)


@app.get("/api/emails/{email_id}")
async def get_email(email_id: str):
    email = db.get_email(email_id)
    if not email:
        raise HTTPException(404, "Email not found")
    return email


def _already_delivered(email: Optional[dict]) -> bool:
    """True once Gmail has actually delivered this message.

    `status` alone is not proof: legacy rows arrived carrying a Gmail message
    id while still labelled approved/trashed, which put delivered mail back in
    Drafts with a live Send button. A message id (or a send timestamp) means it
    went out, whatever the status column says.
    """
    if not email:
        return False
    return bool(str(email.get("gmail_message_id") or "").strip()
                or str(email.get("sent_at") or "").strip())


def _send_attempt_pending(email: Optional[dict]) -> bool:
    """True when this row was handed to Gmail and we never heard the verdict.

    A lost response (read timeout, reset, 5xx) is indistinguishable from a
    message Gmail accepted and queued, so the row is not a safe retry: the
    recipient may already have it. Sending again needs either proof from the
    Sent folder or the user saying so explicitly.
    """
    if not email or _already_delivered(email):
        return False
    return bool(str(email.get("send_attempted_at") or "").strip())


@app.patch("/api/emails/{email_id}")
async def update_email(email_id: str, payload: EmailUpdate):
    email = db.get_email(email_id)
    if not email:
        raise HTTPException(404, "Email not found")
    if email["status"] == "sent" or _already_delivered(email):
        raise HTTPException(400, "Cannot edit an email that has already been sent")
    updates = payload.model_dump(exclude_none=True)
    if "status" in updates and updates["status"] not in ("draft", "approved", "trashed"):
        raise HTTPException(400, "Status must be draft, approved, or trashed")
    db.update_email(email_id, updates)
    return db.get_email(email_id)


@app.post("/api/emails/bulk-status")
async def bulk_email_status(payload: BulkStatus):
    if payload.status not in ("draft", "approved", "trashed"):
        raise HTTPException(400, "Status must be draft, approved, or trashed")
    updated = 0
    for email_id in payload.email_ids:
        email = db.get_email(email_id)
        # A delivered email must not be restorable to drafts — that is two
        # clicks from sending a real contact the same message twice.
        if email and email["status"] != "sent" and not _already_delivered(email):
            db.update_email(email_id, {"status": payload.status})
            updated += 1
    return {"success": True, "updated": updated}


@app.delete("/api/emails/{email_id}")
async def delete_email(email_id: str):
    email = db.get_email(email_id)
    if not email:
        raise HTTPException(404, "Email not found")
    if email["status"] == "sent" or _already_delivered(email):
        # Sent mail is the record of what a real person received. It also
        # backs the duplicate-contact guard, so deleting it re-arms a second
        # first-contact email to someone already emailed.
        raise HTTPException(
            409, "This email was already delivered. Its record is what stops "
                 "the same person being emailed twice, so it can't be deleted.")
    db.delete_email(email_id)
    return {"success": True}


@app.post("/api/emails/bulk-delete")
async def bulk_delete_emails(payload: BulkIds):
    deleted, protected = 0, 0
    for eid in payload.ids:
        email = db.get_email(eid)
        if not email:
            continue
        if email["status"] == "sent" or _already_delivered(email):
            protected += 1
            continue
        if db.delete_email(eid):
            deleted += 1
    return {"success": True, "deleted": deleted, "protected_sent": protected}


@app.post("/api/emails/{email_id}/regenerate")
async def regenerate_email(email_id: str):
    email = db.get_email(email_id)
    if not email:
        raise HTTPException(404, "Email not found")
    if email["status"] == "sent" or _already_delivered(email):
        raise HTTPException(400, "Cannot regenerate a sent email")
    can, err = rate_limiter.can_generate_email()
    if not can:
        raise HTTPException(429, err)
    rate_limiter.record_email_generation()
    contact = db.get_contact(email["contact_id"])
    if not contact:
        raise HTTPException(404, "Contact for this email no longer exists")
    company = db.get_company(contact["company_id"]) if contact.get("company_id") else None
    if email.get("is_follow_up") or email.get("email_type") == "follow_up":
        # A follow-up is not a first-contact email: composing it as one would
        # replace the draft with a cold "Internship inquiry" that never
        # references the message it is following up on.
        original = db.get_email(email.get("original_email_id") or "")
        if not original:
            raise HTTPException(400, "The original email this follow-up replies to "
                                     "no longer exists, so it cannot be rewritten.")
        composed = composer.compose_follow_up(contact, company, original)
    else:
        try:
            composed = composer.compose(
                contact, company,
                email_type=email.get("email_type") or DEFAULT_TYPE,
                resume_id=email.get("resume_id"),
                custom_instructions=email.get("custom_instructions"),
            )
        except TemplateUnavailable as e:
            raise HTTPException(400, str(e))
    db.update_email(email_id, {
        "subject": composed["subject"], "body": composed["body"],
        "status": "draft",
        "used_template_fallback": composed["used_template_fallback"],
        "fallback_reason": composed["fallback_reason"],
    })
    db.log_event("email", email_id, "regenerated", contact.get("name") or "")
    return db.get_email(email_id)


# ---------- follow-ups ----------

@app.get("/api/follow-ups")
async def follow_ups_due(days: int = Query(7, ge=1, le=90)):
    candidates = db.get_follow_up_candidates(days=days)
    for c in candidates:
        try:
            sent = datetime.fromisoformat(c["sent_at"])
            c["days_since_sent"] = (datetime.now() - sent).days
        except Exception:
            c["days_since_sent"] = None
    return candidates


@app.post("/api/emails/{email_id}/follow-up")
async def generate_follow_up(email_id: str):
    original = db.get_email(email_id)
    if not original or original["status"] != "sent":
        raise HTTPException(404, "Original sent email not found")
    # Nothing dedupes downstream: two clicks used to mean two near-identical
    # follow-ups in Drafts, both caught by "Select all", both delivered.
    #
    # The question is per *person*, not per row — the same way the reply guard
    # below and get_follow_up_candidates ask it. Someone with several sent
    # first-contact emails was otherwise offered (and given) one follow-up per
    # email, i.e. two "just following up on my note" messages to one human.
    existing = db.query_one(
        "SELECT f.id FROM emails f JOIN emails o ON f.original_email_id = o.id "
        "WHERE f.status <> 'trashed' AND (o.id = ? OR (? IS NOT NULL "
        "AND o.contact_id = ?)) ORDER BY f.created_at DESC LIMIT 1",
        (email_id, original.get("contact_id"), original.get("contact_id")))
    if existing:
        raise HTTPException(409, "A follow-up to this person has already been "
                                 "drafted — find it in Drafts.")
    # A follow-up is written on the premise of silence ("no reply yet"). The
    # reply can easily sit on a sibling email, so ask about the *person*, the
    # way get_follow_up_candidates does — the row-level has_response flag says
    # nothing about whether they have already written back.
    #
    # Only a reply the current checker verified may block this. An unverified
    # legacy flag (the old checker counted bounces, auto-replies and our own
    # messages) is not evidence, and letting it 409 switched follow-ups off for
    # most of the database.
    if original.get("contact_id") and db.query_one(
            "SELECT 1 FROM emails WHERE contact_id=? AND has_response=1 "
            "AND response_verified_at IS NOT NULL LIMIT 1",
            (original["contact_id"],)):
        raise HTTPException(
            409, "This person already replied (on one of your emails to them), so "
                 "a \"no reply yet\" follow-up would be wrong. Reply in Gmail "
                 "instead.")
    can, err = rate_limiter.can_generate_email()
    if not can:
        raise HTTPException(429, err)
    rate_limiter.record_email_generation()
    contact = db.get_contact(original["contact_id"])
    if not contact:
        raise HTTPException(404, "Contact for this email no longer exists")
    company = db.get_company(contact["company_id"]) if contact.get("company_id") else None
    composed = composer.compose_follow_up(contact, company, original)
    follow_up = db.create_email(
        contact_id=contact["id"],
        company_id=contact.get("company_id"),
        email_type="follow_up",
        resume_id=original.get("resume_id"),
        subject=composed["subject"],
        body=composed["body"],
        status="draft",
        original_email_id=original["id"],
        is_follow_up=True,
        used_template_fallback=composed["used_template_fallback"],
        fallback_reason=composed["fallback_reason"],
    )
    db.log_event("email", follow_up["id"], "follow_up_generated",
                 contact.get("name") or contact.get("email") or "")
    return db.get_email(follow_up["id"])


# ---------- sending ----------

def _resume_allowed_for(email: dict) -> bool:
    """Whether a resume may be stapled to this email.

    Sales pitches are written on the explicit premise that nothing is
    attached. A follow-up carries email_type='follow_up', which is not in
    EMAIL_TYPES at all, so asking the table directly used to return no spec
    and quietly fall through to "attach" — stapling the default resume to a
    follow-up of a sales pitch. Resolve the premise through the original.
    """
    email_type = email.get("email_type") or ""
    if email.get("is_follow_up") or email_type == "follow_up":
        original = db.get_email(email.get("original_email_id") or "") or {}
        spec = EMAIL_TYPES.get(original.get("email_type") or "")
        # Original gone (or itself a follow-up): its premise is unknowable,
        # so err toward not attaching rather than toward a stray PDF.
        return bool(spec) and spec.get("resume_weight") != "none"
    spec = EMAIL_TYPES.get(email_type) or EMAIL_TYPES[DEFAULT_TYPE]
    return spec.get("resume_weight") != "none"


def _resolved_attachment_id(email: dict, attach: bool,
                            resume_override: Optional[str]) -> Optional[str]:
    """Which resume will actually be stapled to this email, or None.

    Single source of truth for the send path: the batch attaches what this
    returns, and the request handler compares it against what the body promises.
    """
    if not (attach and _resume_allowed_for(email)):
        return None
    for candidate in (resume_override, email.get("resume_id")):
        if candidate and resumes.resolve_attachment_path(candidate):
            return candidate
    default = db.get_default_resume()
    if default and resumes.resolve_attachment_path(default["id"]):
        return default["id"]
    return None


def _attachment_claim_conflict(email: dict, attach: bool,
                               resume_override: Optional[str]) -> Optional[str]:
    """Why sending this row would contradict its own body, or None.

    The body's "my resume is attached" is decided once, at compose time, around
    one specific PDF. The send dialog can still untick "Attach resume" or staple
    a different file to every draft, and nothing compared the two — so the
    recipient got an email promising an attachment that isn't there, or a resume
    the email was never written around. Deleting a resume is refused for exactly
    this reason (409 on /api/resumes/{id}); sending must not hold a lower
    standard.
    """
    if not email.get("claims_attachment"):
        return None
    default = db.get_default_resume() or {}
    # A draft with no resume_id was written around the default at the time.
    expected = email.get("resume_id") or default.get("id")
    resolved = _resolved_attachment_id(email, attach, resume_override)
    if not resolved:
        return "the body says a resume is attached, but nothing would be attached"
    if expected and resolved != expected:
        return ("the body was written around a different resume than the one "
                "that would be attached")
    return None


def _send_batch_job(job_id: str, email_ids, resume_override: Optional[str],
                    attach: bool, from_email: str, confirm_resend: bool = False):
    sent = failed = 0
    results = []
    cancelled = False
    # Recipients this batch has already sent a first-contact email to. Two
    # drafts for the same person (a cancelled generation racing its retry) are
    # both picked up by "Select all", and nothing else dedupes at send time.
    first_contact_recipients = set()
    # Same rule for follow-ups: two follow-ups to one person are two copies of
    # "I wanted to follow up on my note" three seconds apart.
    follow_up_recipients = set()
    try:
        for i, email_id in enumerate(email_ids):
            job = db.get_job(job_id)
            if not job or job["status"] == "cancelled":
                cancelled = True
                break
            email = db.get_email(email_id)
            if not email or email["status"] not in ("draft", "approved"):
                results.append({"email_id": email_id, "success": False,
                                "retryable": False, "error": "not sendable"})
                continue
            if _already_delivered(email):
                # Re-checked here and not only in the handler: a second send of
                # this row would also overwrite gmail_message_id and orphan the
                # thread that reply tracking follows.
                results.append({"email_id": email_id, "success": False,
                                "retryable": False, "error": "already sent"})
                failed += 1
                continue
            recipient = str(email.get("contact_email") or "").strip().lower()
            if not email.get("is_follow_up") and recipient in first_contact_recipients:
                results.append({
                    "email_id": email_id, "success": False, "retryable": False,
                    "error": "a first-contact email to this recipient already went "
                             "out in this batch"})
                failed += 1
                continue
            if email.get("is_follow_up") and recipient in follow_up_recipients:
                results.append({
                    "email_id": email_id, "success": False, "retryable": False,
                    "error": "a follow-up to this recipient already went out in "
                             "this batch"})
                failed += 1
                continue
            if _send_attempt_pending(email):
                # An earlier attempt reached Gmail with no answer back. Ask the
                # Sent folder first — if it landed, record that instead of
                # sending the recipient a second copy.
                found = email_sender.find_delivered_message(
                    email.get("contact_email"), email.get("subject"),
                    sent_after=email.get("send_attempted_at"))
                if found and db.query_one(
                        "SELECT id FROM emails WHERE gmail_message_id=? AND id<>?",
                        (found.get("gmail_message_id"), email_id)):
                    # That message is already recorded against another row, so it
                    # is an older copy of the same subject to the same person —
                    # not proof that *this* attempt landed.
                    found = None
                if found:
                    db.update_email(email_id, {
                        "status": "sent", "sent_at": now_iso(),
                        "gmail_message_id": found.get("gmail_message_id"),
                        "gmail_thread_id": found.get("gmail_thread_id"),
                        "send_attempted_at": None, "send_attempt_error": None,
                    })
                    db.log_event("email", email_id, "send_reconciled",
                                 f"found in Gmail Sent → {email.get('contact_email')}")
                    results.append({
                        "email_id": email_id, "success": False, "retryable": False,
                        "error": "already sent — the earlier attempt did land, and "
                                 "it is now recorded as sent"})
                    failed += 1
                    continue
                if not confirm_resend:
                    results.append({
                        "email_id": email_id, "success": False,
                        "delivery_unknown": True,
                        "error": "an earlier attempt reached Gmail but never "
                                 "confirmed, and it is not in your Sent folder. "
                                 "Check Gmail before retrying."})
                    failed += 1
                    continue
            can, err = rate_limiter.can_send_email()
            if not can:
                # Nothing from here on is attempted. Record every remaining id
                # so the results screen cannot report a batch as fully sent
                # while recipients were silently never contacted.
                for remaining_id in email_ids[i:]:
                    results.append({"email_id": remaining_id, "success": False,
                                    "error": err})
                    failed += 1
                break
            db.update_job(job_id, stage=f"Sending to {email.get('contact_email')}",
                          progress_current=i)
            resume_path = resumes.resolve_attachment_path(
                _resolved_attachment_id(email, attach, resume_override))
            # A follow-up belongs in the original conversation. The stored
            # gmail_message_id is an API id, not the RFC Message-ID header, so
            # read the real threading values back from Gmail (and backfill the
            # thread id — every pre-existing row was saved without one).
            if email.get("is_follow_up") and email.get("original_email_id"):
                original = db.get_email(email["original_email_id"])
                if original and original.get("gmail_message_id"):
                    ctx = email_sender.get_thread_context(original["gmail_message_id"])
                    email["reply_to_message_id"] = ctx["message_id"]
                    email["reply_to_thread_id"] = (
                        ctx["thread_id"] or original.get("gmail_thread_id"))
                    if ctx["thread_id"] and not original.get("gmail_thread_id"):
                        db.update_email(original["id"],
                                        {"gmail_thread_id": ctx["thread_id"]})
            if i > 0:
                import time as _t
                _t.sleep(email_sender.send_delay)
            # Write the intent *before* the network call. Between "Gmail accepted
            # the POST" and the update below the row is otherwise an ordinary
            # clean draft, so anything that kills the process in that window
            # (uvicorn --reload picking up a save, SIGINT, OOM, interpreter
            # shutdown freezing this daemon thread) loses the fact that the
            # recipient may already have the message — and the next batch sends a
            # second copy with no Sent-folder check. Cleared again on a
            # definitive verdict below.
            db.update_email(email_id, {"send_attempted_at": now_iso(),
                                       "send_attempt_error": "handed to Gmail — "
                                                             "awaiting confirmation"})
            result = email_sender.send_email(email, from_email, resume_path)
            results.append(result)
            if result.get("success"):
                sent += 1
                rate_limiter.record_email_sent()
                db.update_email(email_id, {
                    "status": "sent", "sent_at": now_iso(),
                    "gmail_message_id": result.get("gmail_message_id"),
                    "gmail_thread_id": result.get("gmail_thread_id"),
                    "send_attempted_at": None, "send_attempt_error": None,
                })
                db.update_contact(email["contact_id"], {"status": "sent"})
                db.log_event("email", email_id, "sent",
                             f"→ {email.get('contact_email')}")
                if recipient:
                    if email.get("is_follow_up"):
                        follow_up_recipients.add(recipient)
                    else:
                        first_contact_recipients.add(recipient)
            else:
                failed += 1
                if result.get("delivery_unknown"):
                    # Keep the row stamped (the write-ahead marker above already
                    # did) with the real error, so nothing downstream — the
                    # results screen, a later batch, the user — reads this as
                    # "safely retryable".
                    db.update_email(email_id, {
                        "send_attempted_at": now_iso(),
                        "send_attempt_error": result.get("error") or "",
                    })
                    db.log_event("email", email_id, "send_unconfirmed",
                                 result.get("error") or "")
                else:
                    # A verdict from Gmail (or a refusal before it saw anything):
                    # nothing was queued, so clear the write-ahead marker and let
                    # this stay an ordinary retryable draft.
                    db.update_email(email_id, {"send_attempted_at": None,
                                               "send_attempt_error": None})
                    db.log_event("email", email_id, "send_failed",
                                 result.get("error") or "")
            db.update_job(job_id, progress_current=i + 1)
        # Progress reflects what was actually accounted for: every id in
        # `results` was either sent or reported as failed. It reads 100% only
        # when nothing was left un-attempted.
        db.update_job(job_id, progress_current=min(len(results), len(email_ids)))
        db.finish_job(job_id, status="cancelled" if cancelled else "done",
                      result={"sent": sent, "failed": failed, "results": results})
    except Exception as e:
        db.finish_job(job_id, status="failed", error=str(e),
                      result={"sent": sent, "failed": failed, "results": results})
    except BaseException as e:
        # KeyboardInterrupt / SystemExit are not Exceptions, so they used to skip
        # finish_job and leave the job row 'running' — which 409s every later
        # send until the next restart reaps it.
        db.finish_job(job_id, status="failed",
                      error=str(e) or type(e).__name__,
                      result={"sent": sent, "failed": failed, "results": results})
        raise
    finally:
        _send_lock.release()


@app.post("/api/emails/send")
def send_emails(payload: SendRequest):
    # Plain `def` on purpose: FastAPI runs it in the threadpool, so a blocking
    # OAuth browser flow in authenticate() cannot freeze the event loop.
    candidates = [db.get_email(eid) or {} for eid in payload.email_ids]
    sendable_rows = [e for e in candidates
                     if e.get("status") in ("draft", "approved")
                     and not _already_delivered(e)]
    sendable = [e["id"] for e in sendable_rows]
    if not sendable:
        raise HTTPException(400, "None of the selected emails can be sent "
                                 "(only drafts and approved emails that have not "
                                 "already gone out are sendable)")
    # Never let the send options quietly falsify the text the user reviewed.
    if not payload.confirm_attachment_change:
        conflicts = [(e, why) for e in sendable_rows
                     if (why := _attachment_claim_conflict(
                         e, payload.attach_resume, payload.resume_id))]
        if conflicts:
            listed = ", ".join(
                f"{e.get('contact_email') or e.get('contact_name') or e['id']} "
                f"({why})" for e, why in conflicts[:5])
            raise HTTPException(
                400, f"{len(conflicts)} of these emails promise an attachment that "
                     f"these send options would change: {listed}. Rewrite the "
                     f"draft, fix the attachment, or confirm the change.")
    can, err = rate_limiter.can_send_email()
    if not can:
        raise HTTPException(429, err)
    if not os.path.isfile(email_sender.credentials_path):
        raise HTTPException(400, "Gmail is not set up: credentials.json is missing. "
                                 "See Settings for instructions.")
    if not _send_lock.acquire(blocking=False):
        raise HTTPException(409, "A send batch is already in progress")
    try:
        # Authenticate in the request so the OAuth browser flow (if needed)
        # happens now, not inside a background thread.
        email_sender.authenticate()
    except Exception as e:
        _send_lock.release()
        raise HTTPException(401, f"Gmail authentication failed: {e}")

    try:
        job = db.create_job("send", {"email_ids": sendable})
        db.update_job(job["id"], progress_total=len(sendable), stage="Sending")
        from_email = (payload.from_email or "").strip() or (db.get_profile().get("email") or "me")
        threading.Thread(
            target=_send_batch_job,
            args=(job["id"], sendable, payload.resume_id, payload.attach_resume,
                  from_email, bool(payload.confirm_resend)),
            daemon=True,
        ).start()
    except Exception:
        # If job setup or thread start fails, the batch thread (which owns the
        # release) never ran — release here or sending is stuck on 409 forever.
        _send_lock.release()
        raise
    return _parse_job(db.get_job(job["id"]))


@app.post("/api/emails/send/{job_id}/cancel")
def cancel_send(job_id: str):
    """Stop a send batch. Emails already sent stay sent; the rest are skipped
    before their Gmail call, so this is safe to hit mid-batch."""
    job = db.get_job(job_id)
    if not job or job["type"] != "send":
        raise HTTPException(404, "Send batch not found")
    if job["status"] != "running":
        raise HTTPException(409, "That batch already finished")
    db.update_job(job_id, status="cancelled")
    return {"success": True}


# ---------- reply tracking ----------

@app.post("/api/emails/check-replies")
def check_replies(recheck: bool = Query(False)):
    """Check sent emails for genuine replies.

    A default run covers everything not yet answered *and* every reply flag the
    current checker has never verified — the legacy checker counted bounces,
    auto-replies and our own messages, so those flags are precisely what needs
    re-examining. recheck=true additionally re-verifies already-verified rows.

    Plain `def` on purpose: the many sequential Gmail calls (and any OAuth flow)
    run in the threadpool, not the event loop.
    """
    where = "e.status='sent' AND e.gmail_message_id IS NOT NULL"
    if not recheck:
        where += " AND (e.has_response=0 OR e.response_verified_at IS NULL)"
    sent_emails = db.query(
        f"""SELECT e.*, ct.name AS contact_name, ct.email AS contact_email
           FROM emails e LEFT JOIN contacts ct ON e.contact_id=ct.id
           WHERE {where}
           ORDER BY e.sent_at DESC LIMIT 200"""
    )
    if not sent_emails:
        return {"success": True, "checked": 0, "new_replies": 0, "cleared": 0,
                "failed_checks": 0, "confirmed": 0, "unverified_remaining": 0}
    try:
        email_sender.authenticate()
    except Exception as e:
        raise HTTPException(401, f"Gmail authentication failed: {e}")
    checker = ResponseChecker(email_sender.service,
                              own_address=db.get_profile().get("email"))
    new_replies = cleared = failed_checks = confirmed = 0
    for email in sent_emails:
        has_reply, when = checker.check_response(
            email["gmail_message_id"], email.get("gmail_thread_id"))
        if has_reply is None:
            # Check failed (network error, rate limit, ...): unknown, not
            # 'no reply' — never clear an existing reply record for it.
            failed_checks += 1
            continue
        # Every definite verdict below stamps response_verified_at, which is what
        # separates "this checker saw the reply" from an inherited legacy flag.
        verified_at = now_iso()
        if has_reply and not email["has_response"]:
            new_replies += 1
            db.update_email(email["id"], {
                "has_response": True,
                "response_at": when.isoformat(timespec="seconds") if when else now_iso(),
                "response_verified_at": verified_at,
            })
            if email.get("contact_id"):
                db.update_contact(email["contact_id"], {"status": "replied"})
            db.log_event("email", email["id"], "replied",
                         email.get("contact_name") or email.get("contact_email") or "")
        elif has_reply and email["has_response"]:
            if not email.get("response_verified_at"):
                confirmed += 1
            updates = {"response_verified_at": verified_at}
            if when:
                updates["response_at"] = when.isoformat(timespec="seconds")
            db.update_email(email["id"], updates)
            if email.get("contact_id"):
                db.update_contact(email["contact_id"], {"status": "replied"})
        elif not has_reply and email["has_response"]:
            cleared += 1
            db.update_email(email["id"], {"has_response": False, "response_at": None,
                                          "response_verified_at": None})
            if email.get("contact_id"):
                contact = db.get_contact(email["contact_id"])
                if contact and contact.get("status") == "replied":
                    db.update_contact(email["contact_id"], {"status": "sent"})
    remaining = db.query_one(
        "SELECT COUNT(*) AS n FROM emails "
        "WHERE has_response=1 AND response_verified_at IS NULL")["n"]
    return {"success": True, "checked": len(sent_emails),
            "new_replies": new_replies, "cleared": cleared,
            "failed_checks": failed_checks, "confirmed": confirmed,
            "unverified_remaining": remaining}


# ---------- dashboard / tracking ----------

@app.get("/api/dashboard")
async def dashboard():
    counts = {
        "companies": db.query_one("SELECT COUNT(*) AS n FROM companies")["n"],
        "contacts": db.query_one("SELECT COUNT(*) AS n FROM contacts")["n"],
        "drafts": db.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE status IN ('draft','approved')")["n"],
        "sent": db.query_one("SELECT COUNT(*) AS n FROM emails WHERE status='sent'")["n"],
        # Only replies the current checker verified. The headline number is read
        # as fact ("Reply rate 89.8%" in green), and the flags inherited from the
        # legacy checker — which counted bounces, auto-replies and our own
        # messages — cannot support that claim. They are reported separately so
        # the user can re-verify them instead of being told a number.
        "replied": db.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE has_response=1 "
            "AND response_verified_at IS NOT NULL")["n"],
        "replied_unverified": db.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE has_response=1 "
            "AND response_verified_at IS NULL")["n"],
    }
    counts["reply_rate"] = round(counts["replied"] / counts["sent"] * 100, 1) if counts["sent"] else 0.0

    since = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
    sent_by_day = db.query(
        """SELECT substr(sent_at, 1, 10) AS day, COUNT(*) AS sent
           FROM emails WHERE status='sent' AND sent_at >= ?
           GROUP BY day ORDER BY day""", (since,))
    replies_by_day = db.query(
        """SELECT substr(response_at, 1, 10) AS day, COUNT(*) AS replies
           FROM emails WHERE has_response=1 AND response_verified_at IS NOT NULL
             AND response_at >= ?
           GROUP BY day ORDER BY day""", (since,))
    timeline = {}
    for i in range(30):
        day = (datetime.now() - timedelta(days=29 - i)).strftime("%Y-%m-%d")
        timeline[day] = {"day": day, "sent": 0, "replies": 0}
    for row in sent_by_day:
        if row["day"] in timeline:
            timeline[row["day"]]["sent"] = row["sent"]
    for row in replies_by_day:
        if row["day"] in timeline:
            timeline[row["day"]]["replies"] = row["replies"]

    by_type = db.query(
        # Trashed drafts are discarded work, not pending work — counting them
        # in the denominator made "122/154 sent" look like a backlog.
        """SELECT email_type, COUNT(*) AS total,
                  SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
                  SUM(CASE WHEN has_response=1 AND response_verified_at IS NOT NULL
                           THEN 1 ELSE 0 END) AS replied,
                  SUM(CASE WHEN has_response=1 AND response_verified_at IS NULL
                           THEN 1 ELSE 0 END) AS replied_unverified
           FROM emails WHERE status <> 'trashed'
           GROUP BY email_type ORDER BY total DESC""")

    follow_ups = db.get_follow_up_candidates()
    events = db.recent_events(limit=25)

    return {
        "counts": counts,
        "timeline": list(timeline.values()),
        "by_type": by_type,
        "follow_ups_due": len(follow_ups),
        "recent_events": events,
        "usage": rate_limiter.get_usage_stats(),
    }


@app.get("/api/usage")
async def usage():
    return rate_limiter.get_usage_stats()


# ---------- gmail ----------

@app.get("/api/gmail/status")
async def gmail_status():
    return {
        "connected": email_sender.is_connected(),
        "credentials_present": os.path.isfile(email_sender.credentials_path),
    }


@app.post("/api/gmail/disconnect")
async def gmail_disconnect():
    removed = email_sender.disconnect()
    return {"success": True, "token_removed": removed}
