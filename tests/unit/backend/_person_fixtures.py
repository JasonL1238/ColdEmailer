"""Person-finder doubles shared by two test modules.

test_guards_are_wired.py used to import these out of test_person_finder.py by
package path, which loaded that module a second time under a second identity —
`test_person_finder` and `tests.unit.backend.test_person_finder` are different
objects, so its body ran twice and its classes existed twice. The leading
underscore keeps this file out of collection (`python_files = test_*.py`).
"""


class FakeScraper:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.fetched = []

    def fetch_html(self, url):
        self.fetched.append(url)
        return self.pages.get(url)

    def extract_text(self, html):
        return html


class FakeEnrichment:
    def __init__(self, website=None, pages=None):
        self.scraper = FakeScraper(pages)
        self._website = website

    def find_website(self, _name):
        return self._website


def _staged_job(db, *, emails=None, linkedin="https://www.linkedin.com/in/jane-doe",
                company=None):
    """A finished person_finder job with one staged candidate."""
    candidate = {
        "id": "c1",
        "name": "Jane Doe",
        "role": "VP Engineering",
        "linkedin_url": linkedin,
        "linkedin_verified": bool(linkedin),
        "source_url": linkedin or "https://acme.com/team",
        "confidence": "strong",
        "score": 5.0,
        "matched_signals": [{"signal": "company:Acme",
                             "evidence": "VP Engineering at Acme",
                             "source_url": "https://acme.com/team"}],
        "conflicting_signals": [],
        "evidence": [{"source_url": "https://acme.com/team",
                      "snippet": "Jane Doe leads engineering at Acme"}],
        "channels": [{"kind": "github", "url": "https://github.com/janedoe"}],
        "emails": emails if emails is not None else [{
            "email": "jane.doe@acme.com", "origin": "found",
            "domain_kind": "company", "source_url": "https://acme.com/team",
            "email_kind": "personal", "email_person_match": True,
            "email_mx_ok": True, "email_verified": True,
        }, {
            "email": "jdoe@acme.com", "origin": "guessed",
            "domain_kind": "company", "source_url": None,
            "email_kind": "pattern_guess", "email_person_match": True,
            "email_mx_ok": True, "email_verified": False,
        }],
        "llm_summary": None,
        "approved_contact_id": None,
    }
    job = db.create_job("person_finder", {"name": "Jane Doe"})
    db.finish_job(job["id"], status="done", result={
        "query": {"name": "Jane Doe", "company_name": "Acme"},
        "company": company or {"company_id": None, "name": "Acme",
                               "domain": "acme.com",
                               "url": "https://acme.com"},
        "candidates": [candidate],
        "keyless": True, "timed_out": False, "searches_used": 3,
    })
    return job["id"]
