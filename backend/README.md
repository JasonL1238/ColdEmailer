# Backend (FastAPI)

API for company discovery, contact database, resume versions, email generation, Gmail sending, and reply tracking.

## Run

```bash
cd backend
venv/bin/python -m uvicorn main:app --reload --port 8000
```

API: http://localhost:8000 · Interactive docs: http://localhost:8000/docs

Normally you don't run this directly — `./start.sh` from the project root starts backend and frontend together.

## Modules

See [`../docs/architecture.md`](../docs/architecture.md) for component and dependency
boundaries and [`../docs/repository-map.md`](../docs/repository-map.md) for module-level
change routing. Those documents are canonical so this runtime README does not duplicate
an inventory that can drift.

## Data

Everything lives in `data/coldemailer.db` (SQLite, WAL mode): companies, contacts, resumes, emails, jobs, settings, events. Uploaded resume PDFs are in `data/resumes/`. Both are gitignored.

Long operations run as background threads that report progress through the `jobs` table; the frontend polls `GET /api/jobs/{id}`.

## Security notes

- Scraping validates every URL (and redirect hop) resolves to a public IP — no SSRF into localhost or cloud metadata endpoints.
- Recipient addresses are validated at ingest and again at send time, so a scraped or imported string can never smuggle extra recipients into a `To:` header.
- State-changing requests from a non-allowlisted `Origin` are rejected, so a random website can't drive your local backend.
- API keys are read from the environment only, never logged or returned to the frontend.

## Tests

```bash
make test-backend
```

Targeted and full validation commands are in
[`../docs/testing.md`](../docs/testing.md).
