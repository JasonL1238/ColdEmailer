"""
Cold Emailer API — discovery, database, resumes, generation, sending, tracking.
"""
import csv
import io
import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

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
                MAX_FOLLOW_UP_STEPS, MIN_FOLLOW_UP_GAP_DAYS,
                MAX_FOLLOW_UP_GAP_DAYS,
                repair_contact_email_kinds,
                repair_contact_reply_status, repair_delivered_email_status,
                repair_mismatched_company_sites,
                repair_offdomain_contact_warnings,
                repair_speculative_company_summaries,
                repair_unverified_legacy_replies)
from contact_ingest import (attach_candidate, contact_notes, find_existing,
                            owned_elsewhere_note, verified_channels)
from deep_research import DeepResearchService
from research_digest import consolidate, has_deep_research
from discovery import DiscoveryService, research_updates
from email_composer import (EmailComposer, EMAIL_TYPES, DEFAULT_TYPE,
                            TemplateUnavailable)
from email_sender import EmailSender
from enrichment import EnrichmentService
from generation import GenerationBusy, GenerationService
from models import (
    BulkIds, BulkStatus, CompanyCreate,
    ContactCreate, ContactUpdate, DeepResearchRequest, DiscoveryRequest,
    CadenceUpdate, SendWindowUpdate, CampaignUpdate, SuppressionCreate,
    EmailUpdate, EnrichRequest, GenerateRequest, ProfileUpdate, ResumeUpdate,
    SendRequest, LinkedInDraftRequest, PersonApproveRequest, PersonFindRequest,
)
from person_finder import PersonFinderService
from rate_limiter import RateLimiter
from resume_service import ResumeService
import analytics as analytics_module
import campaigns as campaigns_module
import pipeline
import suppression
import send_window
import thread_reader
from response_checker import ResponseChecker
from linkedin_outreach import draft_linkedin_message

try:
    from llm_client import get_cloud_llm_provider
except ImportError:
    get_cloud_llm_provider = lambda: None

app = FastAPI(title="Cold Emailer API", version="2.0")

# Include both localhost and 127.0.0.1 — browsers treat them as different
# origins, and start.sh serves the app on 127.0.0.1 to avoid IPv6 proxy issues.
_cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
).split(",")
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
deep_research = DeepResearchService(db, enrichment)
person_finder = PersonFinderService(db, enrichment)
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
repair_contact_email_kinds(db)
resumes.migrate_legacy_resumes(_PROJECT_ROOT)
_seed_profile_once()
_reap_orphaned_jobs()


def _find_or_create_company(name: str, *, source: str, **kwargs):
    """Find a company by name, else create it.

    Returns (company_row, created_company_id_or_None). The `before` snapshot is
    load-bearing: `create_company` legitimately returns an *existing* row on a
    domain or soft-key hit, so comparing ids is the only way to tell an insert
    from a lookup — and only a real insert may be rolled back.
    """
    existing = db.find_company_by_name(name)
    if existing:
        return existing, None
    before = {r["id"] for r in db.query("SELECT id FROM companies")}
    company = db.create_company(name, source=source, **kwargs)
    return company, (company["id"] if company["id"] not in before else None)


def _drop_empty_company(company_id: Optional[str]) -> None:
    """Roll back a company we created for a contact that never landed.

    Atomic: only deletes while it is still empty, so it cannot race a
    concurrent insert.
    """
    if company_id:
        db.execute(
            "DELETE FROM companies WHERE id=? AND NOT EXISTS "
            "(SELECT 1 FROM contacts WHERE company_id=?)",
            (company_id, company_id),
        )


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
        # Carried on the app-wide settings payload because every screen that
        # offers a follow-up has to know how many rungs there are before it can
        # say whether another one is owed.
        "follow_up_cadence": db.get_follow_up_cadence(),
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
    job = discovery.start(payload.query.strip(), payload.count, mode=payload.mode,
                          campaign_id=payload.campaign_id)
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


# ---------- deep research ----------

