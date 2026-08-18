"""Discovery must surface contact conflicts, including IntegrityError races."""
import json

from discovery import DiscoveryService
from enrichment import EnrichmentService


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


def _company_with_jane(db, company="OldCo", domain="old.com", email="jane@old.com"):
    """A fully verified Jane Doe already owned by an existing company."""
    owner = db.create_company(company, domain=domain)
    return db.create_contact(
        company_id=owner["id"],
        name="Jane Doe",
        email=email,
        linkedin_url="https://www.linkedin.com/in/jane-doe",
        email_verified=True,
        linkedin_verified=True,
        person_verified=True,
    )


def _run_discovery(db, svc):
    """Run one discovery job to completion; return (job row, decoded result)."""
    job = db.create_job("discovery", {"query": "widgets", "count": 1})
    svc._run(job["id"], "widgets", 1)
    finished = db.get_job(job["id"])
    result = finished["result"]
    if isinstance(result, str):
        result = json.loads(result)
    return finished, result


class TestDiscoveryContactConflicts:
    def test_precheck_linkedin_conflict_surfaces_warning(self, db, monkeypatch):
        _company_with_jane(db)

        svc = DiscoveryService(db, EnrichmentService())
        monkeypatch.setattr(svc, "_find_candidates", lambda *_a, **_k: [{
            "name": "NewCo",
            "website": "https://newco.com",
            "domain": "newco.com",
            "reason": "test",
        }])
        monkeypatch.setattr(svc.enrichment, "enrich", lambda *_a, **_k: _enrich_payload())

        finished, result = _run_discovery(db, svc)
        assert finished["status"] == "done"
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
        existing = dict(_company_with_jane(db))
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

        finished, result = _run_discovery(db, svc)
        assert finished["status"] == "done"
        assert result["contacts_added"] == 0
        assert result["results"][0]["contact_conflicts"]
        assert result["results"][0]["status"] == "no_emails_found"
        new = db.find_company_by_name("NewCo")
        assert new["scrape_status"] == "no_emails_found"
        warnings = new.get("scrape_warnings") or []
        assert any("already belongs to OldCo" in w for w in warnings)

    def test_same_company_integrity_fallback_does_not_inflate_count(
            self, db, monkeypatch):
        existing = dict(_company_with_jane(
            db, company="Acme", domain="acme.com",
            email="jane.doe@acme.com"))
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

        _finished, result = _run_discovery(db, svc)
        # Existing row belonged to Acme, not the new company — conflict path.
        # (If somehow same company, _inserted False still blocks the count.)
        assert result["contacts_added"] == 0
