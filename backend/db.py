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

SCHEMA_VERSION = 5

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
    discovery_note TEXT,
    research_sources TEXT,
    pages_scraped INTEGER DEFAULT 0,
    pages_attempted INTEGER DEFAULT 0,
    research_quality TEXT DEFAULT 'low',
    source      TEXT DEFAULT 'manual',
    job_id      TEXT,
    scraped_at  TEXT,
    scrape_status TEXT DEFAULT 'pending',
    name_key    TEXT,
    scrape_warnings TEXT,
    deep_intel  TEXT,
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
    linkedin_url TEXT,
    role       TEXT,
    source     TEXT DEFAULT 'manual',
    status     TEXT DEFAULT 'new',
    notes      TEXT,
    source_url TEXT,
    evidence   TEXT,
    affinity   TEXT,
    seniority_rank INTEGER DEFAULT 20,
    email_kind TEXT DEFAULT 'unknown',
    email_verified INTEGER DEFAULT 0,
    linkedin_verified INTEGER DEFAULT 0,
    person_verified INTEGER DEFAULT 0,
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
    -- When the *current* reply checker confirmed this reply. NULL means the
    -- flag has never been verified by it (legacy rows imported from the old
    -- checker, which counted bounces, auto-replies and our own messages), so
    -- nothing may present such a reply — or its date — as established fact.
    response_verified_at TEXT,
    original_email_id   TEXT,
    is_follow_up        INTEGER DEFAULT 0,
    used_template_fallback INTEGER DEFAULT 0,
    fallback_reason     TEXT,
    custom_instructions TEXT,
    -- Set when a message was handed to Gmail but the answer never came back
    -- (read timeout, reset, 5xx). Gmail may well have queued it, so the row is
    -- NOT a safe retry until delivery is confirmed one way or the other.
    send_attempted_at   TEXT,
    send_attempt_error  TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_contact ON emails (contact_id);
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails (status);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,           -- 'discovery' | 'generation' | 'enrich' | 'deep_research'
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


# Columns added after the first release. CREATE TABLE IF NOT EXISTS leaves an
# existing table alone, so they have to be added explicitly or every query
# touching them fails against the user's real database.
_ADDED_COLUMNS = {
    # `recipient_email` is who this message actually went to, frozen at send.
    # Without it the recipient of every sent email was resolved by live join
    # onto contacts.email — so editing or re-researching a contact silently
    # rewrote the history of who had already been written to, and a follow-up
    # saying "following up on my note" could go to someone who never got one.
    # `llm_model` is which model actually wrote this draft. The ladder can
    # substitute silently — and when EMAIL_LLM_MODEL names one vendor's model
    # while EMAIL_LLM_PROVIDER names another, it substitutes a different vendor
    # at a very different price — and the resulting draft looks identical.
    "emails": {"send_attempted_at": "TEXT", "send_attempt_error": "TEXT",
               "response_verified_at": "TEXT", "recipient_email": "TEXT",
               "llm_model": "TEXT", "bounced_at": "TEXT"},
    # Why the search matched this company, per the model that suggested it.
    # Kept apart from `summary` on purpose: summary is scraped evidence and is
    # quoted into emails, this is an unverified guess and must not be.
    "companies": {
        "discovery_note": "TEXT",
        "research_sources": "TEXT",
        "pages_scraped": "INTEGER DEFAULT 0",
        "pages_attempted": "INTEGER DEFAULT 0",
        "research_quality": "TEXT DEFAULT 'low'",
        "name_key": "TEXT",
        "scrape_warnings": "TEXT",
        "deep_intel": "TEXT",
    },
    "contacts": {
        "linkedin_url": "TEXT",
        "source_url": "TEXT",
        "evidence": "TEXT",
        "affinity": "TEXT",
        "seniority_rank": "INTEGER DEFAULT 20",
        "email_kind": "TEXT DEFAULT 'unknown'",
        "email_verified": "INTEGER DEFAULT 0",
        "linkedin_verified": "INTEGER DEFAULT 0",
        "person_verified": "INTEGER DEFAULT 0",
        # When the postmaster told us this address is undeliverable. A bounce
        # used to be classified merely as "not a reply", which is exactly the
        # condition that makes a contact a follow-up candidate — so a dead
        # address was chased on a schedule, and every retry damaged the
        # sending reputation that decides whether the good emails arrive.
        "bounced_at": "TEXT",
        "bounce_detail": "TEXT",
    },
}


# A live (non-trashed) follow-up already drafted for *this person*, not just for
# this row. A contact with several sent first-contact emails used to be offered
# (and given) one follow-up per email, so two near-identical "just following up"
# notes reached the same human from one batch.
_HAS_FOLLOW_UP_SQL = (
    "EXISTS (SELECT 1 FROM emails f JOIN emails o ON f.original_email_id = o.id "
    "WHERE f.status <> 'trashed' AND (o.id = e.id OR "
    "(e.contact_id IS NOT NULL AND o.contact_id = e.contact_id)))"
)


# True when *this person* has answered any of our emails — not just when this
# particular row was answered. A follow-up written on the premise of silence is
# an embarrassing thing to send someone who already wrote back, and the reply
# can easily sit on a sibling email.
#
# Only replies the current checker has confirmed count. An unverified legacy
# flag is not evidence of anything, and letting it stand in for a real reply
# switched the follow-up pipeline off for most of the database.
_CONTACT_HAS_REPLIED_SQL = (
    "EXISTS (SELECT 1 FROM emails r "
    "WHERE r.contact_id = e.contact_id AND r.has_response = 1 "
    "AND r.response_verified_at IS NOT NULL)"
)


# A reply flag on this row (or on a sibling email to the same person) that the
# current checker has never confirmed. Surfaced so the UI can say "unverified"
# instead of printing a fabricated reply date as fact.
_REPLY_UNVERIFIED_SQL = "(e.has_response = 1 AND e.response_verified_at IS NULL)"
_CONTACT_REPLY_UNVERIFIED_SQL = (
    "EXISTS (SELECT 1 FROM emails r "
    "WHERE r.contact_id = e.contact_id AND r.has_response = 1 "
    "AND r.response_verified_at IS NULL)"
)


