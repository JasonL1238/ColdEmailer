"""
SQLite storage layer for Cold Emailer.

Single source of truth for companies, contacts, resumes, emails, jobs,
settings, and activity events. Replaces the legacy CSV/JSON file storage
(contacts.csv, generated_emails.json, company_cache.json) — those are
imported once on first startup by migrate_legacy_data().
"""
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(_BACKEND_DIR, "data", "coldemailer.db")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    domain      TEXT,
    url         TEXT,
    summary     TEXT,
    industry    TEXT,
    product     TEXT,
    hook        TEXT,
    recent_news TEXT,
    why_care    TEXT,
    location    TEXT,
    source      TEXT DEFAULT 'manual',
    job_id      TEXT,
    scraped_at  TEXT,
    scrape_status TEXT DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies (domain);

CREATE TABLE IF NOT EXISTS contacts (
    id         TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies(id) ON DELETE CASCADE,
    name       TEXT DEFAULT '',
    email      TEXT DEFAULT '',
    role       TEXT,
    source     TEXT DEFAULT 'manual',
    status     TEXT DEFAULT 'new',
    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts (company_id);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts (email COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS resumes (
    id           TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    text_content TEXT,
    is_default   INTEGER DEFAULT 0,
    uploaded_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
    id                  TEXT PRIMARY KEY,
    contact_id          TEXT REFERENCES contacts(id) ON DELETE CASCADE,
    company_id          TEXT,
    email_type          TEXT DEFAULT 'application',
    resume_id           TEXT,
    subject             TEXT DEFAULT '',
    body                TEXT DEFAULT '',
    status              TEXT DEFAULT 'draft',
    created_at          TEXT,
    sent_at             TEXT,
    gmail_message_id    TEXT,
    gmail_thread_id     TEXT,
    has_response        INTEGER DEFAULT 0,
    response_at         TEXT,
    original_email_id   TEXT,
    is_follow_up        INTEGER DEFAULT 0,
    used_template_fallback INTEGER DEFAULT 0,
    fallback_reason     TEXT,
    custom_instructions TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_contact ON emails (contact_id);
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails (status);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,           -- 'discovery' | 'generation' | 'enrich'
    status           TEXT DEFAULT 'running',  -- 'running' | 'done' | 'failed' | 'cancelled'
    payload          TEXT,                    -- JSON request
    stage            TEXT,                    -- human-readable current stage
    progress_current INTEGER DEFAULT 0,
    progress_total   INTEGER DEFAULT 0,
    result           TEXT,                    -- JSON result summary
    error            TEXT,
    created_at       TEXT NOT NULL,
    finished_at      TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT,
    entity_id   TEXT,
    event       TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at);
"""


# A live (non-trashed) follow-up already drafted for this email. Surfaced on
# every email row so the UI can stop offering "Draft follow-up" a second time.
_HAS_FOLLOW_UP_SQL = (
    "EXISTS (SELECT 1 FROM emails f "
    "WHERE f.original_email_id = e.id AND f.status <> 'trashed')"
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    """Thread-safe SQLite wrapper. One connection guarded by a lock —
    FastAPI handlers and background job threads share it safely."""

    def __init__(self, path: str = None):
        self.path = path or os.getenv("COLD_DB_PATH") or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

    # ---------- low-level helpers ----------

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _insert(self, table: str, data: Dict[str, Any]):
        cols = ", ".join(data.keys())
        marks = ", ".join(["?"] * len(data))
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))

    def _update(self, table: str, row_id: str, data: Dict[str, Any]):
        if not data:
            return
        sets = ", ".join(f"{k}=?" for k in data.keys())
        self.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), row_id))

    # ---------- settings ----------

    PROFILE_DEFAULTS = {
        "full_name": "",
        "email": "",
        "phone": "",
        "school": "",
        "website": "",
        "background": "",   # short bio / positioning used in prompts
        "signature": "",    # extra signature lines
    }

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set_setting(self, key: str, value: Any):
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def get_profile(self) -> Dict[str, str]:
        profile = dict(self.PROFILE_DEFAULTS)
        stored = self.get_setting("profile", {})
        if isinstance(stored, dict):
            for k in profile:
                if stored.get(k) is not None:
                    profile[k] = stored[k]
        return profile

    def update_profile(self, updates: Dict[str, str]) -> Dict[str, str]:
        profile = self.get_profile()
        for k, v in updates.items():
            if k in profile and v is not None:
                profile[k] = str(v).strip()
        self.set_setting("profile", profile)
        return profile

    # ---------- companies ----------

    def create_company(self, name: str, **kwargs) -> Dict[str, Any]:
        ts = now_iso()
        data = {
            "id": kwargs.get("id") or new_id(),
            "name": name.strip(),
            "domain": kwargs.get("domain"),
            "url": kwargs.get("url"),
            "summary": kwargs.get("summary"),
            "industry": kwargs.get("industry"),
            "product": kwargs.get("product"),
            "hook": kwargs.get("hook"),
            "recent_news": kwargs.get("recent_news"),
            "why_care": kwargs.get("why_care"),
            "location": kwargs.get("location"),
            "source": kwargs.get("source", "manual"),
            "job_id": kwargs.get("job_id"),
            "scraped_at": kwargs.get("scraped_at"),
            "scrape_status": kwargs.get("scrape_status", "pending"),
            "created_at": ts,
            "updated_at": ts,
        }
        self._insert("companies", data)
        return data

    def get_company(self, company_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM companies WHERE id=?", (company_id,))

    def find_company_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        return self.query_one(
            "SELECT * FROM companies WHERE name=? COLLATE NOCASE", (name.strip(),)
        )

    def find_company_by_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        if not domain:
            return None
        return self.query_one("SELECT * FROM companies WHERE domain=?", (domain.lower(),))

    def update_company(self, company_id: str, updates: Dict[str, Any]):
        updates = dict(updates)
        updates["updated_at"] = now_iso()
        self._update("companies", company_id, updates)

    def delete_company(self, company_id: str) -> bool:
        # cascade removes contacts; emails cascade off contacts
        cur = self.execute("DELETE FROM companies WHERE id=?", (company_id,))
        return cur.rowcount > 0

    def list_companies(self, search: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = """
            SELECT c.*,
                   (SELECT COUNT(*) FROM contacts ct WHERE ct.company_id = c.id) AS contact_count,
                   (SELECT COUNT(*) FROM emails e JOIN contacts ct ON e.contact_id = ct.id
                     WHERE ct.company_id = c.id AND e.status = 'sent') AS sent_count,
                   (SELECT COUNT(*) FROM emails e JOIN contacts ct ON e.contact_id = ct.id
                     WHERE ct.company_id = c.id AND e.has_response = 1) AS reply_count
            FROM companies c
        """
        params: tuple = ()
        if search:
            sql += " WHERE c.name LIKE ? OR c.industry LIKE ? OR c.summary LIKE ?"
            like = f"%{search}%"
            params = (like, like, like)
        sql += " ORDER BY c.created_at DESC"
        return self.query(sql, params)

    # ---------- contacts ----------

    def create_contact(self, **kwargs) -> Dict[str, Any]:
        ts = now_iso()
        data = {
            "id": kwargs.get("id") or new_id(),
            "company_id": kwargs.get("company_id"),
            "name": (kwargs.get("name") or "").strip(),
            "email": (kwargs.get("email") or "").strip(),
            "role": kwargs.get("role"),
            "source": kwargs.get("source", "manual"),
            "status": kwargs.get("status", "new"),
            "notes": kwargs.get("notes"),
            "created_at": ts,
            "updated_at": ts,
        }
        self._insert("contacts", data)
        return data

    def get_contact(self, contact_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one(
            """SELECT ct.*, c.name AS company_name, c.url AS company_url
               FROM contacts ct LEFT JOIN companies c ON ct.company_id = c.id
               WHERE ct.id=?""",
            (contact_id,),
        )

    def find_contact_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if not email:
            return None
        return self.query_one(
            "SELECT * FROM contacts WHERE email=? COLLATE NOCASE", (email.strip(),)
        )

    def update_contact(self, contact_id: str, updates: Dict[str, Any]):
        allowed = {"company_id", "name", "email", "role", "status", "notes", "source"}
        updates = {k: v for k, v in updates.items() if k in allowed}
        if updates:
            updates["updated_at"] = now_iso()
            self._update("contacts", contact_id, updates)

    def delete_contact(self, contact_id: str) -> bool:
        cur = self.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        return cur.rowcount > 0

    def list_contacts(
        self, company_id: Optional[str] = None, search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = """
            SELECT ct.*, c.name AS company_name, c.url AS company_url,
                   (SELECT COUNT(*) FROM emails e WHERE e.contact_id = ct.id) AS email_count,
                   (SELECT MAX(e.sent_at) FROM emails e WHERE e.contact_id = ct.id
                     AND e.status='sent') AS last_sent_at
            FROM contacts ct LEFT JOIN companies c ON ct.company_id = c.id
        """
        clauses, params = [], []
        if company_id:
            clauses.append("ct.company_id=?")
            params.append(company_id)
        if search:
            clauses.append("(ct.name LIKE ? OR ct.email LIKE ? OR c.name LIKE ?)")
            like = f"%{search}%"
            params += [like, like, like]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ct.created_at DESC"
        return self.query(sql, tuple(params))

    # ---------- resumes ----------

    def create_resume(self, label: str, filename: str, path: str,
                      text_content: str, is_default: bool = False) -> Dict[str, Any]:
        data = {
            "id": new_id(),
            "label": label,
            "filename": filename,
            "path": path,
            "text_content": text_content,
            "is_default": 1 if is_default else 0,
            "uploaded_at": now_iso(),
        }
        if is_default:
            self.execute("UPDATE resumes SET is_default=0")
        self._insert("resumes", data)
        return data

    def list_resumes(self) -> List[Dict[str, Any]]:
        return self.query("SELECT * FROM resumes ORDER BY uploaded_at DESC")

    def get_resume(self, resume_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM resumes WHERE id=?", (resume_id,))

    def get_default_resume(self) -> Optional[Dict[str, Any]]:
        row = self.query_one("SELECT * FROM resumes WHERE is_default=1")
        if row:
            return row
        return self.query_one("SELECT * FROM resumes ORDER BY uploaded_at DESC LIMIT 1")

    def update_resume(self, resume_id: str, updates: Dict[str, Any]):
        if updates.get("is_default"):
            self.execute("UPDATE resumes SET is_default=0")
        allowed = {"label", "is_default", "text_content"}
        updates = {k: (1 if k == "is_default" and v else v)
                   for k, v in updates.items() if k in allowed}
        self._update("resumes", resume_id, updates)

    def delete_resume(self, resume_id: str) -> Optional[Dict[str, Any]]:
        row = self.get_resume(resume_id)
        if row:
            self.execute("DELETE FROM resumes WHERE id=?", (resume_id,))
        return row

    # ---------- emails ----------

    def create_email(self, **kwargs) -> Dict[str, Any]:
        data = {
            "id": kwargs.get("id") or new_id(),
            "contact_id": kwargs["contact_id"],
            "company_id": kwargs.get("company_id"),
            "email_type": kwargs.get("email_type", "application"),
            "resume_id": kwargs.get("resume_id"),
            "subject": kwargs.get("subject", ""),
            "body": kwargs.get("body", ""),
            "status": kwargs.get("status", "draft"),
            "created_at": kwargs.get("created_at") or now_iso(),
            "sent_at": kwargs.get("sent_at"),
            "gmail_message_id": kwargs.get("gmail_message_id"),
            "gmail_thread_id": kwargs.get("gmail_thread_id"),
            "has_response": 1 if kwargs.get("has_response") else 0,
            "response_at": kwargs.get("response_at"),
            "original_email_id": kwargs.get("original_email_id"),
            "is_follow_up": 1 if kwargs.get("is_follow_up") else 0,
            "used_template_fallback": 1 if kwargs.get("used_template_fallback") else 0,
            "fallback_reason": kwargs.get("fallback_reason"),
            "custom_instructions": kwargs.get("custom_instructions"),
        }
        self._insert("emails", data)
        return data

    def get_email(self, email_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one(
            f"""SELECT e.*, ct.name AS contact_name, ct.email AS contact_email,
                      ct.role AS contact_role, c.name AS company_name,
                      {_HAS_FOLLOW_UP_SQL} AS has_follow_up
               FROM emails e
               LEFT JOIN contacts ct ON e.contact_id = ct.id
               LEFT JOIN companies c ON ct.company_id = c.id
               WHERE e.id=?""",
            (email_id,),
        )

    def update_email(self, email_id: str, updates: Dict[str, Any]):
        allowed = {
            "subject", "body", "status", "sent_at", "gmail_message_id",
            "gmail_thread_id", "has_response", "response_at", "email_type",
            "resume_id", "used_template_fallback", "fallback_reason",
            "custom_instructions", "is_follow_up", "original_email_id",
        }
        updates = {k: v for k, v in updates.items() if k in allowed}
        for bool_key in ("has_response", "is_follow_up", "used_template_fallback"):
            if bool_key in updates:
                updates[bool_key] = 1 if updates[bool_key] else 0
        self._update("emails", email_id, updates)

    def delete_email(self, email_id: str) -> bool:
        cur = self.execute("DELETE FROM emails WHERE id=?", (email_id,))
        return cur.rowcount > 0

    def list_emails(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = f"""
            SELECT e.*, ct.name AS contact_name, ct.email AS contact_email,
                   ct.role AS contact_role, c.name AS company_name,
                   {_HAS_FOLLOW_UP_SQL} AS has_follow_up
            FROM emails e
            LEFT JOIN contacts ct ON e.contact_id = ct.id
            LEFT JOIN companies c ON ct.company_id = c.id
        """
        params: tuple = ()
        if status:
            sql += " WHERE e.status=?"
            params = (status,)
        sql += " ORDER BY e.created_at DESC"
        return self.query(sql, params)

    def get_follow_up_candidates(self, days: int = 7) -> List[Dict[str, Any]]:
        """One candidate per contact who has never replied to anything.

        Filtering has_response on the individual email row is not enough: a
        contact who answered a later email would still surface here as "went
        quiet", and a contact mailed three times would be offered three
        separate follow-ups. The MAX(e.sent_at) aggregate makes SQLite pick
        the most recent unanswered email for each contact.

        Every test that decides whether a *contact* is due has to run on the
        whole group, not on individual rows. A row-level `sent_at < cutoff`
        or "this email has no follow-up yet" simply deletes the newest email
        from the group, and MAX() then hands back a superseded one: the
        contact you mailed two days ago (or already followed up on) reappears
        as "gone quiet", attached to a month-old message.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.query(
            """SELECT e.*, ct.name AS contact_name, ct.email AS contact_email,
                      c.name AS company_name, MAX(e.sent_at) AS _latest_sent_at
               FROM emails e
               LEFT JOIN contacts ct ON e.contact_id = ct.id
               LEFT JOIN companies c ON ct.company_id = c.id
               WHERE e.status='sent' AND e.has_response=0 AND e.is_follow_up=0
                 AND e.sent_at IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM emails f WHERE f.original_email_id = e.id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM emails f
                     JOIN emails o ON f.original_email_id = o.id
                     WHERE e.contact_id IS NOT NULL
                       AND o.contact_id = e.contact_id
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM emails r
                     WHERE r.contact_id = e.contact_id AND r.has_response = 1
                 )
               GROUP BY COALESCE(e.contact_id, e.id)
               HAVING _latest_sent_at < ?
               ORDER BY _latest_sent_at ASC""",
            (cutoff,),
        )
        for row in rows:
            row.pop("_latest_sent_at", None)
        return rows

    # ---------- jobs ----------

    def create_job(self, job_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = {
            "id": new_id(),
            "type": job_type,
            "status": "running",
            "payload": json.dumps(payload),
            "stage": "starting",
            "progress_current": 0,
            "progress_total": 0,
            "result": None,
            "error": None,
            "created_at": now_iso(),
            "finished_at": None,
        }
        self._insert("jobs", data)
        return data

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def list_jobs(self, job_type: Optional[str] = None, limit: int = 25) -> List[Dict[str, Any]]:
        if job_type:
            return self.query(
                "SELECT * FROM jobs WHERE type=? ORDER BY created_at DESC LIMIT ?",
                (job_type, limit),
            )
        return self.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))

    def update_job(self, job_id: str, **updates):
        allowed = {"status", "stage", "progress_current", "progress_total",
                   "result", "error", "finished_at"}
        data = {k: v for k, v in updates.items() if k in allowed}
        if "result" in data and isinstance(data["result"], (dict, list)):
            data["result"] = json.dumps(data["result"])
        if data:
            self._update("jobs", job_id, data)

    def finish_job(self, job_id: str, status: str = "done",
                   result: Any = None, error: Optional[str] = None):
        self.update_job(
            job_id, status=status, result=result, error=error,
            finished_at=now_iso(),
        )

    # ---------- events ----------

    def log_event(self, entity_type: str, entity_id: Optional[str],
                  event: str, detail: str = ""):
        self.execute(
            "INSERT INTO events (entity_type, entity_id, event, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (entity_type, entity_id, event, detail, now_iso()),
        )

    def recent_events(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self.query(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        )


# ---------- legacy migration ----------

def _domain_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.match(r"https?://(?:www\.)?([^/]+)", url.strip())
    return m.group(1).lower() if m else None


def migrate_legacy_data(db: Database, project_root: str, backend_dir: str) -> Dict[str, int]:
    """One-time import of legacy CSV/JSON data. Safe to call repeatedly —
    skips anything already imported (marker setting + duplicate checks)."""
    if db.get_setting("legacy_migrated"):
        return {}

    stats = {"companies": 0, "contacts": 0, "emails": 0}

    # 1. Company cache → companies table
    cache_candidates = [
        os.path.join(backend_dir, "data", "company_cache.json"),
        os.path.join(backend_dir, "backend", "data", "company_cache.json"),
    ]
    company_meta: Dict[str, Dict] = {}
    for path in cache_candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for key, meta in json.load(f).items():
                        if key not in company_meta:
                            company_meta[key] = meta
            except Exception:
                pass

    def _get_or_create_company(name: str) -> Optional[str]:
        name = (name or "").strip()
        if not name:
            return None
        existing = db.find_company_by_name(name)
        if existing:
            return existing["id"]
        meta = company_meta.get(name.lower(), {})
        row = db.create_company(
            name,
            url=meta.get("url"),
            domain=_domain_from_url(meta.get("url")),
            summary=meta.get("summary"),
            industry=meta.get("industry"),
            product=meta.get("product"),
            hook=meta.get("hook_sentence"),
            recent_news=meta.get("recent_news"),
            why_care=meta.get("why_engineers_care"),
            source="migration",
            scrape_status="scraped" if meta.get("summary") else "pending",
            scraped_at=meta.get("cached_at"),
        )
        stats["companies"] += 1
        return row["id"]

    # 2. contacts.csv → contacts (+ companies)
    csv_candidates = [
        os.path.join(backend_dir, "data", "contacts.csv"),
        os.path.join(backend_dir, "backend", "data", "contacts.csv"),
    ]
    legacy_status_map = {"pending": "new", "sent": "sent", "trashed": "archived"}
    for path in csv_candidates:
        if not os.path.isfile(path):
            continue
        try:
            import csv as _csv
            with open(path, "r", encoding="utf-8-sig") as f:
                for row in _csv.DictReader(f):
                    email = (row.get("email") or "").strip()
                    if email and db.find_contact_by_email(email):
                        continue
                    company_id = _get_or_create_company(row.get("company") or "")
                    db.create_contact(
                        id=(row.get("id") or "").strip() or None,
                        company_id=company_id,
                        name=row.get("name") or "",
                        email=email,
                        role=(row.get("role") or "").strip() or None,
                        source="csv",
                        status=legacy_status_map.get(
                            (row.get("status") or "").strip(), "new"),
                    )
                    stats["contacts"] += 1
        except Exception as e:
            print(f"[migrate] contacts.csv import failed for {path}: {e}")
        break  # only first existing file

    # 3. generated_emails.json → emails
    emails_path = os.path.join(backend_dir, "data", "generated_emails.json")
    status_map = {"pending": "draft", "accepted": "approved",
                  "trashed": "trashed", "sent": "sent"}
    if os.path.isfile(emails_path):
        try:
            with open(emails_path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            for email_id, e in legacy.items():
                if db.query_one("SELECT id FROM emails WHERE id=?", (email_id,)):
                    continue
                contact = None
                if e.get("contact_id"):
                    contact = db.get_contact(e["contact_id"])
                if not contact and e.get("contact_email"):
                    contact = db.find_contact_by_email(e["contact_email"])
                if not contact:
                    # Recreate the contact so history is preserved
                    company_id = _get_or_create_company(e.get("company") or "")
                    contact = db.create_contact(
                        id=e.get("contact_id"),
                        company_id=company_id,
                        name=e.get("contact_name") or "",
                        email=e.get("contact_email") or "",
                        source="csv",
                        status="sent" if e.get("status") == "sent" else "new",
                    )
                    stats["contacts"] += 1
                company_id = contact.get("company_id")
                # Gmail already delivered anything carrying a message id or a
                # sent timestamp. Importing those as approved/trashed (which
                # the legacy statuses said) puts a delivered email back in
                # Drafts, where it can be sent a second time.
                status = status_map.get(e.get("status"), "draft")
                if e.get("gmail_message_id") or e.get("sent_at"):
                    status = "sent"
                db.create_email(
                    id=email_id,
                    contact_id=contact["id"],
                    company_id=company_id,
                    email_type="follow_up" if e.get("is_follow_up") else "application",
                    subject=e.get("subject") or "",
                    body=e.get("body") or "",
                    status=status,
                    created_at=e.get("created_at"),
                    sent_at=e.get("sent_at"),
                    gmail_message_id=e.get("gmail_message_id"),
                    has_response=e.get("has_response"),
                    response_at=e.get("response_date"),
                    original_email_id=e.get("original_email_id"),
                    is_follow_up=e.get("is_follow_up"),
                    used_template_fallback=e.get("used_template_fallback"),
                    fallback_reason=e.get("fallback_reason"),
                )
                stats["emails"] += 1
        except Exception as e:
            print(f"[migrate] generated_emails.json import failed: {e}")

    db.set_setting("legacy_migrated", {"at": now_iso(), "stats": stats})
    if any(stats.values()):
        db.log_event("system", None, "migration",
                     f"Imported {stats['companies']} companies, "
                     f"{stats['contacts']} contacts, {stats['emails']} emails")
    return stats


def repair_delivered_email_status(db: "Database") -> int:
    """Mark every email Gmail actually delivered as 'sent'.

    An earlier version of the legacy import kept the old status ("accepted",
    "trashed") on rows that already carried a Gmail message id, leaving
    delivered mail sitting in Drafts with a live Send button — and outside
    the daily cap, the dashboard "sent" count, and reply tracking. Idempotent:
    it only ever touches rows that are demonstrably already delivered.
    """
    rows = db.query(
        "SELECT id, sent_at FROM emails "
        "WHERE gmail_message_id IS NOT NULL AND gmail_message_id <> '' "
        "AND status <> 'sent'"
    )
    for row in rows:
        db.update_email(row["id"], {"status": "sent",
                                    "sent_at": row["sent_at"] or now_iso()})
    if rows:
        db.log_event("system", None, "repair",
                     f"Marked {len(rows)} already-delivered email(s) as sent")
        print(f"[startup] marked {len(rows)} already-delivered email(s) as sent")
    return len(rows)


def repair_mismatched_company_sites(db: "Database") -> int:
    """Quarantine company profiles that were scraped off the wrong website.

    Earlier enrichment accepted the first search result unconditionally, so
    rows exist where the "company" is really an unrelated site (Cogwear ->
    open.spotify.com). Those fabricated summaries read as authoritative in the
    UI and would be quoted back to a real person, so clear the invented
    research and mark the row for re-research. Contacts and email history are
    left untouched. Idempotent.
    """
    from enrichment import domain_matches_name, page_mentions_company

    rows = db.query(
        "SELECT id, name, domain, summary, scrape_status FROM companies "
        "WHERE domain IS NOT NULL AND scrape_status IN ('scraped', 'wrong_site')"
    )
    repaired = 0
    for row in rows:
        # Rows already flagged wrong_site still need their bogus url/domain
        # cleared — an earlier version of this repair left them behind.
        already_flagged = row["scrape_status"] == "wrong_site"
        if not already_flagged and domain_matches_name(row["name"], row["domain"]):
            continue
        if not already_flagged and page_mentions_company(row["name"], row["summary"] or ""):
            continue
        db.update_company(row["id"], {
            "scrape_status": "wrong_site",
            # The url/domain point at the wrong company, so they are not a
            # trustworthy reference for anything — including judging whether a
            # contact's address is "off domain". Clear them with the profile.
            "url": None, "domain": None,
            "summary": None, "industry": None, "product": None,
            "hook": None, "recent_news": None, "why_care": None,
        })
        repaired += 1
    if repaired:
        db.log_event("system", None, "repair",
                     f"Cleared {repaired} company profile(s) scraped from the wrong site")
        print(f"[startup] cleared {repaired} mismatched company profile(s)")
    return repaired


def repair_offdomain_contact_warnings(db: "Database") -> int:
    """Flag existing contacts whose address isn't on their company's domain.

    Discovery used to harvest any address it found on a page, so contacts exist
    that belong to a different company entirely (hello@ultravox.ai filed under
    Fixie.ai). New ones carry a warning note; backfill the old ones so the same
    caution shows before the user sends. Never overwrites an existing note.
    """
    from enrichment import registered_domain

    rows = db.query(
        """SELECT ct.id, ct.email, c.domain, c.name AS company_name
           FROM contacts ct JOIN companies c ON ct.company_id = c.id
           WHERE ct.email <> '' AND c.domain IS NOT NULL
             AND c.scrape_status <> 'wrong_site'
             AND (ct.notes IS NULL OR ct.notes = '')"""
    )
    flagged = 0
    for row in rows:
        email_domain = registered_domain(row["email"].split("@")[-1])
        if email_domain == row["domain"]:
            continue
        db.update_contact(row["id"], {
            "notes": (f"Not on {row['domain']}. Verify this address really "
                      f"reaches {row['company_name']} before sending.")})
        flagged += 1
    if flagged:
        db.log_event("system", None, "repair",
                     f"Flagged {flagged} contact(s) whose address is off the company domain")
        print(f"[startup] flagged {flagged} off-domain contact(s)")
    return flagged


def repair_contact_reply_status(db: "Database") -> int:
    """Give contacts who replied the 'replied' status.

    contacts.status was only ever advanced when a reply was discovered live by
    check-replies, so replies that arrived before that code existed (or were
    imported from the legacy store) left the person showing as merely 'Sent'.
    The Database page then hides the single most useful fact about them.
    Idempotent, and it never downgrades a contact.
    """
    rows = db.query(
        """SELECT DISTINCT ct.id FROM contacts ct
           JOIN emails e ON e.contact_id = ct.id
           WHERE e.has_response = 1 AND ct.status <> 'replied'"""
    )
    for row in rows:
        db.update_contact(row["id"], {"status": "replied"})
    if rows:
        db.log_event("system", None, "repair",
                     f"Marked {len(rows)} contact(s) as replied")
        print(f"[startup] marked {len(rows)} contact(s) as replied")
    return len(rows)
