# Backend (FastAPI)

API for contacts, company enrichment, email generation (Ollama), and sending (Gmail).

## Run

```bash
cd backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

API: http://localhost:8000 · Docs: http://localhost:8000/docs

## Configuration (.env)

Paths are resolved from **project root** when relative:

- `CSV_FILE_PATH` — contacts CSV (default: `data/contacts.csv` under backend dir or project)
- `COMPANY_CACHE_PATH` — company cache JSON
- `EMAIL_STORAGE_PATH` — generated emails JSON
- `RESUME_PATH` — resume PDF for generation context; if missing, `resume28.pdf` / `resume29.pdf` in project root are tried
- `SKILLS_FILE_PATH` — background/qualifications (default: `skills.md` in project root). Cold emails use a **fixed template**; the only variable sentence about you comes from `skills.md`: either the line under `## Email one-liner` or the first sentence of the first `## Experience` block.

Ollama: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`.  
Rate limits: `MAX_EMAILS_PER_DAY`, `MAX_EMAIL_GENERATIONS_PER_MINUTE`, `MAX_COMPANY_RESEARCH_PER_MINUTE`, `EMAIL_SEND_DELAY_SECONDS`.  
Gmail: `credentials.json` and `token.json` in project root (see root README).

## Main modules

- `main.py` — FastAPI app, routes, service wiring
- `csv_processor.py` — Contact CSV read/write
- `email_generator.py` — Template-based cold email body (company name, company details, one experience sentence from skills.md); no LLM for body. Follow-up emails still use Ollama.
- `email_sender.py` — Gmail API send, optional resume attachment
- `email_storage.py` — Generated emails persistence
- `rate_limiter.py` — Per-minute and daily limits
- `company_enrichment_service.py` — Company research + cache

## Tests

From project root: `pytest tests/` (see `tests/README.md`).