# Wording that promises the recipient a file. The composer decides once, while
# writing, whether a real PDF will go out ("My resume is attached for
# convenience."); the send dialog can still switch the attachment off or swap
# it, and nothing used to compare the two. Detected from the text rather than a
# compose-time flag so the rows written before this existed are covered too.
#
# Lives here (and not in email_composer, which promises it) only because
# email_composer imports db: this direction keeps the import graph acyclic.
_ATTACHMENT_CLAIM_RE = re.compile(
    # "my resume is attached", "the deck is attached below"
    r"\b(?:resume|cv|portfolio|deck|attachment)\b[^.\n]{0,40}\b(?:is|are)\s+attached"
    # "I've attached my resume", "attached my website and resume", "attached resume"
    r"|\battach(?:ed|ing)\b[^.\n]{0,40}?\b(?:resume|cv|portfolio|deck)\b"
    # "please find attached", "see the attached"
    r"|\b(?:find|see)\s+(?:my\s+)?(?:attached|the\s+attached)\b",
    re.I)


def body_claims_attachment(body: Optional[str]) -> bool:
    """True when this email body tells the recipient something is attached."""
    return bool(_ATTACHMENT_CLAIM_RE.search(body or ""))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


_COMPANY_NAME_STOP = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "the",
    "group", "labs", "lab", "technologies", "technology", "tech", "systems",
    "solutions", "software", "ai", "io",
}


def company_name_key(name: Optional[str]) -> Optional[str]:
    """Normalize a company name for duplicate detection ('Acme Inc' == 'acme')."""
    if not name:
        return None
    tokens = [
        t for t in re.split(r"[^a-z0-9]+", name.lower())
        if t and t not in _COMPANY_NAME_STOP
    ]
    return "".join(tokens) or None


