"""Tests for the SQLite storage layer."""
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from db import Database, migrate_legacy_data, repair_delivered_email_status


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


def test_profile_defaults_and_update(db):
    profile = db.get_profile()
    assert set(profile) == set(Database.PROFILE_DEFAULTS)
    assert all(v == "" for v in profile.values())

    updated = db.update_profile({"full_name": "  Ada Lovelace  ", "email": "ada@x.com"})
    assert updated["full_name"] == "Ada Lovelace"  # stripped
    assert db.get_profile()["email"] == "ada@x.com"


def test_update_profile_ignores_unknown_keys(db):
    db.update_profile({"full_name": "Ada", "is_admin": True})
    assert "is_admin" not in db.get_profile()


def test_company_crud_and_lookup(db):
    company = db.create_company("Acme Corp", domain="acme.com", industry="Widgets")
    assert db.get_company(company["id"])["name"] == "Acme Corp"
    # name lookup is case-insensitive
    assert db.find_company_by_name("acme corp")["id"] == company["id"]
    assert db.find_company_by_domain("acme.com")["id"] == company["id"]

    db.update_company(company["id"], {"summary": "We make widgets"})
    assert db.get_company(company["id"])["summary"] == "We make widgets"
    assert db.delete_company(company["id"]) is True
    assert db.get_company(company["id"]) is None


def test_deleting_company_cascades_to_contacts_and_emails(db):
    company = db.create_company("Acme")
    contact = db.create_contact(company_id=company["id"], email="a@acme.com")
    email = db.create_email(contact_id=contact["id"], subject="Hi", body="Hello")

    db.delete_company(company["id"])
    assert db.get_contact(contact["id"]) is None
    assert db.get_email(email["id"]) is None


def test_contact_can_be_linkedin_only_and_is_searchable_by_affinity(db):
    company = db.create_company("Acme")
    contact = db.create_contact(
        company_id=company["id"],
        name="Jane Doe",
        email="",
        linkedin_url="https://www.linkedin.com/in/jane-doe",
        role="CTO",
        affinity="University of Pennsylvania, Shared: Stripe",
        source_url="https://acme.com/team",
        evidence="Jane Doe is Acme's CTO and a Wharton alumna.",
        seniority_rank=3,
    )

    found = db.find_contact_by_linkedin(
        "https://www.linkedin.com/in/jane-doe")
    assert found["id"] == contact["id"]
    assert db.list_contacts(search="Stripe")[0]["id"] == contact["id"]


def test_contact_email_and_linkedin_are_unique(db):
    company = db.create_company("Acme")
    db.create_contact(company_id=company["id"], email="a@acme.com", name="A")
    dup = db.create_contact(company_id=company["id"], email="a@acme.com", name="B")
    assert dup["email"] == "a@acme.com"
    assert dup["name"] == "A"  # returns existing on conflict
    assert len([c for c in db.list_contacts() if c["email"] == "a@acme.com"]) == 1

    db.create_contact(
        company_id=company["id"], name="Jane",
        linkedin_url="https://www.linkedin.com/in/jane-doe")
    again = db.create_contact(
        company_id=company["id"], name="Other",
        linkedin_url="https://www.linkedin.com/in/jane-doe/")
    assert again["name"] == "Jane"
    assert len(db.list_contacts(search="jane-doe")) >= 1


def test_company_name_key_dedupes_acme_inc(db):
    first = db.create_company("Acme Inc", domain="acme.com")
    # Same domain must not create a second row
    second = db.create_company("Acme Corporation", domain="acme.com")
    assert second["id"] == first["id"]
    # Different domain with similar name stays separate
    other = db.create_company("Acme Labs", domain="acmelabs.io")
    assert other["id"] != first["id"]
    assert db.find_company_by_name("Acme Inc")["id"] == first["id"]
    assert db.find_company_by_name("Acme Labs")["id"] == other["id"]