@app.post("/api/deep-research")
async def start_deep_research(payload: DeepResearchRequest):
    if not payload.company_name and not payload.company_id:
        raise HTTPException(400, "Provide company_name or company_id")
    can, err = rate_limiter.can_research_company()
    if not can:
        raise HTTPException(429, err)
    try:
        job = deep_research.start(
            company_name=payload.company_name,
            company_id=payload.company_id,
            url=payload.url,
            contact_criteria=payload.contact_criteria or "",
            min_contacts=payload.min_contacts,
            target_criteria_matches=payload.target_criteria_matches,
            continue_mode=payload.continue_mode,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    rate_limiter.record_company_research()
    return _parse_job(db.get_job(job["id"]))


@app.get("/api/deep-research")
async def list_deep_research_jobs():
    return [_parse_job(j) for j in deep_research.list_jobs(limit=20)]


# Must stay above /api/deep-research/{job_id} — a literal path declared after
# a path parameter would be swallowed by it.
@app.get("/api/deep-research/consolidated")
async def list_consolidated_research(search: Optional[str] = None,
                                     include_all: bool = False):
    """Every company that has been deep-dived, with all of its runs merged.

    Two dives on one company — even with different criteria — come back as a
    single section rather than two unrelated job rows.

    `include_all` also returns companies that have never been dived (with
    run_count 0), so the UI can offer one searchable list of everything
    instead of making the user go elsewhere to find a company.
    """
    out = []
    for company in db.list_companies(search=search):
        researched = has_deep_research(company)
        if not researched and not include_all:
            continue
        contacts = db.list_contacts(company_id=company["id"])
        out.append(consolidate(company, contacts))
    # Deep-dived first (most recent at the top), then everything else by name.
    out.sort(key=lambda c: (c["company"].get("name") or "").lower())
    out.sort(
        key=lambda c: (bool(c["run_count"]), c.get("last_researched_at") or ""),
        reverse=True,
    )
    return out


@app.get("/api/deep-research/{job_id}")
async def get_deep_research_job(job_id: str):
    job = deep_research.get_job(job_id)
    if not job:
        raise HTTPException(404, "Deep research run not found")
    out = _parse_job(job)
    company_id = None
    if isinstance(out.get("result"), dict):
        company_id = out["result"].get("company_id")
    if not company_id and isinstance(out.get("payload"), dict):
        company_id = out["payload"].get("company_id")
    if company_id:
        company = db.get_company(company_id)
        if company:
            company["contacts"] = db.list_contacts(company_id=company_id)
            out["company"] = company
    return out


@app.post("/api/deep-research/{job_id}/cancel")
async def cancel_deep_research(job_id: str):
    job = deep_research.get_job(job_id)
    if not job:
        raise HTTPException(404, "Deep research run not found")
    if not deep_research.cancel(job_id):
        raise HTTPException(409, "That deep research already finished")
    return {"success": True}


# ---------- person finder ----------

@app.post("/api/person-finder")
async def start_person_finder(payload: PersonFindRequest):
    can, err = rate_limiter.can_research_company()
    if not can:
        raise HTTPException(429, err)
    try:
        job = person_finder.start(
            name=payload.name,
            company_name=payload.company_name,
            company_url=payload.company_url,
            school=payload.school,
            school_dates=payload.school_dates,
            past_companies=payload.past_companies,
            role_hint=payload.role_hint,
            location=payload.location,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    rate_limiter.record_company_research()
    return _parse_job(db.get_job(job["id"]))


@app.get("/api/person-finder")
async def list_person_finder_jobs():
    return [_parse_job(j) for j in person_finder.list_jobs(limit=20)]


@app.get("/api/person-finder/{job_id}")
async def get_person_finder_job(job_id: str):
    job = person_finder.get_job(job_id)
    if not job:
        raise HTTPException(404, "Person search not found")
    return _parse_job(job)


@app.post("/api/person-finder/{job_id}/cancel")
async def cancel_person_finder(job_id: str):
    job = person_finder.get_job(job_id)
    if not job:
        raise HTTPException(404, "Person search not found")
    if not person_finder.cancel(job_id):
        raise HTTPException(409, "That search already finished")
    return {"success": True}


@app.post("/api/person-finder/{job_id}/approve")
async def approve_person_candidate(job_id: str, payload: PersonApproveRequest):
    """The one path from staged person-finder candidates into `contacts`.

    Crosses the standard contact boundary: annotate → verified_channels →
    attach_candidate. A user-confirmed address that failed the name match is
    applied afterwards with the same honest reclassification the contact-edit
    route uses — email_verified stays 0, nothing guessed becomes sendable.
    """
    from contact_verify import annotate_contact, is_generic_inbox, verify_email

    job = person_finder.get_job(job_id)
    if not job:
        raise HTTPException(404, "Person search not found")
    if job.get("status") == "running":
        raise HTTPException(409, "That search is still running")
    if job.get("status") != "done":
        raise HTTPException(409, "That search did not finish — run it again")
    parsed = _parse_job(job)
    result = parsed.get("result") or {}
    candidates = result.get("candidates") or []
    cand = next(
        (c for c in candidates if c.get("id") == payload.candidate_id), None)
    if not cand:
        raise HTTPException(400, "Unknown candidate for this search")
    if cand.get("approved_contact_id"):
        row = db.get_contact(cand["approved_contact_id"])
        if row:
            return {"contact": row, "conflicts": [], "already_existed": True}

    selected = None
    selected_email = (payload.email or "").strip().lower()
    if selected_email:
        selected = next(
            (e for e in cand.get("emails") or []
             if (e.get("email") or "").lower() == selected_email), None)
        if not selected:
            raise HTTPException(
                400, "That address is not one of this candidate's "
                     "discovered emails")
        if selected.get("origin") == "guessed":
            # A guess becomes savable only when something outside this app
            # backed it up AND the user takes responsibility for identity.
            # There are two ways to earn the first half, and neither alone
            # settles ownership — john.smith@gs.com may accept mail and belong
            # to a different John Smith:
            #   - a mail server confirmed the mailbox exists, or
            #   - a public corpus shows the address in use under this person's
            #     name (a signed commit, a profile, a published key).
            # The second is the stronger evidence, because it carries a name
            # and a mail server never does. Anything the finder can produce
            # with neither still lands on the refusal below.
            corroboration = selected.get("corroboration") or {}
            mailbox_ok = selected.get("smtp_status") == "deliverable"
            corroborated = corroboration.get("name_match") is True
            confirmed = ((mailbox_ok or corroborated)
                         and selected.get("email_person_match"))
            if not confirmed:
                raise HTTPException(
                    400, "Pattern guesses can't be saved as a sendable "
                         "address — they're recorded as a note instead.")
            if not payload.confirm_email_ownership:
                evidence = ("a public source shows this address in use under "
                            "this person's name"
                            if corroborated else
                            "a mail server confirmed this mailbox exists")
                raise HTTPException(
                    400, f"confirm_email_ownership: {evidence}, but that is "
                         "not proof it belongs to this person. Confirm that, "
                         "then approve again.")
        if is_generic_inbox(selected_email):
            raise HTTPException(
                400, "That looks like a company inbox, not this person")
        if suppression.match(selected_email, db.list_suppressions()):
            raise HTTPException(
                400, "That address is on your do-not-contact list. Remove it "
                     "in Settings first if you meant to add this person.")

    # Company find-or-create happens here, at approval — abandoned hunts must
    # not litter the companies table.
    company_info = result.get("company") or {}
    company = None
    created_company_id = None
    if payload.company_id:
        company = db.get_company(payload.company_id)
        if not company:
            raise HTTPException(404, "That company no longer exists")
    else:
        company_name = (
            payload.company_name or company_info.get("name") or "").strip()
        if company_name:
            company, created_company_id = _find_or_create_company(
                company_name,
                source="person_finder",
                url=company_info.get("url"),
                domain=company_info.get("domain"))

    best_evidence = (cand.get("evidence") or [{}])[0]
    # A confirmed guess is deliberately kept OUT of the boundary candidate.
    # Its local part is built from the person's name, so annotate_contact
    # would compute email_person_match + MX and hand back email_verified=True
    # — laundering an inference into a verification. It goes in afterwards
    # through the user-assertion path, which records honestly.
    is_guess = bool(selected and selected.get("origin") == "guessed")
    boundary = {
        "name": cand.get("name") or "",
        "email": "" if is_guess else (selected["email"] if selected else ""),
        "linkedin_url": (cand.get("linkedin_url") or None)
        if payload.include_linkedin else None,
        "role": cand.get("role"),
        "source_url": ((selected or {}).get("source_url")
                       or best_evidence.get("source_url")
                       or cand.get("source_url")),
        "evidence": (best_evidence.get("snippet") or "")[:240],
        "name_from_email": False,
        "on_domain": bool(selected
                          and selected.get("domain_kind") == "company"),
        "seniority_rank": 12,
        "affinity": [],
    }
    annotated = annotate_contact(boundary, check_mx=bool(selected))
    email0, linkedin0 = verified_channels(annotated)

    confirmed_email = None
    if is_guess:
        # Already gated above on smtp_status == deliverable + ownership.
        confirmed_email = selected["email"]
    elif selected and not email0:
        # verified_channels stripped it: the local part doesn't contain the
        # person's name. Only the user can assert ownership of that mailbox.
        if not payload.confirm_email_ownership:
            _drop_empty_company(created_company_id)
            raise HTTPException(
                400, "confirm_email_ownership: that address doesn't contain "
                     "this person's name. Confirm you verified it belongs to "
                     "them, then approve again.")
        confirmed_email = selected["email"]

    if not email0 and not linkedin0 and not confirmed_email:
        _drop_empty_company(created_company_id)
        raise HTTPException(
            400, "Nothing verifiable to save — select an address or include "
                 "a verified LinkedIn profile.")

    existing = find_existing(db, email0 or confirmed_email or "", linkedin0)
    if existing:
        _drop_empty_company(created_company_id)
        if (company and existing.get("company_id")
                and existing["company_id"] != company["id"]):
            raise HTTPException(409, owned_elsewhere_note(
                db, existing["company_id"],
                email0 or confirmed_email or linkedin0,
                company.get("name") or "this company"))
        _mark_candidate_approved(parsed, payload.candidate_id, existing["id"])
        return {"contact": existing, "conflicts": [], "already_existed": True}

    notes_candidate = dict(annotated)
    guessed = next(
        (e for e in cand.get("emails") or [] if e.get("origin") == "guessed"),
        None)
    if guessed:
        notes_candidate["email_guess"] = guessed["email"]
    if selected and selected.get("origin") == "hunter":
        notes_candidate["email_source"] = "hunter"
    notes_parts = [contact_notes(notes_candidate) or ""]
    signals = [s.get("signal") for s in cand.get("matched_signals") or []]
    if signals:
        notes_parts.append(
            "Found via person search: matched "
            + ", ".join(str(s) for s in signals if s) + ".")
    if selected and selected.get("domain_kind") == "personal":
        host = selected["email"].split("@", 1)[-1]
        where = selected.get("source_url")
        notes_parts.append(
            f"Personal address ({host})"
            + (f" found at {where}." if where else "."))
    for channel in cand.get("channels") or []:
        if channel.get("url"):
            notes_parts.append(
                f"{(channel.get('kind') or 'link').capitalize()}: "
                f"{channel['url']}")
    notes = " ".join(p for p in notes_parts if p).strip() or None

    conflicts: List[str] = []
    if company:
        attached = attach_candidate(
            db, company=company, candidate=annotated, email=email0,
            linkedin_url=linkedin0, source="person_finder", notes=notes)
        if not attached.contact:
            _drop_empty_company(created_company_id)
            raise HTTPException(
                409, "; ".join(attached.conflicts)
                or "Could not attach this contact")
        contact = attached.contact
        conflicts = attached.conflicts
    else:
        # No company known — save unattached, like the manual path allows.
        # Validation already happened above (annotate + verified_channels).
        contact = db.create_contact(
            company_id=None,
            name=annotated.get("name") or "",
            email=email0,
            linkedin_url=linkedin0 or None,
            role=annotated.get("role"),
            source="person_finder",
            status="new",
            notes=notes,
            source_url=annotated.get("source_url"),
            evidence=annotated.get("evidence"),
            seniority_rank=annotated.get("seniority_rank", 20),
            email_kind=annotated.get("email_kind") or "unknown",
            email_verified=bool(annotated.get("email_verified")),
            linkedin_verified=bool(annotated.get("linkedin_verified")),
            person_verified=bool(annotated.get("person_verified")),
        )
        if not contact.get("_inserted", True):
            raise HTTPException(
                409, "That contact already exists "
                     "(possible concurrent create)")

    if confirmed_email:
        clash = db.find_contact_by_email(confirmed_email)
        if clash and clash["id"] != contact["id"]:
            conflicts.append(
                f"{confirmed_email} already belongs to another contact — "
                "not saved")
        else:
            # Same semantics as editing a contact's address by hand: honest
            # reclassification, never a verified flag the evidence can't back.
            verdict = verify_email(
                confirmed_email, contact.get("name") or cand.get("name"),
                check_mx=False)
            guess_corroboration = (selected or {}).get("corroboration") or {}
            guess_mailbox_ok = (
                (selected or {}).get("smtp_status") == "deliverable")
            if is_guess and guess_mailbox_ok:
                note_add = (
                    f"Pattern-inferred address. A mail server confirmed a "
                    f"mailbox exists here and rejected a random address on "
                    f"the same domain, so the mailbox is real — that does not "
                    f"prove it belongs to {cand.get('name')}. You confirmed "
                    f"ownership.")
            elif is_guess:
                # Reached only via the corroboration arm of the approval gate,
                # so the note must describe that evidence rather than a mailbox
                # probe that never ran.
                where = ", ".join(guess_corroboration.get("sources") or [])
                note_add = (
                    f"Pattern-inferred address, corroborated by "
                    f"{where or 'a public source'}: the address is in public "
                    f"use under this person's name. That shows the address is "
                    f"real, not that {cand.get('name')} still reads it. You "
                    f"confirmed ownership.")
            else:
                note_add = (
                    f"You confirmed this address belongs to "
                    f"{cand.get('name')} (found at "
                    f"{(selected or {}).get('source_url') or 'unknown source'}).")
            db.update_contact(contact["id"], {
                "email": confirmed_email,
                "email_kind": verdict["email_kind"],
                "email_verified": 0,
                "person_verified": 0,
                "notes": f"{contact.get('notes') or ''} {note_add}".strip(),
            })
            db.log_event("contact", contact["id"], "email_user_confirmed",
                         confirmed_email)
            if is_guess and guess_mailbox_ok:
                db.log_event("contact", contact["id"], "mailbox_confirmed",
                             confirmed_email)
            elif is_guess:
                db.log_event("contact", contact["id"], "address_corroborated",
                             confirmed_email)
            contact = db.get_contact(contact["id"])

    _mark_candidate_approved(parsed, payload.candidate_id, contact["id"])
    return {"contact": contact, "conflicts": conflicts,
            "already_existed": False}


def _mark_candidate_approved(parsed_job: dict, candidate_id: str,
                             contact_id: str):
    """Write approved_contact_id back into the staged result so a revisit
    renders the saved state instead of offering a second approval."""
    result = parsed_job.get("result") or {}
    for cand in result.get("candidates") or []:
        if cand.get("id") == candidate_id:
            cand["approved_contact_id"] = contact_id
    db.update_job(parsed_job["id"], result=result)


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
        # COALESCE, like every other recipient lookup: without it this drawer
        # names a different recipient for the same sent email than the Emails
        # screen does, because it re-resolves a mutable join.
        """SELECT e.*, ct.name AS contact_name,
                  COALESCE(e.recipient_email, ct.email) AS contact_email
           FROM emails e JOIN contacts ct ON e.contact_id = ct.id
           WHERE ct.company_id=? ORDER BY e.created_at DESC""",
        (company_id,),
    )
    return company


def _merge_scraped_into_existing(existing_contact, candidate, *, addr,
                                 linkedin_url, company, company_id) -> List[str]:
    """Fold a freshly scraped candidate into a contact this company already has.

    Returns the conflict notes to surface on the company. Deliberately NOT
    shared with deep_research._persist_contacts: that path fills a blank
    address with only a duplicate check, and has neither the sent-history
    gate nor the generic->personal upgrade this one applies.
    """
    conflicts: List[str] = []
    richer = {}
    if not existing_contact.get("name") and candidate.get("name"):
        richer["name"] = candidate["name"]
    if not existing_contact.get("role") and candidate.get("role"):
        richer["role"] = candidate["role"]
    if not existing_contact.get("linkedin_url") and linkedin_url:
        clash = db.find_contact_by_linkedin(linkedin_url)
        if clash and clash.get("id") != existing_contact.get("id"):
            other = (
                db.get_company(clash.get("company_id"))
                if clash.get("company_id") else None
            )
            other_name = (other or {}).get("name") or "another company"
            detail = (
                f"{linkedin_url} already belongs to {other_name} — "
                f"not attached to {company['name']}"
            )
            conflicts.append(detail)
            db.log_event(
                "company", company_id, "contact_conflict", detail)
        else:
            richer["linkedin_url"] = linkedin_url
            richer["linkedin_verified"] = int(
                bool(candidate.get("linkedin_verified")))
    new_notes = contact_notes(candidate)
    if new_notes and not existing_contact.get("notes"):
        richer["notes"] = new_notes
    existing_email = (existing_contact.get("email") or "").strip()
    # Mailbox-bound flags only apply to the stored address.
    if addr:
        same_mailbox = existing_email.lower() == addr.lower()
        if not existing_email:
            # Filling a blank is normally free, but the blank
            # can be one the user just cleared on a contact
            # with sent history — after which this would put a
            # different person's address on that history.
            if not _contact_has_been_emailed(existing_contact["id"]):
                richer["email"] = addr
                same_mailbox = True
        elif (
            not same_mailbox
            and existing_contact.get("email_kind") == "generic"
            and candidate.get("email_kind") == "personal"
            and (candidate.get("email_verified")
                 or candidate.get("email_person_match"))
            and not _contact_has_been_emailed(existing_contact["id"])
        ):
            # Upgrade company inbox → verified person mailbox.
            # Only while nothing has been sent yet: a follow-up
            # is composed as "following up on my note", and
            # moving the address underneath a sent history
            # points that at someone who never got the note.
            richer["email"] = addr
            same_mailbox = True
        if same_mailbox:
            for flag in ("email_kind", "email_verified"):
                if candidate.get(flag) is not None:
                    val = candidate.get(flag)
                    richer[flag] = (
                        int(bool(val)) if flag != "email_kind"
                        else val
                    )
    if (
        linkedin_url
        and candidate.get("linkedin_verified") is not None
        and richer.get("linkedin_url")
    ):
        richer["linkedin_verified"] = int(
            bool(candidate.get("linkedin_verified")))
    if candidate.get("person_verified") is not None:
        # Only promote when this update actually keeps verified
        # person evidence on the row (email flags or attached LI).
        if richer.get("email_verified") or richer.get("linkedin_url"):
            richer["person_verified"] = int(
                bool(candidate.get("person_verified")))
    if richer:
        db.update_contact(existing_contact["id"], richer)
    return conflicts


def _enrich_company_async(company_id: str, mode: str = "full"):
    company = db.get_company(company_id)
    if not company:
        return
    db.update_company(company_id, {"scrape_status": "scraping"})
    try:
        enriched = enrichment.enrich(
            company["name"], company.get("url"),
            preferred_school=db.get_profile().get("school"),
            preferred_affiliations=db.get_profile().get("affiliations"),
            mode=mode or "full",
        )
        updates = research_updates(enriched)
        db.update_company(company_id, updates)
        # Add evidence-backed contacts, preferring same-school senior leaders.
        from enrichment import select_outreach_contacts
        domain = (db.get_company(company_id) or {}).get("domain")
        contact_conflicts = []
        contacts_added = 0
        for candidate in select_outreach_contacts(
                enriched.get("contacts") or [],
                enriched.get("emails") or [],
                enriched.get("mail_domain") or domain,
                limit=3, person_only=True):
            addr, linkedin_url = verified_channels(candidate)
            if not addr and not linkedin_url:
                continue
            existing_contact = find_existing(db, addr, linkedin_url)
            if existing_contact:
                if existing_contact.get("company_id") == company_id:
                    contact_conflicts.extend(_merge_scraped_into_existing(
                        existing_contact, candidate, addr=addr,
                        linkedin_url=linkedin_url, company=company,
                        company_id=company_id))
                else:
                    detail = owned_elsewhere_note(
                        db, existing_contact.get("company_id"),
                        addr or linkedin_url, company["name"])
                    contact_conflicts.append(detail)
                    db.log_event(
                        "company", company_id, "contact_conflict", detail)
                continue
            attached = attach_candidate(
                db, company=company, candidate=candidate, email=addr,
                linkedin_url=linkedin_url, source="discovery")
            contact_conflicts.extend(attached.conflicts)
            if attached.is_new_outreach:
                contacts_added += 1
        db.update_company(company_id, {"scrape_warnings": contact_conflicts})
        # If every outreach contact conflicted away, downgrade scraped.
        status = updates["scrape_status"]
        if status == "scraped":
            rows = db.query(
                "SELECT email, email_kind, email_verified, linkedin_url, "
                "linkedin_verified, name FROM contacts WHERE company_id=?",
                (company_id,),
            )
            has_outreach = any(
                (
                    (r.get("email") or "").strip()
                    and (r.get("email_kind") or "") == "personal"
                    and r.get("email_verified")
                ) or (
                    (r.get("linkedin_url") or "").strip()
                    and r.get("linkedin_verified")
                    and (r.get("name") or "").strip()
                )
                for r in rows
            )
            if not has_outreach:
                status = "no_emails_found"
                updates["scrape_status"] = status
                db.update_company(company_id, {"scrape_status": status})
        # Log what actually happened — an unconditional "researched" here made
        # the activity feed claim success for wrong-site and failed scrapes.
        if status == "scraped":
            extra = (f" ({len(contact_conflicts)} contact conflict(s))"
                     if contact_conflicts else "")
            db.log_event("company", company_id, "enriched",
                         f"{company['name']}{extra}")
        elif status == "wrong_site":
            db.log_event("company", company_id, "research_failed",
                         f"{company['name']}: {enriched.get('mismatch') or 'wrong site'}")
        else:
            db.log_event("company", company_id, "research_failed",
                         f"{company['name']}: no usable information found")
        return {
            "contacts_added": contacts_added,
            "contact_conflicts": contact_conflicts,
        }
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
async def enrich_company(company_id: str, payload: Optional[EnrichRequest] = None):
    company = db.get_company(company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    can, err = rate_limiter.can_research_company()
    if not can:
        raise HTTPException(429, err)
    mode = (payload.mode if payload else "full")
    rate_limiter.record_company_research()
    threading.Thread(
        target=_enrich_company_async, args=(company_id, mode),
        daemon=True).start()
    return {"success": True, "message": "Research started", "mode": mode}


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
    from contact_ingest import sanitize_inbound_contact
    email = (payload.email or "").strip()
    cleaned, err = sanitize_inbound_contact(
        name=payload.name, email=email, linkedin_url=payload.linkedin_url,
        role=payload.role, require_person_email=True, check_mx=False,
        suppressions=db.list_suppressions())
    if err:
        messages = {
            "company_or_role_inbox":
                "That looks like a company inbox (hello@, info@, careers@…). "
                "Add a person's address or a matching LinkedIn profile instead.",
            "email_does_not_match_name":
                "The email local-part does not match this person's name.",
            "linkedin_does_not_match_name":
                "That LinkedIn URL does not match this person's name.",
            "linkedin_needs_name":
                "A LinkedIn profile needs the person's name so we can verify it.",
            "name_required_for_email":
                "A personal email needs the person's name so we can verify it.",
            "invalid_email":
                "That email address is not valid.",
            "no_usable_contact_method":
                "Provide a personal email and/or a LinkedIn profile URL.",
            "suppressed":
                "That address is on your do-not-contact list. Remove it in "
                "Settings first if you meant to add this person.",
            "invalid_linkedin_url":
                "Use a full https://www.linkedin.com/in/... profile URL.",
        }
        raise HTTPException(400, messages.get(err, err))
    email = cleaned["email"]
    linkedin_url = cleaned["linkedin_url"]
    if email and db.find_contact_by_email(email):
        raise HTTPException(409, f"A contact with email {email} already exists")
    if linkedin_url and db.find_contact_by_linkedin(linkedin_url):
        raise HTTPException(409, "That LinkedIn profile is already a contact")
    company_id = payload.company_id
    created_company_id = None
    if company_id and not db.get_company(company_id):
        raise HTTPException(404, "That company no longer exists")
    if not company_id and payload.company_name and payload.company_name.strip():
        company, created_company_id = _find_or_create_company(
            payload.company_name, source="manual")
        company_id = company["id"]
    contact = db.create_contact(
        name=cleaned["name"], email=email, role=cleaned["role"],
        linkedin_url=linkedin_url,
        company_id=company_id, notes=payload.notes, source="manual",
        email_kind=cleaned["email_kind"],
        email_verified=cleaned["email_verified"],
        linkedin_verified=cleaned["linkedin_verified"],
        person_verified=cleaned["person_verified"],
    )
    if not contact.get("_inserted", True):
        _drop_empty_company(created_company_id)
        raise HTTPException(
            409, "That contact already exists (possible concurrent create)")
    row = db.get_contact(contact["id"])
    if cleaned.get("ingest_warning"):
        row = dict(row or {})
        row["ingest_warning"] = cleaned["ingest_warning"]
    return row


@app.put("/api/contacts/{contact_id}")
async def update_contact(contact_id: str, payload: ContactUpdate,
                         force: bool = False):
    existing = db.get_contact(contact_id)
    if not existing:
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
    new_linkedin = (updates.get("linkedin_url") or "").strip()
    if new_linkedin:
        clash = db.find_contact_by_linkedin(new_linkedin)
        if clash and clash["id"] != contact_id:
            raise HTTPException(409, "Another contact already uses that LinkedIn profile")
    old_email = (existing.get("email") or "").strip()
    address_changed = "email" in updates and new_email.lower() != old_email.lower()

    # A bounce belongs to an address, not to a person. Moving the contact to a
    # different mailbox must clear it, or the send error keeps naming an
    # address that has never bounced and telling the user to do the thing they
    # just did.
    if updates.pop("clear_bounce", None) or address_changed:
        updates["bounced_at"] = None
        updates["bounce_detail"] = None

    if address_changed and _contact_has_been_emailed(contact_id) and not force:
        # Nothing here freezes a *future* email. `emails.recipient_email`
        # preserves who past ones went to, but a follow-up is composed fresh
        # against the live contact — so moving the address after a send makes
        # "I wanted to follow up on my note from <date>" arrive at someone who
        # never got the note, threaded into a conversation they were not part
        # of. Same rule as deleting a contact with sent history.
        raise HTTPException(
            409,
            f"{existing.get('name') or old_email} has already been emailed. "
            f"Changing the address now would aim any follow-up at someone who "
            f"never received the original. Re-send with ?force=true to do it "
            f"anyway, or add the new address as a separate contact.")

    # Re-classify whenever either half of the (name, address) pair moves.
    # Keying off the address alone left the mirror-image staleness: renaming
    # the contact on jane.doe@ to "Bob Smith" kept email_kind 'personal' and
    # email_verified 1, which then assert that jane.doe@ is Bob's mailbox and
    # keep the mismatch warning silent.
    new_name = updates.get("name", existing.get("name"))
    if address_changed or ("name" in updates
                           and new_name != existing.get("name")
                           and (new_email or old_email)):
        from contact_verify import verify_email
        addr = new_email if "email" in updates else old_email
        # name_from_email stays False here on purpose. Unlike the legacy-row
        # repair, this name arrived from the caller alongside the address, so
        # comparing them is real evidence rather than circular. Forcing the
        # conservative branch made verify_email structurally unable to return
        # 'personal', so every edit downgraded a correct row and the drafts
        # list cried "address doesn't match" at perfectly matching addresses.
        verdict = verify_email(addr, new_name, check_mx=False)
        updates["email_kind"] = verdict["email_kind"] if addr else "none"
        # Cleared on a rename too, not only on an address change. Both flags
        # assert a *pairing* — "this mailbox was confirmed to be this person".
        # Leaving them after a rename produced a row that simultaneously reads
        # "address does not match this person" and "verified as this person",
        # and the UI shows the green Person/Verified chip off the second one.
        updates["email_verified"] = 0
        updates["person_verified"] = 0
    elif "email" in updates:
        # Same address echoed back by the client. Rewriting the classification
        # from an unchanged value can only lose information.
        updates.pop("email", None)

    db.update_contact(contact_id, updates)
    if address_changed:
        # A queued message resolves its recipient at send time, so moving the
        # address re-aims mail that is already approved and waiting — at a
        # different human, days later, with nobody watching. `force` does not
        # protect this: _contact_has_been_emailed only counts rows that have
        # been sent or attempted, and a queued row is neither.
        requeued = db.query(
            "SELECT id FROM emails WHERE contact_id=? AND scheduled_at IS NOT NULL",
            (contact_id,))
        for row in requeued:
            db.update_email(row["id"], {"scheduled_at": None,
                                        "scheduled_by_job": None})
            db.log_event("email", row["id"], "send_unscheduled",
                         "recipient address changed while queued")
    if updates.get("bounced_at", "keep") is None:
        # The email-level flags too, not just the contact's. Nothing else in
        # the app ever writes NULL to emails.bounced_at, so a row that bounced
        # before recipient_email existed — and therefore cannot say which
        # address it was about — went on refusing every future send and every
        # follow-up, repeating an instruction the user had already followed.
        db.execute(
            "UPDATE emails SET bounced_at=NULL WHERE contact_id=? "
            "AND (recipient_email IS NULL OR lower(recipient_email)=?)",
            (contact_id, old_email.lower()))
    return db.get_contact(contact_id)


@app.post("/api/contacts/{contact_id}/linkedin-draft")
async def create_linkedin_draft(
        contact_id: str, payload: LinkedInDraftRequest):
    """Draft only. The user reviews and sends in LinkedIn themselves."""
    contact = db.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    if not contact.get("linkedin_url"):
        raise HTTPException(
            400, "No verified LinkedIn profile URL is stored for this contact")
    company = (
        db.get_company(contact["company_id"])
        if contact.get("company_id") else None
    )
    message = draft_linkedin_message(
        contact, company, db.get_profile(), payload.custom_instructions)
    return {
        "message": message,
        "linkedin_url": contact["linkedin_url"],
        "manual_send_required": True,
    }


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
    """CSV import. Requires name/company plus email and/or linkedin_url."""
    try:
        raw = await file.read()
        if len(raw) > MAX_CSV_BYTES:
            raise ValueError(f"CSV is too large (max {MAX_CSV_BYTES // (1024 * 1024)} MB)")
        text = _decode_csv(raw)
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        cols = {c.strip().lower(): c for c in reader.fieldnames}
        linkedin_export = {
            "first name", "last name", "url", "company", "position",
        }.issubset(cols)
        missing = {"name", "company"} - set(cols)
        if missing and not linkedin_export:
            raise ValueError(
                f"Missing required column(s): {', '.join(sorted(missing))}. "
                "Use a Reach CSV (name, company, email/linkedin_url) or a "
                "LinkedIn Connections export")
        if not linkedin_export and not ({"email", "linkedin_url"} & set(cols)):
            raise ValueError(
                "Header must include at least one contact method: email or linkedin_url")
        # Report why rows were dropped separately — "duplicates skipped" is a
        # misleading thing to tell someone whose addresses were malformed.
        added = duplicates = invalid = warnings = 0
        invalid_samples = []
        from contact_ingest import sanitize_inbound_contact
        # Read once for the file, not per row: a 2000-row CSV would otherwise
        # re-read the list 2000 times.
        suppressions = db.list_suppressions()
        for row in reader:
            def get(key):
                return (row.get(cols[key]) or "").strip() if key in cols else ""

            if linkedin_export:
                name = " ".join(
                    part for part in (get("first name"), get("last name"))
                    if part
                )
                email = get("email address")
                linkedin_url = get("url")
                company_name = get("company")
                role = get("position")
                notes = "Imported from your LinkedIn Connections export."
                affinity = "Direct LinkedIn connection"
                source = "linkedin_export"
            else:
                name = get("name")
                email = get("email")
                linkedin_url = get("linkedin_url")
                company_name = get("company")
                role = get("role")
                notes = get("notes") or None
                affinity = get("affinity") or None
                source = "csv"
            cleaned, err = sanitize_inbound_contact(
                name=name, email=email, linkedin_url=linkedin_url, role=role,
                require_person_email=True, check_mx=False,
                suppressions=suppressions)
            if err:
                invalid += 1
                if len(invalid_samples) < 5:
                    label = email or linkedin_url or name or "row"
                    invalid_samples.append(f"{label} ({err})")
                continue
            # Only count warnings for rows we actually store.
            pending_warning = bool(cleaned.get("ingest_warning"))
            email = cleaned["email"]
            linkedin_url = cleaned["linkedin_url"]
            name = cleaned["name"]
            role = cleaned["role"]
            if ((email and db.find_contact_by_email(email))
                    or (linkedin_url and db.find_contact_by_linkedin(linkedin_url))):
                duplicates += 1
                continue
            company_id = None
            created_company_id = None
            if company_name:
                company, created_company_id = _find_or_create_company(
                    company_name, source="csv")
                company_id = company["id"]
            created = db.create_contact(
                name=name, email=email, company_id=company_id,
                linkedin_url=linkedin_url,
                role=role or None, notes=notes, affinity=affinity, source=source,
                email_kind=cleaned["email_kind"],
                email_verified=cleaned["email_verified"],
                linkedin_verified=cleaned["linkedin_verified"],
                person_verified=cleaned["person_verified"],
            )
            if not created.get("_inserted", True):
                _drop_empty_company(created_company_id)
                duplicates += 1
                continue
            if pending_warning:
                warnings += 1
            added += 1
        db.log_event("contact", None, "imported", f"{added} contacts from CSV")
        return {"success": True, "added": added,
                "duplicates": duplicates, "invalid": invalid,
                "warnings": warnings,
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
    contacts = db.list_contacts()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "name", "email", "linkedin_url", "company", "role", "affinity",
        "source_url", "status", "source", "created_at",
    ])
    for c in contacts:
        writer.writerow([_csv_safe(c.get(k)) for k in
                         ("name", "email", "linkedin_url", "company_name", "role",
                          "affinity", "source_url", "status", "source",
                          "created_at")])
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
async def get_resume_file(resume_id: str, download: bool = Query(False)):
    row = resumes.get(resume_id)
    if not row or not os.path.isfile(row["path"]):
        raise HTTPException(404, "Resume file not found")
    if download:
        return FileResponse(row["path"], media_type="application/pdf",
                            filename=row["filename"])
    # No filename= here: Starlette treats it as an attachment. Inline lets the
    # browser's PDF viewer render it in the app or a normal tab.
    return FileResponse(
        row["path"], media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'},
    )


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


def _domain_accepts_mail(recipient: str, cache: dict) -> bool:
    """False only when DNS says the domain has no mail server at all.

    Deliberately optimistic. `domain_has_mx` returns None when it cannot
    check — no resolver, timeout, network down — and treating that as
    undeliverable would block real sends on a transient DNS blip. Only a
    definite negative stops a send; anything unknown proceeds and lets Gmail
    be the judge.
    """
    domain = (recipient or "").rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return True
    if domain not in cache:
        from contact_verify import domain_has_mx
        try:
            cache[domain] = domain_has_mx(domain)
        except Exception:
            cache[domain] = None
    return cache[domain] is not False


def _contact_has_been_emailed(contact_id: Optional[str]) -> bool:
    """True once anything has actually gone out to this contact.

    Guards edits that would move the address underneath a sent history. The
    stored recipient is what the app treats as the record of who has already
    been written to, and re-pointing it makes a follow-up ("following up on my
    note from…") arrive at someone who never received the note.
    """
    if not contact_id:
        return False
    return bool(db.query_one(
        "SELECT 1 FROM emails WHERE contact_id=? "
        # send_attempted_at counts. It is set before the network call and
        # cleared only on a definite verdict, so a row sitting in that window
        # may already be in the recipient's inbox. Treating it as "nothing
        # sent yet" let the address move mid-flight, after which
        # find_delivered_message searched the wrong mailbox — so the retry
        # could not tell the send had landed — and a confirmed resend
        # delivered real mail to someone who was never the recipient.
        "AND (status='sent' OR gmail_message_id IS NOT NULL "
        "     OR send_attempted_at IS NOT NULL) LIMIT 1",
        (contact_id,),
    ))


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


def _refusal(email_id: str, error: str, **extra) -> Dict[str, Any]:
    """One refusal entry for a send batch.

    `retryable` is deliberately NOT defaulted: Emails.jsx buckets on
    `retryable === false` vs `!== false`, so materialising it on the two
    entries that omit it today would move rows between the "skipped" and
    "refused" buckets.
    """
    return {"email_id": email_id, "success": False, **extra, "error": error}


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
        # Rewrite the rung it already is. Recomposing every follow-up as step 1
        # would hand the third nudge the first one's wording — the exact
        # repetition the cadence exists to prevent.
        cadence_steps = db.get_follow_up_cadence().get("steps") or []
        step = int(email.get("follow_up_step") or 0) or (
            db.query_one("SELECT COUNT(*) AS n FROM emails WHERE contact_id=? "
                         "AND is_follow_up=1 AND status='sent'",
                         (email["contact_id"],))["n"] + 1)
        previous = db.query_one(
            "SELECT * FROM emails WHERE contact_id=? AND status='sent' "
            "AND sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 1",
            (email["contact_id"],))
        composed = composer.compose_follow_up(
            contact, company, original, step=step,
            total_steps=max(step, len(cadence_steps)), previous=previous)
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

# How long one contact may spend waiting for the per-minute generation window.
# Long enough to ride out several contended reopenings, short enough that a
# permanently full window ends the run with a report rather than a job that
# looks alive forever.
_GENERATION_WAIT_BUDGET_SECONDS = 300.0


def _bounced_for_current_address(email: Dict, contact: Optional[Dict]) -> bool:
    """True when the address this message would go to is the one that bounced.

    A bounce is a fact about an *address*, not about a person. Both bounce
    gates used to ask "has anything to this contact ever bounced", and nothing
    in the app ever clears `emails.bounced_at` — so fixing the address, which
    is exactly what the refusal tells the user to do, left the contact
    permanently retired: the button kept repeating the same instruction, and
    the due list kept them out even after a fresh first-contact email to the
    new address was delivered.
    """
    current = str((contact or {}).get("email") or "").strip().lower()
    if (contact or {}).get("bounced_at"):
        return True
    if not current:
        # No contact row to compare against: fall back to the row's own flag,
        # which is the conservative answer.
        return bool(email.get("bounced_at"))
    # A row with no recipient_email predates that column and cannot say *which*
    # address died, so it is read as being about the current one — refusing is
    # the safe answer when the question is "will this bounce". Changing the
    # contact's address clears those rows explicitly (see update_contact),
    # which is the user asserting the problem is fixed.
    return bool(db.query_one(
        "SELECT 1 FROM emails WHERE contact_id=? AND bounced_at IS NOT NULL "
        "AND lower(COALESCE(recipient_email, ?)) = ? LIMIT 1",
        (email.get("contact_id"), current, current)))


def _wait_for_generation_slot(job_id: str, seconds: float) -> bool:
    """Sleep out a per-minute window in small steps so Stop still works.
    Returns True when the job was cancelled while waiting."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        job = db.get_job(job_id)
        if not job or job["status"] == "cancelled":
            return True
        time.sleep(min(0.5, remaining))
    job = db.get_job(job_id)
    return not job or job["status"] == "cancelled"


def _thread_root(email: Dict) -> Dict:
    """The first-contact email a follow-up hangs off.

    Follow-ups are written with `original_email_id` pointing straight at the
    root, so this walks at most one link — but it is written as a bounded walk
    because a hand-edited or legacy row could chain, and a cycle here would
    hang the request thread rather than fail it.
    """
    current = email
    seen = {current.get("id")}
    while current.get("is_follow_up") and current.get("original_email_id"):
        parent_id = current["original_email_id"]
        if parent_id in seen:
            break
        seen.add(parent_id)
        parent = db.get_email(parent_id)
        if not parent:
            break
        current = parent
    return current


def _follow_up_plan(contact_id: Optional[str], *, manual: bool) -> Dict[str, Any]:
    """Whether this person may get another follow-up, and which rung it is.

    Returns {"step", "total", "previous", "refusal"}. Every caller that creates
    a follow-up goes through here, so the batch button and the single button
    cannot drift into disagreeing about who is safe to chase.

    `manual` is the user clicking "Draft follow-up" on a specific sent email.
    It relaxes exactly one rule: a follow-up they trashed retires the contact
    from the automatic due list (trashing is how you say "not this one"), but
    it must not veto them asking for another one by hand.
    """
    cadence = db.get_follow_up_cadence()
    steps = list(cadence.get("steps") or [])
    plan = {"step": None, "total": len(steps), "previous": None,
            "refusal": None, "manual": manual}
    if not cadence.get("enabled") or not steps:
        plan["refusal"] = ("Follow-ups are switched off. Turn the cadence back on "
                           "in Settings to draft one.")
        return plan
    if not contact_id:
        plan["refusal"] = "Contact for this email no longer exists"
        return plan

    # A follow-up is written on the premise of silence ("no reply yet"). The
    # reply can easily sit on a sibling email, so ask about the *person*, the
    # way get_follow_up_candidates does — the row-level has_response flag says
    # nothing about whether they have already written back.
    #
    # Only a reply the current checker verified may block this. An unverified
    # legacy flag (the old checker counted bounces, auto-replies and our own
    # messages) is not evidence, and letting it refuse switched follow-ups off
    # for most of the database.
    if db.query_one(
            "SELECT 1 FROM emails WHERE contact_id=? AND has_response=1 "
            "AND response_verified_at IS NOT NULL LIMIT 1", (contact_id,)):
        plan["refusal"] = (
            "This person already replied (on one of your emails to them), so a "
            "\"no reply yet\" follow-up would be wrong. Reply in Gmail instead.")
        return plan

    # The postmaster already refused this address. A bounce reads as "no
    # reply", which is exactly the condition that makes someone a follow-up
    # candidate — the due list has always known that, but the manual button
    # did not, so the one route a user actually clicks was the one that would
    # cheerfully draft mail to a dead mailbox.
    contact_row = db.get_contact(contact_id) or {}
    if _bounced_for_current_address({"contact_id": contact_id}, contact_row):
        plan["refusal"] = (
            "Mail to this address bounced, so a follow-up would bounce too. Add "
            "a different address for this person first.")
        return plan
    # Beside the bounce gate, for the same reason it is here: drafting a
    # follow-up to somebody on the do-not-contact list produces work whose only
    # possible outcome is the send path refusing it.
    blocked = suppression.match((contact_row or {}).get("email") or "",
                                db.list_suppressions())
    if blocked:
        plan["refusal"] = suppression.blocked_reason(
            blocked, (contact_row or {}).get("email") or "This address")
        return plan

    # Nothing dedupes downstream: two clicks used to mean two near-identical
    # follow-ups in Drafts, both caught by "Select all", both delivered. The
    # question is per *person*, not per row — someone with several sent
    # first-contact emails was otherwise given one follow-up per email.
    #
    # `sent` is never blocking: that rung is spent, and the count below decides
    # whether another is owed. `trashed` differs by route. Trashing a draft is
    # how the user says "not this one", so it retires the contact from the
    # automatic due list — but it must not veto them clicking Draft follow-up
    # themselves, which is a fresh, explicit request.
    # Trashing a follow-up retires the contact from the automatic list. The
    # decision is stamped on the contact as well as implied by the trashed row,
    # because "Delete forever" erases the row — and with it the only record
    # that the user said no.
    if not manual and contact_row.get("follow_ups_declined_at"):
        plan["refusal"] = ("You trashed a follow-up to this person, so they are "
                           "out of the automatic list. Draft one by hand if you "
                           "change your mind.")
        return plan
    spent = "('sent', 'trashed')" if manual else "('sent')"
    if db.query_one(
            f"SELECT 1 FROM emails WHERE contact_id=? "
            f"AND (is_follow_up=1 OR original_email_id IS NOT NULL) "
            f"AND status NOT IN {spent} LIMIT 1", (contact_id,)):
        plan["refusal"] = ("A follow-up to this person has already been drafted "
                           "— find it in Drafts.")
        return plan

    # No sent_at predicate on purpose. A rung is spent by being delivered, and
    # a legacy import can leave a delivered follow-up with no timestamp
    # (migrate_legacy_data passes the legacy row's missing date straight
    # through). Filtering those out made them invisible to the rung count, so
    # this handed out rung 1 again to someone who already had it.
    sent_rows = db.query(
        # Undated rows sort first, so sent_rows[-1] — the message the next rung
        # is written against — is the most recent one we actually know a date
        # for.
        "SELECT * FROM emails WHERE contact_id=? AND status='sent' "
        "ORDER BY sent_at IS NULL DESC, sent_at ASC", (contact_id,))
    if not sent_rows:
        plan["refusal"] = "Nothing has been sent to this person yet."
        return plan
    already = sum(1 for r in sent_rows
                  if r.get("is_follow_up") or r.get("original_email_id"))
    if already >= len(steps):
        plan["refusal"] = (
            f"All {len(steps)} follow-up{'s' if len(steps) != 1 else ''} in your "
            f"cadence have already gone to this person. Add a step in Settings "
            f"if you want another.")
        return plan
    plan["step"] = already + 1
    plan["previous"] = sent_rows[-1]
    return plan


# One follow-up may be created at a time, process-wide.
#
# Composing a follow-up is an LLM round trip lasting seconds, and it sits
# *between* the gate and the INSERT. So the batch job could be inside the call
# for contact C while the user clicked "Draft follow-up" on C in another tab:
# both gates passed, both wrote, and C ended up with two rung-1 drafts carrying
# the same text — both caught by "Select all", both delivered. Nothing
# downstream dedupes drafts, and the in-batch send guard only catches two
# follow-ups inside a single batch.
#
# A lock rather than a unique index because the rule is "one *unsent* follow-up
# per person", which SQLite cannot express as a partial unique index over a
# mutable status column without also forbidding the legitimate second rung.
_follow_up_lock = threading.Lock()


def _create_follow_up(original: Dict, contact: Dict, plan: Dict) -> Dict:
    """Compose and store one rung, re-checking the gate under the lock.

    `plan` is what the caller was told a moment ago; the authoritative decision
    is the one made here, with nothing able to interleave between it and the
    INSERT. Returns None when the recheck now refuses.
    """
    company = db.get_company(contact["company_id"]) if contact.get("company_id") else None
    composed = composer.compose_follow_up(
        contact, company, original,
        step=plan["step"], total_steps=plan["total"], previous=plan["previous"])
    with _follow_up_lock:
        fresh = _follow_up_plan(contact["id"], manual=plan.get("manual", False))
        if fresh["refusal"]:
            return None
        if fresh["step"] != plan["step"] or fresh["total"] != plan["total"]:
            # The rung moved while this was being written — the batch job can
            # sit in the per-minute pause for a minute, and a rung sent in that
            # window advances everyone. Storing `composed` now would file
            # rung-1 wording under follow_up_step=2: the recipient reads "I
            # wanted to follow up on my note" for the second time, and the
            # cadence believes rung 2 is spent. Refusing costs one compose;
            # both callers already report it and the contact stays due.
            return None
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
            follow_up_step=fresh["step"],
            used_template_fallback=composed["used_template_fallback"],
            fallback_reason=composed["fallback_reason"],
            llm_model=composed.get("llm_model"),
        )
    if plan.get("manual"):
        # Asking for one by hand is changing your mind about the trashed one.
        db.execute("UPDATE contacts SET follow_ups_declined_at=NULL WHERE id=?",
                   (contact["id"],))
    db.log_event("email", follow_up["id"], "follow_up_generated",
                 f"{contact.get('name') or contact.get('email') or ''} "
                 f"(step {fresh['step']} of {fresh['total']})".strip())
    return follow_up


@app.get("/api/follow-ups")
async def follow_ups_due(days: Optional[int] = Query(None, ge=1, le=90)):
    """Contacts due for their next rung. `days` overrides the cadence gap."""
    candidates = db.get_follow_up_candidates(days=days)
    for c in candidates:
        try:
            sent = datetime.fromisoformat(c["sent_at"])
            c["days_since_sent"] = (datetime.now() - sent).days
        except Exception:
            c["days_since_sent"] = None
    return candidates


@app.get("/api/follow-ups/cadence")
async def get_follow_up_cadence():
    cadence = db.get_follow_up_cadence()
    return {**cadence, "max_steps": MAX_FOLLOW_UP_STEPS,
            "min_gap_days": MIN_FOLLOW_UP_GAP_DAYS,
            "max_gap_days": MAX_FOLLOW_UP_GAP_DAYS}


@app.put("/api/follow-ups/cadence")
async def set_follow_up_cadence(payload: CadenceUpdate):
    cadence = db.update_follow_up_cadence(payload.model_dump())
    db.log_event("settings", "follow_up_cadence", "cadence_updated",
                 f"{'on' if cadence['enabled'] else 'off'}: "
                 f"{cadence['steps'] or 'no steps'}")
    return cadence


@app.post("/api/emails/{email_id}/follow-up")
async def generate_follow_up(email_id: str):
    email = db.get_email(email_id)
    if not email or email["status"] != "sent":
        raise HTTPException(404, "Original sent email not found")
    plan = _follow_up_plan(email.get("contact_id"), manual=True)
    if plan["refusal"]:
        raise HTTPException(409, plan["refusal"])
    contact = db.get_contact(email["contact_id"])
    if not contact:
        raise HTTPException(404, "Contact for this email no longer exists")
    can, err = rate_limiter.can_generate_email()
    if not can:
        raise HTTPException(429, err)
    rate_limiter.record_email_generation()
    # Thread onto the first-contact email even when the click landed on an
    # earlier follow-up: that is the message the whole conversation hangs off,
    # and the send path reads its Gmail ids to build the References header.
    follow_up = _create_follow_up(_thread_root(email), contact, plan)
    if follow_up is None:
        # Something landed while this was being written — a reply, a bounce, or
        # the batch job drafting the same rung. Refusing is the right answer,
        # and it costs the user nothing but a click.
        raise HTTPException(409, _follow_up_plan(email["contact_id"], manual=True)
                            ["refusal"] or "A follow-up to this person was just "
                            "drafted — find it in Drafts.")
    return db.get_email(follow_up["id"])


def _draft_follow_ups_job(job_id: str, candidates: List[Dict[str, Any]]):
    """Draft one follow-up per due contact. Never sends anything."""
    drafted = skipped = 0
    cancelled = False
    notes: List[Dict[str, Any]] = []
    try:
        for i, candidate in enumerate(candidates):
            job = db.get_job(job_id)
            if not job or job["status"] == "cancelled":
                cancelled = True
                break
            db.update_job(job_id, progress_current=i,
                          stage=f"Drafting for {candidate.get('contact_name') or 'contact'}")
            # Re-asked per contact rather than trusted from the due list: this
            # job can run for minutes, and a reply, bounce or hand-written
            # draft landing halfway through has to stop the rest.
            plan = _follow_up_plan(candidate.get("contact_id"), manual=False)
            if plan["refusal"]:
                skipped += 1
                notes.append({"contact_id": candidate.get("contact_id"),
                              "name": candidate.get("contact_name"),
                              "error": plan["refusal"]})
                continue
            can, err = rate_limiter.can_generate_email()
            if not can:
                # Two very different refusals share one message. The daily cap
                # means come back tomorrow; the per-minute burst window means
                # wait a moment. Treating them alike abandoned the batch on the
                # speed bump — a 25-contact due list drafted 10 and reported 15
                # "rate limited", and in template mode (which is instant) that
                # is the normal case, not the edge one. generation.py already
                # waits these out; this is the same shape.
                # Looped, not a single retry. generation_retry_after only ages
                # out the *oldest* timestamp in the window, i.e. it frees one
                # slot — and anything else generating in the meantime (the
                # manual button, Rewrite, a generate-emails job) takes it. One
                # retry then found the window shut again and abandoned the
                # whole tail, which is the failure this pause exists to
                # prevent. Bounded so a permanently contended window cannot
                # keep the job alive forever.
                waited = 0.0
                while not can and waited < _GENERATION_WAIT_BUDGET_SECONDS:
                    wait = rate_limiter.generation_retry_after()
                    if wait is None:
                        break               # waiting genuinely cannot help
                    db.update_job(
                        job_id, progress_current=i,
                        stage="Pausing — per-minute generation limit reached")
                    if _wait_for_generation_slot(job_id, wait):
                        cancelled = True
                        break
                    waited += wait
                    can, err = rate_limiter.can_generate_email()
                if cancelled:
                    break
            if not can:
                for remaining in candidates[i:]:
                    skipped += 1
                    notes.append({"contact_id": remaining.get("contact_id"),
                                  "name": remaining.get("contact_name"),
                                  "error": err})
                break
            contact = db.get_contact(candidate["contact_id"])
            original = db.get_email(candidate["id"])
            if not contact or not original:
                skipped += 1
                notes.append({"contact_id": candidate.get("contact_id"),
                              "name": candidate.get("contact_name"),
                              "error": "contact or original email no longer exists"})
                continue
            rate_limiter.record_email_generation()
            try:
                if _create_follow_up(_thread_root(original), contact, plan):
                    drafted += 1
                else:
                    skipped += 1
                    notes.append({"contact_id": candidate.get("contact_id"),
                                  "name": candidate.get("contact_name"),
                                  "error": "a follow-up to this person was "
                                           "drafted while this run was working"})
            except Exception as e:
                skipped += 1
                notes.append({"contact_id": candidate.get("contact_id"),
                              "name": candidate.get("contact_name"),
                              "error": str(e)})
        db.update_job(job_id, progress_current=(drafted + skipped if cancelled
                                                else len(candidates)))
        # Deliberately NOT only_if_running. That compare-and-swap is against
        # status='running', so on the one path where the tally matters most —
        # the user pressed Stop — the UPDATE matched nothing and `result`
        # stayed NULL. The Emails page handles 'cancelled' and 'done'
        # identically and reads `j.result || {}`, so it announced "0 follow-ups
        # drafted" while the real ones sat in Drafts, selectable by "Select
        # all". The send job has always distinguished cancelled this way.
        db.finish_job(job_id, "cancelled" if cancelled else "done",
                      {"drafted": drafted, "skipped": skipped, "notes": notes})
    except Exception as e:
        db.finish_job(job_id, "failed", error=str(e),
                      result={"drafted": drafted, "skipped": skipped, "notes": notes})


@app.post("/api/follow-ups/draft-all")
async def draft_all_follow_ups(days: Optional[int] = Query(None, ge=1, le=90)):
    """Draft the next rung for every contact currently due. Sends nothing —
    every message still has to be read and sent by hand."""
    running = [j for j in db.list_jobs("follow_up", limit=5) if j["status"] == "running"]
    if running:
        raise HTTPException(409, "Follow-ups are already being drafted.")
    candidates = db.get_follow_up_candidates(days=days)
    if not candidates:
        raise HTTPException(409, "No contacts are due for a follow-up right now.")
    job = db.create_job("follow_up", {"count": len(candidates)})
    db.update_job(job["id"], progress_total=len(candidates))
    threading.Thread(target=_draft_follow_ups_job, args=(job["id"], candidates),
                     daemon=True).start()
    return _parse_job(db.get_job(job["id"]))


@app.post("/api/follow-ups/draft-all/{job_id}/cancel")
async def cancel_draft_follow_ups(job_id: str):
    """Stop drafting. Follow-ups already written stay in Drafts — the loop
    checks between contacts, so nothing is left half-composed."""
    job = db.get_job(job_id)
    if not job or job["type"] != "follow_up":
        raise HTTPException(404, "Follow-up drafting job not found")
    if job["status"] != "running":
        raise HTTPException(409, "That run already finished")
    db.update_job(job_id, status="cancelled")
    return {"success": True}


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
    # One DNS answer per domain per batch. A batch is routinely 20 emails to
    # 3 companies, and the lookup is the only network call here that is not
    # the send itself.
    mx_cache: dict = {}
    # Same rule for follow-ups: two follow-ups to one person are two copies of
    # "I wanted to follow up on my note" three seconds apart.
    follow_up_recipients = set()
    suppressions: list = []
    try:
        # Read once for the batch, before the loop, so a read failure stops
        # everything rather than letting the first few emails through and
        # blocking the rest. An unreadable list blocks everything — the only
        # direction this particular check may fail in.
        #
        # Inside the try, so the failure path still reaches the finally that
        # releases _send_lock. Returning early from above it held the lock for
        # the life of the process and no later batch could ever start.
        #
        # Re-read again per message below; this one exists so an unreadable
        # list stops the batch before the first send rather than failing each
        # message individually.
        try:
            suppressions = db.list_suppressions()
        except Exception as e:
            db.finish_job(
                job_id, status="failed",
                error=f"Could not read the do-not-contact list, so nothing was "
                      f"sent: {e}")
            return
        for i, email_id in enumerate(email_ids):
            job = db.get_job(job_id)
            if not job or job["status"] == "cancelled":
                cancelled = True
                break
            email = db.get_email(email_id)
            if not email or email["status"] not in ("draft", "approved"):
                results.append(_refusal(email_id, "not sendable", retryable=False))
                continue
            if _already_delivered(email):
                # Re-checked here and not only in the handler: a second send of
                # this row would also overwrite gmail_message_id and orphan the
                # thread that reply tracking follows.
                results.append(_refusal(email_id, "already sent", retryable=False))
                failed += 1
                continue
            recipient = str(email.get("contact_email") or "").strip().lower()
            if not email.get("is_follow_up") and recipient in first_contact_recipients:
                results.append(_refusal(
                    email_id,
                    "a first-contact email to this recipient already went out "
                    "in this batch", retryable=False))
                failed += 1
                continue
            if email.get("is_follow_up") and recipient in follow_up_recipients:
                results.append(_refusal(
                    email_id,
                    "a follow-up to this recipient already went out in this "
                    "batch", retryable=False))
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
                        "recipient_email": email.get("contact_email"),
                    })
                    db.log_event("email", email_id, "send_reconciled",
                                 f"found in Gmail Sent → {email.get('contact_email')}")
                    results.append(_refusal(
                        email_id,
                        "already sent — the earlier attempt did land, and it is "
                        "now recorded as sent", retryable=False))
                    failed += 1
                    continue
                if not confirm_resend:
                    results.append(_refusal(
                        email_id,
                        "an earlier attempt reached Gmail but never confirmed, "
                        "and it is not in your Sent folder. Check Gmail before "
                        "retrying.", delivery_unknown=True))
                    failed += 1
                    continue
            can, err = rate_limiter.can_send_email()
            if not can:
                # Nothing from here on is attempted. Record every remaining id
                # so the results screen cannot report a batch as fully sent
                # while recipients were silently never contacted.
                for remaining_id in email_ids[i:]:
                    results.append(_refusal(remaining_id, err))
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
            # Both deliverability gates sit *below* the pending-attempt
            # reconciliation on purpose. Above it, a message already sitting in
            # Gmail Sent — whose contact later bounced, or whose DNS went dark
            # — was refused before the Sent folder was ever consulted, leaving
            # the row a draft with no message id: invisible to reply tracking
            # and refused identically on every retry.
            contact_row = (db.get_contact(email["contact_id"])
                           if email.get("contact_id") else None)
            # A follow-up is written on the premise of silence, and drafting
            # happens days before sending — the batch job runs Monday, the
            # reply check confirms an answer Tuesday, and "Select all" on
            # Tuesday afternoon still had every reason to send it. Both
            # drafting routes refuse in this state; this is the last gate
            # before real mail, and it was the only one that did not ask.
            if email.get("is_follow_up") and email.get("contact_id") and db.query_one(
                    "SELECT 1 FROM emails WHERE contact_id=? AND has_response=1 "
                    "AND response_verified_at IS NOT NULL LIMIT 1",
                    (email["contact_id"],)):
                results.append(_refusal(
                    email_id,
                    f"{recipient} replied since this follow-up was drafted, so "
                    f"it would ask someone who already answered whether they "
                    f"are still interested. Reply in Gmail instead.",
                    retryable=False))
                failed += 1
                continue
            # The do-not-contact list, asked again here rather than trusted
            # from ingest. Rows predate the list, CSVs arrive with addresses
            # already in them, and a draft can sit for a week after somebody
            # asks to be removed — so this is the check that actually holds.
            #
            # Fails closed, unlike every other gate around it. If the list
            # cannot be read, nothing goes out: a wrongly blocked email costs
            # an error message, and a wrongly sent one costs a person who
            # asked to be left alone hearing from you again.
            # Re-read per message, not once for the batch. With the 3-second
            # inter-send delay a 200-email run lasts ten minutes, and the
            # scheduler's unattended batches are this same code — so "I added
            # them to the list while it was sending" has to take effect on the
            # next message, not the next batch. It is a local SELECT over a
            # handful of rows against a network round trip; the cost is nothing.
            try:
                suppressions = db.list_suppressions()
                blocked = suppression.match(recipient, suppressions)
            except Exception as e:
                # Still fails closed — nothing is sent — but it says what
                # actually happened. Reporting this as "they are on your list"
                # sent the user to Settings to remove an entry that does not
                # exist, and marked a transient failure as permanent.
                results.append(_refusal(
                    email_id,
                    f"Could not read the do-not-contact list, so {recipient} "
                    f"was not written to: {e}", retryable=True))
                failed += 1
                continue
            if blocked:
                results.append(_refusal(
                    email_id, suppression.blocked_reason(blocked, recipient),
                    retryable=False))
                failed += 1
                continue
            if _bounced_for_current_address(email, contact_row):
                results.append(_refusal(
                    email_id,
                    f"{recipient} bounced previously — mail to it is "
                    f"undeliverable. Add a different address for this person "
                    f"and it will send.", retryable=False))
                failed += 1
                continue
            if not _domain_accepts_mail(recipient, mx_cache):
                results.append(_refusal(
                    email_id,
                    f"{recipient} has no mail server — the domain does not "
                    f"accept email, so this would bounce.", retryable=False))
                failed += 1
                continue
            if i > 0:
                time.sleep(email_sender.send_delay)
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
            # Record the recipient on *anything but a definite refusal*. This
            # used to sit inside the success branch, so a first copy whose
            # delivery Gmail never confirmed — which may well have been queued
            # and delivered — left the guard unarmed, and the duplicate draft
            # behind it went out for real.
            if recipient and (result.get("success") or result.get("delivery_unknown")):
                if email.get("is_follow_up"):
                    follow_up_recipients.add(recipient)
                else:
                    first_contact_recipients.add(recipient)
            if result.get("success"):
                sent += 1
                rate_limiter.record_email_sent()
                db.update_email(email_id, {
                    "status": "sent", "sent_at": now_iso(),
                    "gmail_message_id": result.get("gmail_message_id"),
                    "gmail_thread_id": result.get("gmail_thread_id"),
                    "send_attempted_at": None, "send_attempt_error": None,
                    # Freeze who this actually went to. Resolving the recipient
                    # by live join meant a later contact edit or re-research
                    # rewrote the record of who had already been written to.
                    "recipient_email": email.get("contact_email"),
                })
                db.update_contact(email["contact_id"], {"status": "sent"})
                db.log_event("email", email_id, "sent",
                             f"→ {email.get('contact_email')}")
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
    # Scheduling needs BOTH gates: the window switched on in Settings, and this
    # batch asking for it. Either alone does nothing — that is the whole
    # arrangement that keeps a background thread from mailing real people
    # before anyone has looked at it.
    scheduled_for = None
    if payload.schedule == "next_window":
        window = db.get_send_window()
        if not window.get("enabled"):
            raise HTTPException(
                400, "Scheduled sending is switched off. Turn on the sending "
                     "window in Settings first — until then nothing sends "
                     "itself.")
        scheduled_for = send_window.next_opening(window)
        if scheduled_for is None:
            raise HTTPException(400, "That sending window never opens. Check "
                                     "the days and hours in Settings.")
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
        from_email = (payload.from_email or "").strip() or (db.get_profile().get("email") or "me")
        # The whole option set, not just the ids. A scheduled batch is sent by
        # a thread minutes or days later, and it can only know what the user
        # chose if the choice was written down — reading these back out of a
        # payload that held only `email_ids` meant "Attach resume" unticked
        # came back as True, and a resume override the user confirmed came
        # back as None.
        job = db.create_job("send", {
            "email_ids": sendable,
            "resume_id": payload.resume_id,
            "attach_resume": bool(payload.attach_resume),
            "from_email": from_email,
            "confirm_resend": bool(payload.confirm_resend),
            "confirm_attachment_change": bool(payload.confirm_attachment_change),
        })
        db.update_job(job["id"], progress_total=len(sendable), stage="Sending")
        if scheduled_for is not None:
            # Both gates passed: the window is enabled *and* this batch asked
            # to be scheduled. Stamp the rows and hand them to the scheduler
            # rather than to Gmail. Nothing is sent in this request.
            #
            # Converted to *this machine's* frame before the tzinfo is dropped.
            # The sweep compares the stamp against db.now_iso(), which is naive
            # server-local — storing the window's wall clock instead meant a
            # UTC container with an Australia/Sydney window fired ten hours
            # out, in whichever direction the offset ran.
            stamp = scheduled_for.astimezone().replace(tzinfo=None).isoformat(
                timespec="seconds")
            for eid in sendable:
                updates = {"status": "approved", "scheduled_at": stamp,
                           "scheduled_by_job": job["id"]}
                # Pin the actual PDF now rather than resolving "the default" at
                # send time. Promoting a different resume between queueing and
                # sending would otherwise restaple a file the draft was never
                # written around — and because the row's own resume_id was
                # NULL, the sweep's attachment check could not see the swap.
                if payload.attach_resume:
                    pinned = _resolved_attachment_id(
                        db.get_email(eid) or {}, True, payload.resume_id)
                    if pinned:
                        updates["resume_id"] = pinned
                db.update_email(eid, updates)
            db.update_job(job["id"], stage=f"Scheduled for {stamp}",
                          progress_current=0)
            db.finish_job(job["id"], "done",
                          {"sent": 0, "failed": 0, "scheduled": len(sendable),
                           "scheduled_at": stamp, "results": []})
            db.log_event("job", job["id"], "send_scheduled",
                         f"{len(sendable)} email(s) → {stamp}")
            _send_lock.release()
            return _parse_job(db.get_job(job["id"]))
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


_SCHEDULER_TICK_SECONDS = int(os.getenv("COLD_SCHEDULER_TICK_SECONDS", "60"))
# A queued message whose moment passed this long ago is dropped rather than
# sent. Generous enough to survive a laptop closed over a weekend, short enough
# that nothing goes out claiming to be timely when it is not.
_SCHEDULE_STALE_AFTER_HOURS = int(os.getenv("COLD_SCHEDULE_STALE_HOURS", "72"))


def scheduled_send_sweep() -> Optional[str]:
    """One pass of the scheduler. Returns the job id it started, or None.

    Split out from the loop so it can be driven directly by a test — a
    behaviour that hands real email to Gmail with nobody watching should not
    only be reachable through a sleeping thread.

    Refuses in every uncertain case: window off, window shut, nothing due,
    another batch already running, Gmail not authenticated. Doing nothing this
    minute costs a minute; sending when we should not costs a real person.
    """
    window = db.get_send_window()
    if not window.get("enabled"):
        return None
    if not send_window.is_open(window):
        return None
    # Anything whose moment passed more than this long ago is not "due", it is
    # forgotten. Without the floor, a queue left behind while the window was
    # switched off would all satisfy `scheduled_at <= now` and go out in one
    # burst the minute it was switched back on — months-old drafts, in a batch
    # nobody asked for today.
    floor = (datetime.now() - timedelta(hours=_SCHEDULE_STALE_AFTER_HOURS)) \
        .isoformat(timespec="seconds")
    stale = db.query(
        "SELECT id FROM emails WHERE scheduled_at IS NOT NULL AND scheduled_at < ? "
        "AND status IN ('draft', 'approved')", (floor,))
    for row in stale:
        db.update_email(row["id"], {"scheduled_at": None, "scheduled_by_job": None})
        db.log_event("email", row["id"], "send_unscheduled",
                     "queued too long ago to send unattended")

    due = db.query(
        "SELECT id, scheduled_by_job FROM emails "
        "WHERE scheduled_at IS NOT NULL AND scheduled_at <= ? "
        "  AND scheduled_at >= ? "
        "  AND status IN ('draft', 'approved') "
        "  AND sent_at IS NULL AND gmail_message_id IS NULL "
        "ORDER BY scheduled_at ASC", (now_iso(), floor))
    if not due:
        return None
    # Group by the batch that scheduled them, so each keeps the attachment and
    # resume options the user chose when they queued it. Send the oldest batch
    # this tick and leave the rest for the next one — one send batch at a time
    # is an invariant of this app, not an accident.
    batch_key = due[0]["scheduled_by_job"]
    ids = [r["id"] for r in due if r["scheduled_by_job"] == batch_key]

    if not _send_lock.acquire(blocking=False):
        return None                      # a manual batch is running
    try:
        email_sender.authenticate()
    except Exception as e:
        _send_lock.release()
        print(f"[scheduler] Gmail not authenticated, leaving {len(ids)} queued: {e}")
        return None
    try:
        origin = _parse_job(db.get_job(batch_key)) if batch_key else None
        options = (origin or {}).get("payload") or {}
        attach = bool(options.get("attach_resume", True))
        resume_override = options.get("resume_id")

        # The attachment check runs again here, against the options actually
        # stored. The request-time check happened before anything was queued,
        # and a draft can be rewritten in the days between — so the only way to
        # be sure the body and the attachment still agree is to ask now, with
        # nobody watching, which is exactly when it matters most.
        if not options.get("confirm_attachment_change"):
            contradicted = []
            for eid in list(ids):
                row = db.get_email(eid)
                if row and _attachment_claim_conflict(row, attach, resume_override):
                    contradicted.append(eid)
            for eid in contradicted:
                ids.remove(eid)
                db.update_email(eid, {"scheduled_at": None,
                                      "scheduled_by_job": None})
                db.log_event("email", eid, "send_unscheduled",
                             "body no longer matches the queued attachment options")
            if not ids:
                _send_lock.release()
                return None

        # Clear the stamp before handing over. If this process dies mid-batch
        # the rows become ordinary drafts the user can look at, rather than
        # something a later sweep picks up and sends again.
        for eid in ids:
            db.update_email(eid, {"scheduled_at": None})
        job = db.create_job("send", {"email_ids": ids, "scheduled_from": batch_key})
        db.update_job(job["id"], progress_total=len(ids),
                      stage="Sending (scheduled)")
        threading.Thread(
            target=_send_batch_job,
            args=(job["id"], ids, resume_override, attach,
                  options.get("from_email") or (db.get_profile().get("email") or "me"),
                  # Never replayed. "Resend it anyway" is a statement about the
                  # rows the user was looking at when they said it; days later
                  # a *different* row in the same batch can have picked up an
                  # unconfirmed attempt, and honouring the stored flag would
                  # send that one a second copy on the strength of a decision
                  # about someone else. A queued row whose delivery has become
                  # unknown is left for a human.
                  False),
            daemon=True,
        ).start()
        return job["id"]
    except Exception:
        _send_lock.release()
        raise


def _scheduler_loop():
    while True:
        try:
            scheduled_send_sweep()
        except Exception as e:                       # never let the loop die
            print(f"[scheduler] sweep failed: {e}")
        time.sleep(_SCHEDULER_TICK_SECONDS)


@app.get("/api/send-window")
async def get_send_window():
    window = db.get_send_window()
    pending = db.query_one(
        "SELECT COUNT(*) AS n, MIN(scheduled_at) AS next FROM emails "
        "WHERE scheduled_at IS NOT NULL AND status IN ('draft', 'approved')")
    upcoming = send_window.next_opening(window)
    return {**window,
            "description": send_window.describe(window),
            "open_now": send_window.is_open(window) if window["enabled"] else None,
            # Same conversion the stamp uses. Returning the window's wall clock
            # here while `next_scheduled_at` below comes back in server-local
            # put two frames in one payload with no marker on either — the
            # "Send at Mon 8:00" button and the "queued Sun 10:00 PM" chip
            # naming the same instant ten hours apart.
            "next_opening": (upcoming.astimezone().replace(tzinfo=None)
                             .isoformat(timespec="seconds") if upcoming else None),
            "detected_timezone": send_window.local_timezone_name(),
            "scheduled_count": (pending or {}).get("n") or 0,
            "next_scheduled_at": (pending or {}).get("next")}


@app.put("/api/send-window")
async def set_send_window(payload: SendWindowUpdate):
    was_enabled = db.get_send_window().get("enabled")
    window = db.update_send_window(payload.model_dump())
    cleared = 0
    if was_enabled and not window["enabled"]:
        # Switching the window off reads as "stop", and the card then says
        # sending is not held for business hours. Leaving rows stamped merely
        # *paused* them: the queue survived, invisible, and re-enabling the
        # window months later released every stale row at once.
        for row in db.query(
                "SELECT id FROM emails WHERE scheduled_at IS NOT NULL "
                "AND status IN ('draft', 'approved')"):
            db.update_email(row["id"], {"scheduled_at": None,
                                        "scheduled_by_job": None})
            cleared += 1
    db.log_event("settings", "send_window", "send_window_updated",
                 send_window.describe(window)
                 + (f" (unqueued {cleared})" if cleared else ""))
    return {**window, "description": send_window.describe(window),
            "unscheduled": cleared}


@app.post("/api/emails/unschedule")
async def unschedule_emails(payload: BulkIds):
    """Take queued messages back out of the scheduler. They stay as drafts."""
    cleared = 0
    for email_id in payload.ids:
        row = db.get_email(email_id)
        if row and row.get("scheduled_at") and not _already_delivered(row):
            db.update_email(email_id, {"scheduled_at": None,
                                       "scheduled_by_job": None})
            cleared += 1
    return {"success": True, "cleared": cleared}


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

@app.get("/api/emails/{email_id}/thread")
def read_thread(email_id: str):
    """The conversation this email started, read from Gmail on demand.

    Nothing here is stored. A reply body is text written by someone outside
    this system, and the app's standing rule for that is to treat it as data —
    keeping it out of the database keeps it out of every prompt, export and
    backup by construction rather than by remembering to exclude it.

    Plain `def`: the Gmail round-trip belongs in the threadpool, not the event
    loop.
    """
    email = db.get_email(email_id)
    if not email:
        raise HTTPException(404, "Email not found")
    if not (email.get("gmail_message_id") or "").strip():
        # Not an error the user can act on by retrying — an unsent draft has no
        # thread, and a delivered-but-unrecorded row needs Re-verify first.
        raise HTTPException(409, "This email has no Gmail thread yet — "
                                 "it has not been sent from this app.")
    try:
        email_sender.authenticate()
    except Exception as e:
        raise HTTPException(401, f"Gmail authentication failed: {e}")

    service = email_sender.service
    try:
        thread_id = (email.get("gmail_thread_id") or "").strip()
        if not thread_id:
            # Legacy rows recorded the message id alone.
            thread_id = (service.users().messages().get(
                userId="me", id=email["gmail_message_id"], format="minimal"
            ).execute() or {}).get("threadId")
        if not thread_id:
            raise HTTPException(404, "Gmail no longer has this thread")
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="full").execute() or {}
    except HTTPException:
        raise
    except Exception as e:
        # Read-only failure. Say so plainly; the stored reply state is
        # untouched, so nothing here can turn a network blip into a verdict.
        raise HTTPException(502, f"Could not read the thread from Gmail: {e}")

    try:
        parsed = thread_reader.parse_thread(
            thread,
            own_address=db.get_profile().get("email"),
            sent_message_id=email.get("gmail_message_id"))
    except Exception as e:
        # Parsing runs over a payload this app did not write. A shape it cannot
        # handle is a failure to read one thread, not a 500 with a traceback in
        # it — and, being read-only, it has changed nothing on the way out.
        raise HTTPException(502, f"Could not read the thread from Gmail: {e}")
    # Reading a thread deliberately writes nothing, so when it disagrees with
    # the stored state the honest move is to say so and point at the check that
    # *is* allowed to decide. Silently correcting the record here would mean
    # two code paths could mark a contact replied, on different rules.
    parsed["thread_id"] = thread_id
    parsed["unrecorded_reply"] = bool(
        parsed["reply_count"] and not email.get("has_response"))
    parsed["unrecorded_bounce"] = bool(
        parsed["bounce_count"] and not email.get("bounced_at"))
    return parsed


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
        f"""SELECT e.*, ct.name AS contact_name,
                  COALESCE(e.recipient_email, ct.email) AS contact_email
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
    new_replies = cleared = failed_checks = confirmed = bounced = 0
    for email in sent_emails:
        verdict = checker.check_thread(
            email["gmail_message_id"], email.get("gmail_thread_id"))
        has_reply, when = verdict["has_reply"], verdict["replied_at"]

        # Record the bounce before anything else. It used to be discarded as
        # "not a reply" — which is the exact condition that makes a contact a
        # follow-up candidate, so a dead address was chased on a schedule.
        if verdict["bounced"] and not email.get("bounced_at"):
            bounced += 1
            at = (verdict["bounced_at"].isoformat(timespec="seconds")
                  if verdict["bounced_at"] else now_iso())
            db.update_email(email["id"], {"bounced_at": at})
            if email.get("contact_id"):
                db.update_contact(email["contact_id"], {
                    "bounced_at": at,
                    "bounce_detail": f"Undeliverable as of {at[:10]}",
                })
            db.log_event("email", email["id"], "bounced",
                         f"→ {email.get('contact_email')}")

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
            "bounced": bounced, "unverified_remaining": remaining}


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