def normalize_linkedin_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    value = url.strip().rstrip("/")
    if not value:
        return None
    # Collapse www / trailing slash differences for lookups
    value = re.sub(r"^https?://(www\.)?linkedin\.com",
                   "https://www.linkedin.com", value, flags=re.I)
    return value.rstrip("/")


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
            self._add_missing_columns()
            self._ensure_dedupe_indexes()
            self._backfill_name_keys()
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

    def _add_missing_columns(self):
        """Bring an existing database up to the current schema."""
        for table, columns in _ADDED_COLUMNS.items():
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _ensure_dedupe_indexes(self):
        """Unique indexes that block duplicate emails / LinkedIn / companies.

        Pre-existing duplicate rows are collapsed first so the index create
        does not fail on a user's real database. Non-unique helper indexes that
        reference columns added via ALTER TABLE are created here too — after
        `_add_missing_columns` — so legacy DBs do not fail on first open.
        """
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_companies_name_key ON companies (name_key)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contacts_linkedin "
            "ON contacts (linkedin_url COLLATE NOCASE)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contacts_person_verified "
            "ON contacts (person_verified)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contacts_seniority "
            "ON contacts (seniority_rank)"
        )
        # Normalize LinkedIn URLs before unique-index creation
        rows = self._conn.execute(
            "SELECT id, linkedin_url FROM contacts "
            "WHERE linkedin_url IS NOT NULL AND TRIM(linkedin_url) != ''"
        ).fetchall()
        for row in rows:
            normalized = normalize_linkedin_url(row["linkedin_url"])
            if normalized and normalized != row["linkedin_url"]:
                self._conn.execute(
                    "UPDATE contacts SET linkedin_url=? WHERE id=?",
                    (normalized, row["id"]),
                )
        self._collapse_duplicate_contacts()
        self._collapse_duplicate_companies()
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email_unique "
            "ON contacts (email COLLATE NOCASE) "
            "WHERE email IS NOT NULL AND TRIM(email) != ''"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_linkedin_unique "
            "ON contacts (linkedin_url COLLATE NOCASE) "
            "WHERE linkedin_url IS NOT NULL AND TRIM(linkedin_url) != ''"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_domain_unique "
            "ON companies (domain COLLATE NOCASE) "
            "WHERE domain IS NOT NULL AND TRIM(domain) != ''"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_name_key_unique "
            "ON companies (name_key) "
            "WHERE name_key IS NOT NULL AND TRIM(name_key) != ''"
        )

    def _collapse_duplicate_contacts(self):
        """Keep the best contact per email / LinkedIn within the same company."""
        # Email duplicates — prefer person_verified, then oldest
        rows = self._conn.execute(
            "SELECT lower(email) AS e, company_id, COUNT(*) AS n "
            "FROM contacts WHERE email IS NOT NULL AND TRIM(email) != '' "
            "GROUP BY lower(email), company_id HAVING COUNT(*) > 1"
        ).fetchall()
        for row in rows:
            keep = self._conn.execute(
                "SELECT id FROM contacts WHERE lower(email)=? AND "
                "ifnull(company_id,'')=ifnull(?, '') "
                "ORDER BY person_verified DESC, email_verified DESC, "
                "created_at ASC LIMIT 1",
                (row["e"], row["company_id"]),
            ).fetchone()
            if not keep:
                continue
            self._conn.execute(
                "UPDATE emails SET contact_id=?, company_id=COALESCE(?, company_id) "
                "WHERE contact_id IN ("
                "  SELECT id FROM contacts WHERE lower(email)=? "
                "  AND ifnull(company_id,'')=ifnull(?, '') AND id!=?)",
                (keep["id"], row["company_id"], row["e"], row["company_id"],
                 keep["id"]),
            )
            self._conn.execute(
                "DELETE FROM contacts WHERE lower(email)=? "
                "AND ifnull(company_id,'')=ifnull(?, '') AND id!=?",
                (row["e"], row["company_id"], keep["id"]),
            )
        # Cross-company same email: clear the newer copy's email rather than
        # merging people across companies.
        cross = self._conn.execute(
            "SELECT lower(email) AS e FROM contacts "
            "WHERE email IS NOT NULL AND TRIM(email) != '' "
            "GROUP BY lower(email) HAVING COUNT(DISTINCT company_id) > 1"
        ).fetchall()
        for row in cross:
            keep = self._conn.execute(
                "SELECT id FROM contacts WHERE lower(email)=? "
                "ORDER BY person_verified DESC, created_at ASC LIMIT 1",
                (row["e"],),
            ).fetchone()
            if not keep:
                continue
            self._conn.execute(
                "UPDATE contacts SET email='' WHERE lower(email)=? AND id!=?",
                (row["e"], keep["id"]),
            )

        # LinkedIn duplicates (normalized)
        rows = self._conn.execute(
            "SELECT lower(rtrim(replace(replace(linkedin_url, "
            "'https://linkedin.com', 'https://www.linkedin.com'), "
            "'http://www.linkedin.com', 'https://www.linkedin.com'), '/')) AS u, "
            "company_id "
            "FROM contacts WHERE linkedin_url IS NOT NULL AND TRIM(linkedin_url) != '' "
            "GROUP BY u, company_id HAVING COUNT(*) > 1"
        ).fetchall()
        for row in rows:
            keep = self._conn.execute(
                "SELECT id FROM contacts WHERE "
                "lower(rtrim(replace(replace(linkedin_url, "
                "'https://linkedin.com', 'https://www.linkedin.com'), "
                "'http://www.linkedin.com', 'https://www.linkedin.com'), '/'))=? "
                "AND ifnull(company_id,'')=ifnull(?, '') "
                "ORDER BY person_verified DESC, linkedin_verified DESC, "
                "created_at ASC LIMIT 1",
                (row["u"], row["company_id"]),
            ).fetchone()
            if not keep:
                continue
            self._conn.execute(
                "UPDATE emails SET contact_id=?, company_id=COALESCE(?, company_id) "
                "WHERE contact_id IN ("
                "  SELECT id FROM contacts WHERE "
                "  lower(rtrim(replace(replace(linkedin_url, "
                "'https://linkedin.com', 'https://www.linkedin.com'), "
                "'http://www.linkedin.com', 'https://www.linkedin.com'), '/'))=? "
                "  AND ifnull(company_id,'')=ifnull(?, '') AND id!=?)",
                (keep["id"], row["company_id"], row["u"], row["company_id"],
                 keep["id"]),
            )
            self._conn.execute(
                "DELETE FROM contacts WHERE "
                "lower(rtrim(replace(replace(linkedin_url, "
                "'https://linkedin.com', 'https://www.linkedin.com'), "
                "'http://www.linkedin.com', 'https://www.linkedin.com'), '/'))=? "
                "AND ifnull(company_id,'')=ifnull(?, '') AND id!=?",
                (row["u"], row["company_id"], keep["id"]),
            )

        # Cross-company same LinkedIn: clear the loser's URL (do not merge people).
        cross_li = self._conn.execute(
            "SELECT lower(rtrim(linkedin_url, '/')) AS u FROM contacts "
            "WHERE linkedin_url IS NOT NULL AND TRIM(linkedin_url) != '' "
            "GROUP BY u HAVING COUNT(DISTINCT ifnull(company_id,'')) > 1"
        ).fetchall()
        for row in cross_li:
            keep = self._conn.execute(
                "SELECT id FROM contacts WHERE lower(rtrim(linkedin_url, '/'))=? "
                "ORDER BY person_verified DESC, linkedin_verified DESC, "
                "created_at ASC LIMIT 1",
                (row["u"],),
            ).fetchone()
            if not keep:
                continue
            self._conn.execute(
                "UPDATE contacts SET linkedin_url=NULL, linkedin_verified=0 "
                "WHERE lower(rtrim(linkedin_url, '/'))=? AND id!=?",
                (row["u"], keep["id"]),
            )

    def _collapse_duplicate_companies(self):
        rows = self._conn.execute(
            "SELECT lower(domain) AS d, MIN(created_at) AS first_at "
            "FROM companies WHERE domain IS NOT NULL AND TRIM(domain) != '' "
            "GROUP BY lower(domain) HAVING COUNT(*) > 1"
        ).fetchall()
        for row in rows:
            keep = self._conn.execute(
                "SELECT id FROM companies WHERE lower(domain)=? "
                "ORDER BY created_at ASC LIMIT 1",
                (row["d"],),
            ).fetchone()
            if not keep:
                continue
            dups = self._conn.execute(
                "SELECT id FROM companies WHERE lower(domain)=? AND id!=?",
                (row["d"], keep["id"]),
            ).fetchall()
            for dup in dups:
                self._conn.execute(
                    "UPDATE contacts SET company_id=? WHERE company_id=?",
                    (keep["id"], dup["id"]),
                )
                self._conn.execute(
                    "UPDATE emails SET company_id=? WHERE company_id=?",
                    (keep["id"], dup["id"]),
                )
                self._conn.execute("DELETE FROM companies WHERE id=?", (dup["id"],))

        # Conflicting soft keys: keep the oldest, disambiguate or clear the rest
        # so the unique index can be created.
        key_rows = self._conn.execute(
            "SELECT name_key FROM companies "
            "WHERE name_key IS NOT NULL AND TRIM(name_key) != '' "
            "GROUP BY name_key HAVING COUNT(*) > 1"
        ).fetchall()
        for row in key_rows:
            keep = self._conn.execute(
                "SELECT id, domain FROM companies WHERE name_key=? "
                "ORDER BY created_at ASC LIMIT 1",
                (row["name_key"],),
            ).fetchone()
            if not keep:
                continue
            dups = self._conn.execute(
                "SELECT id, domain FROM companies WHERE name_key=? AND id!=?",
                (row["name_key"], keep["id"]),
            ).fetchall()
            for dup in dups:
                # Same domain → true duplicate, merge into keeper
                if keep["domain"] and dup["domain"] and keep["domain"] == dup["domain"]:
                    self._conn.execute(
                        "UPDATE contacts SET company_id=? WHERE company_id=?",
                        (keep["id"], dup["id"]),
                    )
                    self._conn.execute(
                        "UPDATE emails SET company_id=? WHERE company_id=?",
                        (keep["id"], dup["id"]),
                    )
                    self._conn.execute(
                        "DELETE FROM companies WHERE id=?", (dup["id"],))
                else:
                    # Distinct companies sharing a soft key — disambiguate
                    suffix = (dup["domain"] or dup["id"][:8] or "x").split(".")[0]
                    self._conn.execute(
                        "UPDATE companies SET name_key=? WHERE id=?",
                        (f"{row['name_key']}:{suffix}", dup["id"]),
                    )

    def _backfill_name_keys(self):
        rows = self._conn.execute(
            "SELECT id, name FROM companies WHERE name_key IS NULL OR name_key=''"
        ).fetchall()
        for row in rows:
            key = company_name_key(row["name"])
            if not key:
                continue
            clash = self._conn.execute(
                "SELECT id FROM companies WHERE name_key=? AND id!=?",
                (key, row["id"]),
            ).fetchone()
            if clash:
                continue
            self._conn.execute(
                "UPDATE companies SET name_key=? WHERE id=?", (key, row["id"]))

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
        "affiliations": "",  # past employers / communities used for warm-match research
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
        name_key = kwargs.get("name_key") or company_name_key(name)
        domain = (kwargs.get("domain") or "").strip().lower() or None
        # Prefer returning an existing soft/domain match over inserting a
        # duplicate with a null key to dodge the unique index.
        if domain:
            existing = self.find_company_by_domain(domain)
            if existing:
                return existing
        # Exact name + same domain (or both missing domain) only.
        existing = self.find_company_by_name(name)
        if existing:
            existing_domain = (existing.get("domain") or "").lower() or None
            if existing_domain == domain:
                return existing
            # Same display name, different domain → distinct companies
            if domain and existing_domain and existing_domain != domain:
                existing = None
            elif not domain and not existing_domain:
                return existing
            else:
                # One has domain, one doesn't — prefer treating as same only when
                # soft keys match AND we're not inventing a second domain later.
                existing = None
        if name_key:
            soft = self.find_company_by_name_key(name_key)
            # Soft-key collision only counts as a duplicate when domains match.
            # Never merge solely because both lack a domain ("Acme Inc" vs
            # "Acme Labs" without websites).
            if soft and domain and soft.get("domain") and soft.get("domain") == domain:
                return soft
            if soft and domain and soft.get("domain") and soft.get("domain") != domain:
                name_key = f"{name_key}:{domain.split('.')[0]}"
            elif soft and domain and not soft.get("domain"):
                # Only claim the domain-less soft-key row when the display
                # names match (same company found before its website). Never
                # attach acme.com onto an unrelated "Acme Labs" soft key.
                if (soft.get("name") or "").strip().lower() == name.strip().lower():
                    self.update_company(
                        soft["id"], {"domain": domain, "name_key": name_key})
                    return soft
                name_key = f"{name_key}:{domain.split('.')[0]}"
            elif soft and not domain:
                # Incoming has no domain; soft-key hit may be a different firm.
                name_key = f"{name_key}:{new_id()[:8]}"
        data = {
            "id": kwargs.get("id") or new_id(),
            "name": name.strip(),
            "name_key": name_key,
            "domain": domain,
            "url": kwargs.get("url"),
            "summary": kwargs.get("summary"),
            "industry": kwargs.get("industry"),
            "product": kwargs.get("product"),
            "hook": kwargs.get("hook"),
            "recent_news": kwargs.get("recent_news"),
            "why_care": kwargs.get("why_care"),
            "location": kwargs.get("location"),
            "discovery_note": kwargs.get("discovery_note"),
            "research_sources": json.dumps(kwargs.get("research_sources") or []),
            "pages_scraped": int(kwargs.get("pages_scraped") or 0),
            "pages_attempted": int(kwargs.get("pages_attempted") or 0),
            "research_quality": kwargs.get("research_quality") or "low",
            "source": kwargs.get("source", "manual"),
            "job_id": kwargs.get("job_id"),
            "scraped_at": kwargs.get("scraped_at"),
            "scrape_status": kwargs.get("scrape_status", "pending"),
            "scrape_warnings": json.dumps(kwargs.get("scrape_warnings") or []),
            "deep_intel": (
                json.dumps(kwargs["deep_intel"])
                if isinstance(kwargs.get("deep_intel"), (dict, list))
                else kwargs.get("deep_intel")
            ),
            "created_at": ts,
            "updated_at": ts,
        }
        try:
            self._insert("companies", data)
        except sqlite3.IntegrityError:
            existing = (
                self.find_company_by_domain(domain) if domain else None
            ) or self.find_company_by_name(name) or self.find_company_by_name_key(
                name_key or "")
            if existing:
                return existing
            raise
        return self._decode_company(data)

    @staticmethod
    def _decode_company(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        out = dict(row)
        for key in ("research_sources", "scrape_warnings"):
            raw = out.get(key)
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    out[key] = parsed if isinstance(parsed, list) else []
                except (TypeError, ValueError):
                    out[key] = []
            elif raw is None:
                out[key] = []
        raw_intel = out.get("deep_intel")
        if isinstance(raw_intel, str) and raw_intel.strip():
            try:
                parsed = json.loads(raw_intel)
                out["deep_intel"] = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                out["deep_intel"] = {}
        elif raw_intel is None:
            out["deep_intel"] = None
        return out

    def get_company(self, company_id: str) -> Optional[Dict[str, Any]]:
        return self._decode_company(
            self.query_one("SELECT * FROM companies WHERE id=?", (company_id,)))

    def find_company_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        if not name or not name.strip():
            return None
        exact = self._decode_company(self.query_one(
            "SELECT * FROM companies WHERE name=? COLLATE NOCASE", (name.strip(),)
        ))
        if exact:
            return exact
        return None

    def find_company_by_name_key(self, name_key: str) -> Optional[Dict[str, Any]]:
        if not name_key:
            return None
        return self._decode_company(self.query_one(
            "SELECT * FROM companies WHERE name_key=?", (name_key,),
        ))

    def find_company_by_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        if not domain:
            return None
        return self._decode_company(self.query_one(
            "SELECT * FROM companies WHERE domain=? COLLATE NOCASE",
            (domain.lower(),)))

    def update_company(self, company_id: str, updates: Dict[str, Any]):
        updates = dict(updates)
        for key in ("research_sources", "scrape_warnings"):
            if key in updates and not isinstance(updates[key], str):
                updates[key] = json.dumps(updates[key] or [])
        if "deep_intel" in updates and not isinstance(updates["deep_intel"], str):
            updates["deep_intel"] = (
                json.dumps(updates["deep_intel"])
                if updates["deep_intel"] is not None else None
            )
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
                   -- Verified replies only: the green count is read as fact, and
                   -- unverified legacy flags cannot back that up.
                   (SELECT COUNT(*) FROM emails e JOIN contacts ct ON e.contact_id = ct.id
                     WHERE ct.company_id = c.id AND e.has_response = 1
                       AND e.response_verified_at IS NOT NULL) AS reply_count,
                   (SELECT COUNT(*) FROM emails e JOIN contacts ct ON e.contact_id = ct.id
                     WHERE ct.company_id = c.id AND e.has_response = 1
                       AND e.response_verified_at IS NULL) AS unverified_reply_count
            FROM companies c
        """
        params: tuple = ()
        if search:
            sql += " WHERE c.name LIKE ? OR c.industry LIKE ? OR c.summary LIKE ?"
            like = f"%{search}%"
            params = (like, like, like)
        sql += " ORDER BY c.created_at DESC"
        return [self._decode_company(row) for row in self.query(sql, params)]

    # ---------- contacts ----------

    def create_contact(self, **kwargs) -> Dict[str, Any]:
        ts = now_iso()
        email = (kwargs.get("email") or "").strip()
        linkedin_url = normalize_linkedin_url(kwargs.get("linkedin_url"))
        data = {
            "id": kwargs.get("id") or new_id(),
            "company_id": kwargs.get("company_id"),
            "name": (kwargs.get("name") or "").strip(),
            "email": email,
            "linkedin_url": linkedin_url,
            "role": kwargs.get("role"),
            "source": kwargs.get("source", "manual"),
            "status": kwargs.get("status", "new"),
            "notes": kwargs.get("notes"),
            "source_url": kwargs.get("source_url"),
            "evidence": kwargs.get("evidence"),
            "affinity": kwargs.get("affinity"),
            "seniority_rank": kwargs.get("seniority_rank", 20),
            "email_kind": kwargs.get("email_kind") or "unknown",
            "email_verified": 1 if kwargs.get("email_verified") else 0,
            "linkedin_verified": 1 if kwargs.get("linkedin_verified") else 0,
            "person_verified": 1 if kwargs.get("person_verified") else 0,
            "created_at": ts,
            "updated_at": ts,
        }
        try:
            self._insert("contacts", data)
        except sqlite3.IntegrityError:
            existing = (
                self.find_contact_by_email(email) if email else None
            ) or (self.find_contact_by_linkedin(linkedin_url) if linkedin_url else None)
            if existing:
                existing = dict(existing)
                existing["_inserted"] = False
                return existing
            raise
        data["_inserted"] = True
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

    def find_contact_by_linkedin(self, linkedin_url: str) -> Optional[Dict[str, Any]]:
        linkedin_url = normalize_linkedin_url(linkedin_url)
        if not linkedin_url:
            return None
        row = self.query_one(
            "SELECT * FROM contacts WHERE linkedin_url=? COLLATE NOCASE",
            (linkedin_url,),
        )
        if row:
            return row
        # Legacy rows may still store bare linkedin.com without www.
        variants = {
            linkedin_url.lower().rstrip("/"),
            linkedin_url.lower().rstrip("/").replace(
                "://www.linkedin.com", "://linkedin.com"),
        }
        for variant in variants:
            row = self.query_one(
                "SELECT * FROM contacts WHERE lower(rtrim(linkedin_url, '/')) = ?",
                (variant,),
            )
            if row:
                return row
        return None

    def update_contact(self, contact_id: str, updates: Dict[str, Any]):
        allowed = {
            "company_id", "name", "email", "linkedin_url", "role", "status",
            "notes", "source", "source_url", "evidence", "affinity",
            "seniority_rank", "email_kind", "email_verified",
            "linkedin_verified", "person_verified", "bounced_at",
            "bounce_detail",
        }
        updates = {k: v for k, v in updates.items() if k in allowed}
        if "linkedin_url" in updates:
            updates["linkedin_url"] = normalize_linkedin_url(updates.get("linkedin_url"))
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
                     AND e.status='sent') AS last_sent_at,
                   -- A 'Replied' chip resting only on unverified legacy flags is
                   -- an assertion the app cannot back up, so let the UI say so.
                   (SELECT COUNT(*) FROM emails e WHERE e.contact_id = ct.id
                     AND e.has_response=1 AND e.response_verified_at IS NOT NULL)
                     AS verified_reply_count,
                   (SELECT COUNT(*) FROM emails e WHERE e.contact_id = ct.id
                     AND e.has_response=1 AND e.response_verified_at IS NULL)
                     AS unverified_reply_count
            FROM contacts ct LEFT JOIN companies c ON ct.company_id = c.id
        """
        clauses, params = [], []
        if company_id:
            clauses.append("ct.company_id=?")
            params.append(company_id)
        if search:
            clauses.append(
                "(ct.name LIKE ? OR ct.email LIKE ? OR ct.role LIKE ? "
                "OR ct.affinity LIKE ? OR ct.linkedin_url LIKE ? OR c.name LIKE ?)"
            )
            like = f"%{search}%"
            params += [like, like, like, like, like, like]
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
            "response_verified_at": kwargs.get("response_verified_at"),
            "original_email_id": kwargs.get("original_email_id"),
            "is_follow_up": 1 if kwargs.get("is_follow_up") else 0,
            "used_template_fallback": 1 if kwargs.get("used_template_fallback") else 0,
            "fallback_reason": kwargs.get("fallback_reason"),
            "llm_model": kwargs.get("llm_model"),
            "custom_instructions": kwargs.get("custom_instructions"),
        }
        self._insert("emails", data)
        return data

    @staticmethod
    def _annotate_email(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Add fields derived from the body, so the UI and the send guard agree
        on whether this draft promises the recipient an attachment."""
        if row is not None:
            row["claims_attachment"] = 1 if body_claims_attachment(row.get("body")) else 0
        return row

    def get_email(self, email_id: str) -> Optional[Dict[str, Any]]:
        return self._annotate_email(self.query_one(
            f"""SELECT e.*, ct.name AS contact_name,
                   COALESCE(e.recipient_email, ct.email) AS contact_email,
                      ct.role AS contact_role,
                      ct.email_kind AS contact_email_kind,
                      ct.bounced_at AS contact_bounced_at,
                      c.name AS company_name,
                      {_HAS_FOLLOW_UP_SQL} AS has_follow_up,
                      {_CONTACT_HAS_REPLIED_SQL} AS contact_has_replied,
                      {_REPLY_UNVERIFIED_SQL} AS reply_unverified,
                      {_CONTACT_REPLY_UNVERIFIED_SQL} AS contact_reply_unverified
               FROM emails e
               LEFT JOIN contacts ct ON e.contact_id = ct.id
               LEFT JOIN companies c ON ct.company_id = c.id
               WHERE e.id=?""",
            (email_id,),
        ))

    def update_email(self, email_id: str, updates: Dict[str, Any]):
        allowed = {
            "subject", "body", "status", "sent_at", "gmail_message_id",
            "gmail_thread_id", "has_response", "response_at",
            "response_verified_at", "email_type",
            "resume_id", "used_template_fallback", "fallback_reason", "llm_model",
            "custom_instructions", "is_follow_up", "original_email_id",
            "send_attempted_at", "send_attempt_error", "recipient_email",
            "bounced_at",
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
            SELECT e.*, ct.name AS contact_name,
                   COALESCE(e.recipient_email, ct.email) AS contact_email,
                   ct.role AS contact_role, ct.email_kind AS contact_email_kind,
                   ct.bounced_at AS contact_bounced_at,
                   c.name AS company_name,
                   {_HAS_FOLLOW_UP_SQL} AS has_follow_up,
                   {_CONTACT_HAS_REPLIED_SQL} AS contact_has_replied,
                   {_REPLY_UNVERIFIED_SQL} AS reply_unverified,
                   {_CONTACT_REPLY_UNVERIFIED_SQL} AS contact_reply_unverified
            FROM emails e
            LEFT JOIN contacts ct ON e.contact_id = ct.id
            LEFT JOIN companies c ON ct.company_id = c.id
        """
        params: tuple = ()
        if status:
            sql += " WHERE e.status=?"
            params = (status,)
        sql += " ORDER BY e.created_at DESC"
        return [self._annotate_email(row) for row in self.query(sql, params)]

    def get_follow_up_candidates(self, days: int = 7) -> List[Dict[str, Any]]:
        """One candidate per contact who has never replied to anything.

        Filtering has_response on the individual email row is not enough: a
        contact who answered a later email would still surface here as "went
        quiet", and a contact mailed three times would be offered three
        separate follow-ups. The MAX(e.sent_at) aggregate makes SQLite pick
        the most recent unanswered email for each contact.

        "Replied" means a reply the current checker confirmed. An unverified
        legacy flag is not evidence, and treating it as one silently removed
        most of the pipeline from this list — the banner said "2 contacts went
        quiet" while a hundred genuinely unanswered ones were suppressed.

        Every test that decides whether a *contact* is due has to run on the
        whole group, not on individual rows. A row-level `sent_at < cutoff`
        or "this email has no follow-up yet" simply deletes the newest email
        from the group, and MAX() then hands back a superseded one: the
        contact you mailed two days ago (or already followed up on) reappears
        as "gone quiet", attached to a month-old message.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.query(
            """SELECT e.*, ct.name AS contact_name,
                   COALESCE(e.recipient_email, ct.email) AS contact_email,
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
                       AND r.response_verified_at IS NOT NULL
                 )
                 -- A bounce reads as "no reply", which is exactly what makes a
                 -- contact due for a follow-up. Chasing an address the
                 -- postmaster has already rejected cannot succeed, and each
                 -- retry costs sending reputation that the deliverable
                 -- addresses depend on.
                 AND (ct.bounced_at IS NULL OR ct.id IS NULL)
                 AND e.bounced_at IS NULL
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
                   result: Any = None, error: Optional[str] = None,
                   *, only_if_running: bool = False) -> bool:
        """Mark a job finished. When only_if_running, refuse to overwrite
        cancelled/failed rows (compare-and-swap against status='running')."""
        payload = result
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        if only_if_running:
            cur = self.execute(
                "UPDATE jobs SET status=?, result=?, error=?, finished_at=? "
                "WHERE id=? AND status='running'",
                (status, payload, error, now_iso(), job_id),
            )
            return cur.rowcount > 0
        self.update_job(
            job_id, status=status, result=result, error=error,
            finished_at=now_iso(),
        )
        return True

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