def test_legacy_db_upgrades_without_name_key_column(tmp_path):
    """Schema v3 DBs must open after adding name_key / verification columns."""
    import sqlite3
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE companies (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT, url TEXT,
            summary TEXT, industry TEXT, product TEXT, hook TEXT,
            recent_news TEXT, why_care TEXT, location TEXT,
            discovery_note TEXT, research_sources TEXT,
            pages_scraped INTEGER DEFAULT 0, pages_attempted INTEGER DEFAULT 0,
            research_quality TEXT DEFAULT 'low', source TEXT,
            job_id TEXT, scraped_at TEXT, scrape_status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE contacts (
            id TEXT PRIMARY KEY, company_id TEXT, name TEXT, email TEXT,
            linkedin_url TEXT, role TEXT, source TEXT, status TEXT, notes TEXT,
            source_url TEXT, evidence TEXT, affinity TEXT,
            seniority_rank INTEGER DEFAULT 20,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE resumes (
            id TEXT PRIMARY KEY, label TEXT, filename TEXT, path TEXT,
            text_content TEXT, is_default INTEGER, uploaded_at TEXT NOT NULL
        );
        CREATE TABLE emails (
            id TEXT PRIMARY KEY, contact_id TEXT, company_id TEXT,
            subject TEXT, body TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, status TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT,
            entity_id TEXT, event TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL
        );
        PRAGMA user_version=3;
    """)
    conn.close()
    db = Database(path)
    company = db.create_company("Acme", domain="acme.com")
    assert company["name_key"] == "acme"
    contact = db.create_contact(
        company_id=company["id"], email="jane.doe@acme.com",
        name="Jane Doe", email_verified=1, person_verified=1)
    assert contact["person_verified"] == 1


def test_same_display_name_different_domains_stay_separate(db):
    a = db.create_company("Same Name Co", domain="a.com")
    b = db.create_company("Same Name Co", domain="b.com")
    assert a["id"] != b["id"]


def test_acme_inc_and_acme_labs_without_domain_stay_separate(db):
    a = db.create_company("Acme Inc")
    b = db.create_company("Acme Labs")
    assert a["id"] != b["id"]


def test_domain_claim_does_not_steal_unrelated_soft_key(db):
    labs = db.create_company("Acme Labs")  # no domain yet
    inc = db.create_company("Acme Inc", domain="acme.com")
    assert labs["id"] != inc["id"]
    assert db.get_company(labs["id"])["domain"] in (None, "")
    assert db.get_company(inc["id"])["domain"] == "acme.com"


def test_update_contact_rejects_unlisted_fields(db):
    contact = db.create_contact(email="a@b.com", name="A")
    db.update_contact(contact["id"], {"name": "B", "id": "hacked"})
    row = db.get_contact(contact["id"])
    assert row["name"] == "B"
    assert row["id"] == contact["id"]


def test_list_companies_reports_contact_and_reply_counts(db):
    company = db.create_company("Acme")
    c1 = db.create_contact(company_id=company["id"], email="a@acme.com")
    db.create_contact(company_id=company["id"], email="b@acme.com")
    db.create_email(contact_id=c1["id"], status="sent", has_response=True,
                    response_verified_at="2026-03-13T09:00:00")

    row = next(r for r in db.list_companies() if r["id"] == company["id"])
    assert row["contact_count"] == 2
    assert row["sent_count"] == 1
    assert row["reply_count"] == 1
    assert row["unverified_reply_count"] == 0


def test_list_companies_keeps_unverified_replies_out_of_the_reply_count(db):
    """The green count is read as fact. A flag the current checker never
    confirmed (every legacy row) cannot back that up, so it is reported on its
    own instead of inflating the number."""
    company = db.create_company("Legacy Co")
    contact = db.create_contact(company_id=company["id"], email="a@legacy.com")
    db.create_email(contact_id=contact["id"], status="sent", has_response=True,
                    response_at="2026-03-12T16:26:00")   # batch-stamped legacy flag

    row = next(r for r in db.list_companies() if r["id"] == company["id"])
    assert row["reply_count"] == 0
    assert row["unverified_reply_count"] == 1


def test_resume_default_is_exclusive(db):
    first = db.create_resume("A", "a.pdf", "/tmp/a.pdf", "text", is_default=True)
    second = db.create_resume("B", "b.pdf", "/tmp/b.pdf", "text", is_default=True)

    assert db.get_resume(first["id"])["is_default"] == 0
    assert db.get_default_resume()["id"] == second["id"]


def test_get_default_resume_falls_back_to_newest(db):
    db.create_resume("A", "a.pdf", "/tmp/a.pdf", "text", is_default=False)
    assert db.get_default_resume() is not None


def test_follow_up_candidates_respect_age_reply_and_existing_follow_ups(db):
    contact = db.create_contact(email="a@b.com")
    other = db.create_contact(email="b@b.com")
    old = "2020-01-01T00:00:00"

    stale = db.create_email(contact_id=contact["id"], status="sent", sent_at=old)
    db.create_email(contact_id=other["id"], status="sent", sent_at=old, has_response=True)
    db.create_email(contact_id=contact["id"], status="draft", sent_at=old)

    ids = {e["id"] for e in db.get_follow_up_candidates()}
    assert ids == {stale["id"]}

    # once a follow-up exists for it, it drops out
    db.create_email(contact_id=contact["id"], status="draft", original_email_id=stale["id"])
    assert db.get_follow_up_candidates() == []


def test_follow_up_candidates_skip_contacts_who_replied_to_any_email(db):
    """A reply on one email means the person is not "waiting on you" — the
    older unanswered email to the same person must not resurface as due."""
    contact = db.create_contact(email="a@b.com")
    old = "2020-01-01T00:00:00"
    db.create_email(contact_id=contact["id"], status="sent", sent_at=old)
    db.create_email(contact_id=contact["id"], status="sent", sent_at="2020-02-01T00:00:00",
                    has_response=True, response_at="2020-02-02T00:00:00",
                    response_verified_at="2020-02-02T00:05:00")

    assert db.get_follow_up_candidates() == []


def test_an_unverified_reply_flag_does_not_suppress_a_follow_up(db):
    """The legacy checker counted bounces, auto-replies and our own messages, so
    its flags are not evidence of a reply. Letting them stand in for one removed
    ~105 genuinely unanswered contacts from this list, i.e. switched the app's
    core recurring action off for most of the database."""
    contact = db.create_contact(email="quiet@b.com")
    stale = db.create_email(contact_id=contact["id"], status="sent",
                            sent_at="2020-01-01T00:00:00")
    # Same person, flagged by the old checker with a batch timestamp
    db.create_email(contact_id=contact["id"], status="sent",
                    sent_at="2020-01-02T00:00:00", has_response=True,
                    response_at="2026-03-12T16:26:00")

    assert [e["id"] for e in db.get_follow_up_candidates()] == [stale["id"]]


def test_follow_up_candidates_are_one_per_contact(db):
    """Three unanswered emails to the same person is one follow-up, not three,
    and the count is what both banners label as "contacts"."""
    contact = db.create_contact(email="a@b.com")
    db.create_email(contact_id=contact["id"], status="sent", sent_at="2020-01-01T00:00:00")
    db.create_email(contact_id=contact["id"], status="sent", sent_at="2020-01-02T00:00:00")
    newest = db.create_email(contact_id=contact["id"], status="sent",
                             sent_at="2020-01-03T00:00:00")
    someone_else = db.create_contact(email="c@b.com")
    other = db.create_email(contact_id=someone_else["id"], status="sent",
                            sent_at="2020-01-01T00:00:00")

    candidates = db.get_follow_up_candidates()
    assert {c["id"] for c in candidates} == {newest["id"], other["id"]}
    assert len({c["contact_id"] for c in candidates}) == len(candidates)
    # internal ordering column must not leak into the API payload
    assert all("_latest_sent_at" not in c for c in candidates)


def test_follow_up_candidates_drop_a_contact_once_any_follow_up_exists(db):
    """The has-a-follow-up test has to be per contact. Row-scoped, it just
    deletes the newest email from the group and MAX() hands back an older
    one — so the same person is offered follow-up after follow-up, each
    quoting a message the recipient has already been chased about."""
    contact = db.create_contact(email="a@b.com")
    older = db.create_email(contact_id=contact["id"], status="sent",
                            sent_at="2020-01-01T00:00:00", subject="First")
    newest = db.create_email(contact_id=contact["id"], status="sent",
                             sent_at="2020-02-01T00:00:00", subject="Second")

    assert {c["id"] for c in db.get_follow_up_candidates()} == {newest["id"]}

    db.create_email(contact_id=contact["id"], status="draft", is_follow_up=True,
                    original_email_id=newest["id"])
    assert db.get_follow_up_candidates() == []
    assert older["id"] not in {c["id"] for c in db.get_follow_up_candidates()}


def test_follow_up_candidates_ignore_contacts_with_recent_outreach(db):
    """Recency is a property of the contact, not of one row. Filtering by date
    before the GROUP BY drops the fresh email and reports the person as gone
    quiet, attached to the stale one."""
    contact = db.create_contact(email="a@b.com")
    db.create_email(contact_id=contact["id"], status="sent", subject="Old",
                    sent_at="2020-01-01T00:00:00")
    recent = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    db.create_email(contact_id=contact["id"], status="sent", subject="Fresh",
                    sent_at=recent)

    assert db.get_follow_up_candidates(days=7) == []


def test_email_rows_report_whether_a_follow_up_already_exists(db):
    contact = db.create_contact(email="a@b.com")
    original = db.create_email(contact_id=contact["id"], status="sent",
                               sent_at="2020-01-01T00:00:00")

    assert db.get_email(original["id"])["has_follow_up"] == 0

    follow_up = db.create_email(contact_id=contact["id"], status="draft",
                                is_follow_up=True,
                                original_email_id=original["id"])
    assert db.get_email(original["id"])["has_follow_up"] == 1
    row = next(e for e in db.list_emails() if e["id"] == original["id"])
    assert row["has_follow_up"] == 1

    # a trashed follow-up does not count — that one is never going out
    db.update_email(follow_up["id"], {"status": "trashed"})
    assert db.get_email(original["id"])["has_follow_up"] == 0


def test_repair_marks_already_delivered_emails_as_sent(db):
    """Legacy rows kept their old status while carrying a Gmail message id,
    which left delivered mail sitting in Drafts with a live Send button."""
    contact = db.create_contact(email="a@b.com")
    delivered = db.create_email(contact_id=contact["id"], status="approved",
                                sent_at="2020-01-01T00:00:00",
                                gmail_message_id="19c6de4ca8e06b84")
    binned = db.create_email(contact_id=contact["id"], status="trashed",
                             gmail_message_id="19d0938d88cb062e")
    untouched = db.create_email(contact_id=contact["id"], status="draft")

    assert repair_delivered_email_status(db) == 2
    assert db.get_email(delivered["id"])["status"] == "sent"
    assert db.get_email(delivered["id"])["sent_at"] == "2020-01-01T00:00:00"
    assert db.get_email(binned["id"])["status"] == "sent"
    assert db.get_email(binned["id"])["sent_at"]        # backfilled
    assert db.get_email(untouched["id"])["status"] == "draft"
    # idempotent
    assert repair_delivered_email_status(db) == 0


def test_legacy_import_never_lands_delivered_mail_back_in_drafts(tmp_path):
    """generated_emails.json carried Gmail ids on "accepted"/"trashed" rows;
    mapping those statuses verbatim made already-sent mail sendable again."""
    backend_dir = tmp_path / "backend"
    (backend_dir / "data").mkdir(parents=True)
    (backend_dir / "data" / "generated_emails.json").write_text(json.dumps({
        "e1": {"contact_email": "a@b.com", "contact_name": "A", "company": "Acme",
               "subject": "Hi", "body": "Hello", "status": "accepted",
               "sent_at": "2026-02-17T18:17:16",
               "gmail_message_id": "19c6de4ca8e06b84"},
        "e2": {"contact_email": "c@d.com", "contact_name": "C", "company": "Acme",
               "subject": "Hi", "body": "Hello", "status": "trashed",
               "gmail_message_id": "19d0938d88cb062e"},
        "e3": {"contact_email": "e@f.com", "contact_name": "E", "company": "Acme",
               "subject": "Hi", "body": "Hello", "status": "pending"},
    }))
    fresh = Database(str(tmp_path / "migrated.db"))

    migrate_legacy_data(fresh, str(backend_dir))

    assert fresh.get_email("e1")["status"] == "sent"
    assert fresh.get_email("e2")["status"] == "sent"
    assert fresh.get_email("e3")["status"] == "draft"


def test_job_lifecycle_serializes_result(db):
    job = db.create_job("discovery", {"query": "startups"})
    assert job["status"] == "running"

    db.update_job(job["id"], stage="Scraping", progress_current=2, progress_total=5)
    row = db.get_job(job["id"])
    assert row["stage"] == "Scraping" and row["progress_current"] == 2

    db.finish_job(job["id"], status="done", result={"companies_added": 3})
    row = db.get_job(job["id"])
    assert row["status"] == "done"
    assert '"companies_added": 3' in row["result"]
    assert row["finished_at"]


def test_update_email_coerces_booleans_and_filters_fields(db):
    contact = db.create_contact(email="a@b.com")
    email = db.create_email(contact_id=contact["id"], subject="Hi")

    db.update_email(email["id"], {"has_response": True, "contact_id": "hacked"})
    row = db.get_email(email["id"])
    assert row["has_response"] == 1
    assert row["contact_id"] == contact["id"]


def test_events_are_returned_newest_first(db):
    db.log_event("email", "1", "sent", "first")
    db.log_event("email", "2", "sent", "second")
    events = db.recent_events(limit=2)
    assert events[0]["detail"] == "second"


def test_settings_roundtrip_structured_values(db):
    db.set_setting("limits", {"per_day": 50})
    assert db.get_setting("limits") == {"per_day": 50}
    assert db.get_setting("missing", "fallback") == "fallback"