@app.get("/api/suppressions")
async def list_suppressions():
    return db.list_suppressions()


@app.post("/api/suppressions")
async def add_suppression(payload: SuppressionCreate):
    """Add an address or a domain to the do-not-contact list.

    Accepts `dana@acme.com`, `acme.com`, `@acme.com`, or a pasted
    `Dana Lee <dana@acme.com>` — the forms people actually type when they mean
    "stop writing to this person" or "stop writing to anyone here".
    """
    value, kind = suppression.normalize(payload.value)
    if not value:
        raise HTTPException(
            422, "That is not an email address or a domain. Try dana@acme.com "
                 "or acme.com.")
    entry = db.add_suppression(value, kind, reason=payload.reason)
    db.log_event("suppression", entry["id"], "added", f"{kind}: {value}")
    # What it already covers, so the user learns immediately rather than by
    # discovering a send was refused a week later.
    covered = db.query_one(
        "SELECT COUNT(*) AS n FROM contacts WHERE email IS NOT NULL AND email <> ''"
    )["n"]
    matches = [c for c in db.query(
        "SELECT id, name, email FROM contacts "
        "WHERE email IS NOT NULL AND email <> ''")
        if suppression.match(c["email"], [entry])]
    return {**entry, "matched_contacts": len(matches),
            "checked_contacts": covered}