def migrate_legacy_data(db: Database, backend_dir: str) -> Dict[str, int]:
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

    Two things this deliberately does NOT do:

    * It never treats `summary` as identity evidence. The summary is LLM output
      produced from a prompt that opens "Company name: {name}", so the model
      writes the name back out almost every time — the fabricated profiles this
      repair exists to quarantine were the ones that check kept letting through.
      Live enrichment verifies against the *scraped page text*, which is not
      persisted, so a domain-mismatched row simply has no trustworthy proof
      here. Re-research restores anything wrongly caught.
    * It does not limit itself to already-scraped rows. A row whose scrape never
      produced a summary sits at 'pending' while still holding the junk
      url/domain the search picked, and that domain then becomes the yardstick
      for "is this contact address off domain" warnings. `domain` is only ever
      written by enrichment (never by the user), so every row carrying one is
      fair game.
    """
    from enrichment import domain_matches_name, registered_domain

    rows = db.query(
        "SELECT id, name, domain, scrape_status FROM companies "
        "WHERE domain IS NOT NULL AND domain <> ''"
    )
    repaired = 0
    for row in rows:
        # Legacy rows stored a full netloc, so compare on the registered domain
        # ("tower.betterview.com" is still BetterView's site).
        domain = registered_domain(row["domain"]) or row["domain"]
        # Rows already flagged wrong_site still need their bogus url/domain
        # cleared — an earlier version of this repair left them behind.
        if row["scrape_status"] != "wrong_site" and domain_matches_name(row["name"], domain):
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


def _is_offdomain_warning(note: Optional[str]) -> bool:
    """True for a note this repair wrote (and only for one of those).

    Discovery's own live warning reads "Found on the site but not on ...", and
    anything the user typed is theirs — neither may be cleared.
    """
    note = (note or "").strip()
    return note.startswith("Not on ") and "Verify this address really reaches" in note


def repair_offdomain_contact_warnings(db: "Database") -> int:
    """Flag existing contacts whose address isn't on their company's domain.

    Discovery used to harvest any address it found on a page, so contacts exist
    that belong to a different company entirely (hello@ultravox.ai filed under
    Fixie.ai). New ones carry a warning note; backfill the old ones so the same
    caution shows before the user sends. Never overwrites an existing note.

    The warning is only as good as the domain it cites, so it is withheld when
    the domain cannot carry that weight, and any earlier note this repair wrote
    under those conditions is cleared:

    * If the company's stored domain does not itself look like the company's
      (Figure AI -> ezeedomains.com), it is not a yardstick — citing it inverts
      the advice and tells the user to distrust brett.adcock@figure.ai.
    * Someone who already replied, or who has been mailed, has proved the
      address reaches them. "Verify before sending" is noise there.
    """
    from enrichment import domain_matches_name, registered_domain

    rows = db.query(
        """SELECT ct.id, ct.email, ct.status, ct.notes,
                  c.domain, c.name AS company_name, c.scrape_status,
                  (SELECT COUNT(*) FROM emails e
                    WHERE e.contact_id = ct.id AND e.status = 'sent') AS sent_count
           FROM contacts ct LEFT JOIN companies c ON ct.company_id = c.id
           WHERE ct.email <> ''"""
    )
    flagged = cleared = 0
    for row in rows:
        domain = registered_domain(row["domain"] or "") or (row["domain"] or "")
        engaged = row["status"] == "replied" or bool(row["sent_count"])
        trustworthy = (bool(domain) and row["scrape_status"] != "wrong_site"
                       and domain_matches_name(row["company_name"] or "", domain))
        # Both sides through registered_domain: a stored netloc with a subdomain
        # ("tower.betterview.com") could never equal "betterview.com".
        on_domain = registered_domain(row["email"].split("@")[-1]) == domain
        warranted = trustworthy and not on_domain and not engaged

        if warranted and not (row["notes"] or "").strip():
            db.update_contact(row["id"], {
                "notes": (f"Not on {domain}. Verify this address really "
                          f"reaches {row['company_name']} before sending.")})
            flagged += 1
        elif not warranted and _is_offdomain_warning(row["notes"]):
            db.update_contact(row["id"], {"notes": None})
            cleared += 1
    if flagged:
        db.log_event("system", None, "repair",
                     f"Flagged {flagged} contact(s) whose address is off the company domain")
        print(f"[startup] flagged {flagged} off-domain contact(s)")
    if cleared:
        db.log_event("system", None, "repair",
                     f"Cleared {cleared} off-domain warning(s) that cited an "
                     f"unverified domain or a contact who already replied")
        print(f"[startup] cleared {cleared} misleading off-domain warning(s)")
    return flagged


def _batch_stamped_reply_ids(db: "Database") -> List[str]:
    """Reply rows whose response_at is really the moment a checker *ran*.

    The legacy checker wrote `datetime.now()` for every hit, so a polling loop
    over 113 threads left 113 "reply times" seconds apart inside one window.
    A genuine Gmail internalDate does not cluster like that. Any response_at
    shared to the minute by three or more rows is treated as a batch stamp.
    """
    buckets = db.query(
        """SELECT substr(response_at, 1, 16) AS minute, COUNT(*) AS n
           FROM emails
           WHERE has_response = 1 AND response_at IS NOT NULL AND response_at <> ''
           GROUP BY minute HAVING n >= 3"""
    )
    if not buckets:
        return []
    minutes = [b["minute"] for b in buckets]
    marks = ", ".join(["?"] * len(minutes))
    rows = db.query(
        f"""SELECT id FROM emails
            WHERE has_response = 1 AND substr(response_at, 1, 16) IN ({marks})""",
        tuple(minutes),
    )
    return [r["id"] for r in rows]


def repair_unverified_legacy_replies(db: "Database") -> int:
    """Record that pre-existing reply flags have never been verified.

    Every reply the *current* checker confirms carries a response_verified_at,
    so rows without one are exactly the flags inherited from the legacy checker
    — which counted bounces, auto-replies and our own messages in the thread.
    Nothing may print those as fact, and they must not gate the follow-up
    pipeline; that is enforced by the response_verified_at column itself.

    This repair only *reports* the situation (once) so the count is visible in
    the activity feed, and points at the fix. It deliberately makes no Gmail
    calls: re-verification is the user's "Re-verify replies" action, which can
    tell "no reply" apart from "the check failed".
    """
    unverified = db.query_one(
        "SELECT COUNT(*) AS n FROM emails "
        "WHERE has_response = 1 AND response_verified_at IS NULL")["n"]
    if not unverified:
        return 0
    batch_stamped = len(_batch_stamped_reply_ids(db))
    seen = db.get_setting("unverified_replies_reported")
    if seen == unverified:
        return unverified
    detail = (f"{unverified} reply flag(s) predate the current reply checker and "
              f"are shown as unverified")
    if batch_stamped:
        detail += (f" — {batch_stamped} carry a batch timestamp (the moment the old "
                   f"checker ran, not a real reply time)")
    detail += ". Use \"Re-verify replies\" to re-check them against Gmail."
    db.log_event("system", None, "repair", detail)
    db.set_setting("unverified_replies_reported", unverified)
    print(f"[startup] {detail}")
    return unverified


def repair_speculative_company_summaries(db: "Database") -> int:
    """Move unscraped "summaries" out of the field emails quote from.

    When scraping failed, discovery used to store the model's one-line guess at
    why the company matched the search into `summary` — the same field the
    composer quotes as researched fact. The guess is indistinguishable from
    evidence once it is in there, so an email could tell a real person what
    their company does on the strength of nothing. Idempotent.
    """
    rows = db.query(
        "SELECT id, name, summary FROM companies "
        "WHERE summary IS NOT NULL AND summary <> '' "
        "AND discovery_note IS NULL "
        "AND scrape_status IN ('scrape_failed', 'no_website', 'wrong_site')"
    )
    for row in rows:
        db.update_company(row["id"], {
            "summary": None,
            "discovery_note": row["summary"],
        })
    if rows:
        db.log_event("system", None, "repair",
                     f"Moved {len(rows)} unscraped company description(s) out of research")
        print(f"[startup] moved {len(rows)} speculative company summary/-ies to notes")
    return len(rows)


# Role mailboxes contact_verify.GENERIC_LOCALS does not list. Kept local to
# this repair rather than widened globally: GENERIC_LOCALS drives rejection at
# ingest, and quietly turning more addresses into hard rejections is a bigger
# behaviour change than labelling existing rows. contact_ingest documents the
# same gap and defends against it by refusing any mailbox it cannot tie to a
# person.
_EXTRA_ROLE_LOCALS = frozenset({
    "legal", "privacy", "compliance", "security", "abuse", "webmaster",
    "hiring", "internships", "internship", "noreply", "no-reply", "donotreply",
    "do-not-reply", "newsletter", "newsletters", "notifications", "notify",
    "updates", "feedback", "bugs", "postmaster", "mailer-daemon", "subscribe",
    "unsubscribe", "invoices", "invoice", "payments", "orders", "shop",
    "service", "services", "customerservice", "customersupport", "care",
    "auto-reply", "autoreply", "bounces", "dpo", "helpdesk", "it", "reply",
})


def _looks_like_role_local(email: str) -> bool:
    """True when the local part is made of role words.

    Deliberately token-based rather than "is the name blank": jane.doe@ splits
    into two tokens that are not role words, so a real person with no name
    recorded is not mistaken for a company inbox.
    """
    import re as _re

    from contact_verify import GENERIC_LOCALS

    local = (email or "").strip().lower().split("@", 1)[0].split("+", 1)[0]
    if not local:
        return False
    # Whole local first. Splitting on [._+-] means a hyphenated entry can never
    # match token-by-token, which silently made no-reply@, do-not-reply@ and
    # mailer-daemon@ read as people — complete with a "this address does not
    # look like this contact" warning on an autoresponder.
    if local in _EXTRA_ROLE_LOCALS:
        return True
    parts = [p for p in _re.split(r"[._+-]+", local) if p]
    if not parts:
        return False
    # Both vocabularies. Consulting only the extra set left mixed locals —
    # careers.internships@, hr.legal@ — falling between this function and
    # contact_verify.is_generic_inbox, matching neither.
    known = _EXTRA_ROLE_LOCALS | GENERIC_LOCALS
    return all(p in known or len(p) <= 1 for p in parts)


def repair_contact_email_kinds(db: "Database") -> int:
    """Classify contacts that predate `email_kind`.

    Ingest rejects role inboxes (hello@, info@, careers@) and the outreach
    ranking puts named people first, but both read `contacts.email_kind`.
    Anything imported or scraped before that column existed still carries
    'unknown', so info@acme.com is indistinguishable from a named human to
    every filter, chip and sort in the app — and gets an email written to it.

    **This never assigns 'personal', on purpose.** 'personal' means the local
    part was checked against a name that came from somewhere other than the
    address itself. For these rows that provenance is not merely unset, it is
    unrecordable — there is no name_from_email column, and discovery stores
    `name = candidate.name or _guess_name_from_email(local)`. So a legacy row
    can perfectly well be name='Press Releases' / press.releases@acme.com,
    where the name was *derived from* the local part. Comparing the two then
    matches circularly and promotes a role inbox to the strongest verdict —
    the exact opposite of the point, and it would list the address under the
    UI's "Personal email" filter while withholding the "role inbox" chip.

    Passing name_from_email=True takes the conservative branch: a match that
    could be circular is not treated as evidence. The result is that this
    under-claims (a genuine jane.doe@ lands on 'named_unmatched' rather than
    'personal') and never over-claims. Re-researching a company annotates
    contacts properly and upgrades them.

    Classification is pure string work — no DNS — so this is safe on every
    startup and offline. It never sets `email_verified`, which means "MX was
    actually confirmed" and cannot be inferred from an address.
    """
    from contact_verify import verify_email

    rows = db.query(
        # `email <> ''` alone lets through junk like 'n/a' that can never be
        # classified, which would then be re-examined on every startup forever.
        "SELECT id, name, email FROM contacts "
        "WHERE email IS NOT NULL AND email LIKE '%_@_%' "
        "AND (email_kind IS NULL OR email_kind = 'unknown')"
    )
    kinds: Dict[str, int] = {}
    for row in rows:
        verdict = verify_email(row["email"], row["name"], check_mx=False,
                               name_from_email=True)
        kind = verdict["email_kind"]
        # A local outside GENERIC_LOCALS (legal@, hiring@, noreply@) lands on
        # 'named_unmatched', i.e. reads as a person. Keying this off a blank
        # name instead was an over-claim in the other direction: a real
        # person's jane.doe@ that simply had no name attached yet got filed as
        # a company inbox — which is not only wrong in the UI but arms the
        # address-overwrite branch in _enrich_company_async, since that branch
        # triggers on email_kind == 'generic'.
        if kind == "named_unmatched" and _looks_like_role_local(row["email"]):
            kind = "generic"
        if kind == "unknown":
            continue
        db.update_contact(row["id"], {"email_kind": kind})
        kinds[kind] = kinds.get(kind, 0) + 1
    total = sum(kinds.values())
    if total:
        summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        db.log_event("system", None, "repair",
                     f"Classified {total} unclassified contact address(es): {summary}")
        print(f"[startup] classified {total} contact address(es): {summary}")
    return total


def repair_contact_reply_status(db: "Database") -> int:
    """Give contacts who replied the 'replied' status.

    contacts.status was only ever advanced when a reply was discovered live by
    check-replies, so replies that arrived before that code existed left the
    person showing as merely 'Sent'. The Database page then hides the single
    most useful fact about them. Idempotent, and it never downgrades a contact.

    Only *verified* replies promote a contact. This repair used to spread the
    legacy false positives from emails onto 114 contact chips, which is the
    opposite of helpful — an unverified flag is surfaced as "unverified"
    instead (see list_contacts).
    """
    rows = db.query(
        """SELECT DISTINCT ct.id FROM contacts ct
           JOIN emails e ON e.contact_id = ct.id
           WHERE e.has_response = 1 AND e.response_verified_at IS NOT NULL
             AND ct.status <> 'replied'"""
    )
    for row in rows:
        db.update_contact(row["id"], {"status": "replied"})
    if rows:
        db.log_event("system", None, "repair",
                     f"Marked {len(rows)} contact(s) as replied")
        print(f"[startup] marked {len(rows)} contact(s) as replied")
    return len(rows)
