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

| File | Responsibility |
|---|---|
| `main.py` | All HTTP routes; wires the services together |
| `db.py` | SQLite storage layer + one-time migration from the legacy CSV/JSON files |
| `discovery.py` | Background jobs: natural-language query → real companies → scraped contacts |
| `enrichment.py` | Per-company scraping, email extraction and ranking, LLM metadata |
| `generation.py` | Background jobs: compose a draft per selected contact |
| `email_composer.py` | Email types, prompt construction, offline template fallbacks |
| `resume_service.py` | Resume PDF upload, text extraction, versioning |
| `email_sender.py` | Gmail API send with attachments and recipient safety checks |
| `response_checker.py` | Thread inspection for genuine replies (ignores bounces/auto-replies) |
| `llm_client.py` | Provider abstraction: Gemini, OpenAI, OpenRouter |
| `web_scraper.py` | HTTP fetching with SSRF guards, robots.txt, per-domain rate limiting |
| `ddg_search.py` | DuckDuckGo search wrapper that degrades gracefully |
| `rate_limiter.py` | Send/generation/research caps |
| `models.py` | Pydantic request models and input validators |

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
cd ../tests && ../backend/venv/bin/python -m pytest
```