@app.delete("/api/suppressions/{suppression_id}")
async def remove_suppression(suppression_id: str):
    if not db.remove_suppression(suppression_id):
        raise HTTPException(404, "Not on the list")
    db.log_event("suppression", suppression_id, "removed", "")
    return {"success": True}


@app.get("/api/campaigns")
async def list_campaigns():
    """Every campaign, plus what was never in one.

    The unassigned counts are shipped alongside rather than folded in: they are
    permanently unassigned by design, and omitting them would let the campaign
    totals read as the whole database.
    """
    return campaigns_module.build(db.campaign_rows(), db.unassigned_counts())


@app.patch("/api/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, payload: CampaignUpdate):
    """Rename, annotate, or set aside. Deliberately cannot delete: the rows
    pointing at a campaign are real outreach history, and removing the label
    would orphan them into the unassigned pile, which is supposed to mean
    "predates campaigns" rather than "someone tidied up"."""
    campaign = db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    updates: dict = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()[:200]
    if payload.notes is not None:
        updates["notes"] = payload.notes
    if payload.archived is not None:
        updates["archived_at"] = now_iso() if payload.archived else None
    db.update_campaign(campaign_id, updates)
    return db.get_campaign(campaign_id)


@app.get("/api/pipeline")
async def pipeline_board(limit: int = Query(100, ge=10, le=500)):
    """Every contact, filed under the stage the evidence puts them in.

    Read-only, and deliberately does not repair `contacts.status` even where it
    can prove it wrong — opening a page should not rewrite real rows. The
    disagreement comes back as `status_drift` instead.
    """
    return pipeline.build(db.pipeline_rows(), limit=limit)


@app.get("/api/analytics")
async def analytics(days: int = Query(90, ge=7, le=730)):
    """Which kind of email actually earns replies.

    Sent mail only, joined to the contact so the person can be segmented as
    well as the message. Trashed drafts and unsent rows are irrelevant here —
    nobody received them.
    """
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.query(
        """SELECT e.email_type, e.used_template_fallback, e.follow_up_step,
                  e.is_follow_up, e.original_email_id,
                  e.has_response, e.response_verified_at, e.sent_at, e.response_at,
                  ct.email_kind AS contact_email_kind, ct.seniority_rank,
                  c.id AS company_id, c.name AS company_name
           FROM emails e
           LEFT JOIN contacts ct ON e.contact_id = ct.id
           LEFT JOIN companies c ON ct.company_id = c.id
           WHERE e.status='sent' AND e.sent_at IS NOT NULL AND e.sent_at >= ?""",
        (since,))
    return {**analytics_module.build(rows), "days": days}


# ---------- gmail ----------


@app.post("/api/gmail/disconnect")
async def gmail_disconnect():
    removed = email_sender.disconnect()
    return {"success": True, "token_removed": removed}


# The send scheduler runs from startup but does nothing until the sending
# window is switched on, so enabling it in Settings takes effect without a
# restart. Started here, at the bottom, because it references handlers defined
# above. Off under pytest: a thread that hands mail to Gmail has no business
# running inside a test suite, however inert it is meant to be — tests drive
# `scheduled_send_sweep()` directly instead.
if not os.getenv("COLD_DISABLE_SCHEDULER"):
    threading.Thread(target=_scheduler_loop, daemon=True,
                     name="send-scheduler").start()
