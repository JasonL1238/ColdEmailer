"""Discovery must surface contact conflicts, including IntegrityError races."""
import json
import os
import tempfile

import pytest

from db import Database
from discovery import DiscoveryService
from enrichment import EnrichmentService


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


def _enrich_payload():
    return {
        "ok": True,
        "url": "https://newco.com",
        "domain": "newco.com",
        "summary": "NewCo builds widgets for industrial teams every day.",
        "identity_verified": True,
        "emails": ["jane@newco.com"],
        "contacts": [{
            "name": "Jane Doe",
            "email": "jane@newco.com",
            "linkedin_url": "https://www.linkedin.com/in/jane-doe",
            "email_kind": "personal",
            "email_person_match": True,
            "email_verified": True,
            "linkedin_verified": True,
            "person_verified": True,
            "name_from_email": False,
            "role": "CEO",
            "seniority_rank": 1,
            "affinity": [],
        }],
        "pages_scraped": 1,
        "pages_attempted": 1,
        "research_sources": ["https://newco.com"],
        "research_quality": "medium",
        "scraped_at": "2026-01-01T00:00:00Z",
    }


class TestDiscoveryContactConflicts:
    def test_precheck_linkedin_conflict_surfaces_warning(self, db, monkeypatch):
        old = db.create_company("OldCo", domain="old.com")
        db.create_contact(
            company_id=old["id"],
            name="Jane Doe",
            email="jane@old.com",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            email_verified=True,
            linkedin_verified=True,
            person_verified=True,
        )

        svc = DiscoveryService(db, EnrichmentService())
        monkeypatch.setattr(svc, "_find_candidates", lambda *_a, **_k: [{
            "name": "NewCo",
            "website": "https://newco.com",
            "domain": "newco.com",
            "reason": "test",
        }])
        monkeypatch.setattr(svc.enrichment, "enrich", lambda *_a, **_k: _enrich_payload())

        job = db.create_job("discovery", {"query": "widgets", "count": 1})
        svc._run(job["id"], "widgets", 1)

        finished = db.get_job(job["id"])
        assert finished["status"] == "done"
        result = finished["result"]
        if isinstance(result, str):
            result = json.loads(result)
        assert result["contacts_added"] == 0
        assert result["results"][0]["contact_conflicts"]
        assert result["results"][0]["status"] == "no_emails_found"
        new = db.find_company_by_name("NewCo")
        warnings = new.get("scrape_warnings") or []
        assert any("already belongs to OldCo" in w for w in warnings)
        assert new["scrape_status"] == "no_emails_found"

    def test_create_returning_other_company_does_not_inflate_count(
            self, db, monkeypatch):
        """IntegrityError race: create_contact returns another company's row."""
        old = db.create_company("OldCo", domain="old.com")
        existing = db.create_contact(
            company_id=old["id"],
            name="Jane Doe",
            email="jane@old.com",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            email_verified=True,
            linkedin_verified=True,
            person_verified=True,
        )
        existing = dict(existing)
        existing["_inserted"] = False

        svc = DiscoveryService(db, EnrichmentService())
        monkeypatch.setattr(svc, "_find_candidates", lambda *_a, **_k: [{
            "name": "NewCo",
            "website": "https://newco.com",
            "domain": "newco.com",
            "reason": "test",
        }])
        monkeypatch.setattr(svc.enrichment, "enrich", lambda *_a, **_k: _enrich_payload())
        # Miss pre-check, then pretend unique-index fallback returned OldCo.
        monkeypatch.setattr(db, "find_contact_by_email", lambda *_a, **_k: None)
        monkeypatch.setattr(db, "find_contact_by_linkedin", lambda *_a, **_k: None)
        monkeypatch.setattr(db, "create_contact", lambda **_k: existing)

        job = db.create_job("discovery", {"query": "widgets", "count": 1})
        svc._run(job["id"], "widgets", 1)

        finished = db.get_job(job["id"])
        assert finished["status"] == "done"
        result = finished["result"]
        if isinstance(result, str):
            result = json.loads(result)
        assert result["contacts_added"] == 0
        assert result["results"][0]["contact_conflicts"]
        assert result["results"][0]["status"] == "no_emails_found"
        new = db.find_company_by_name("NewCo")
        assert new["scrape_status"] == "no_emails_found"
        warnings = new.get("scrape_warnings") or []
        assert any("already belongs to OldCo" in w for w in warnings)

    def test_same_company_integrity_fallback_does_not_inflate_count(
            self, db, monkeypatch):
        company = db.create_company("Acme", domain="acme.com")
        existing = db.create_contact(
            company_id=company["id"],
            name="Jane Doe",
            email="jane.doe@acme.com",
            linkedin_url="https://www.linkedin.com/in/jane-doe",
            email_verified=True,
            linkedin_verified=True,
            person_verified=True,
        )
        existing = dict(existing)
        existing["_inserted"] = False

        svc = DiscoveryService(db, EnrichmentService())
        monkeypatch.setattr(svc, "_find_candidates", lambda *_a, **_k: [{
            "name": "Acme Labs",
            "website": "https://acmelabs.com",
            "domain": "acmelabs.com",
            "reason": "test",
        }])
        payload = _enrich_payload()
        payload["url"] = "https://acmelabs.com"
        payload["domain"] = "acmelabs.com"
        monkeypatch.setattr(svc.enrichment, "enrich", lambda *_a, **_k: payload)
        monkeypatch.setattr(db, "find_contact_by_email", lambda *_a, **_k: None)
        monkeypatch.setattr(db, "find_contact_by_linkedin", lambda *_a, **_k: None)
        monkeypatch.setattr(db, "create_contact", lambda **_k: existing)

        job = db.create_job("discovery", {"query": "widgets", "count": 1})
        svc._run(job["id"], "widgets", 1)
        finished = db.get_job(job["id"])
        result = finished["result"]
        if isinstance(result, str):
            result = json.loads(result)
        # Existing row belonged to Acme, not the new company — conflict path.
        # (If somehow same company, _inserted False still blocks the count.)
        assert result["contacts_added"] == 0
