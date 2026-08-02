# Architecture

Reach is a local React/Vite single-page app backed by FastAPI and one SQLite
database. The browser talks only to `/api`; Vite proxies that path to the backend in
development.

## Components and entry points

| Boundary | Entry point | Responsibility |
|---|---|---|
| Frontend | `frontend/src/main.jsx` → `App.jsx` | Navigation, app context, page composition |
| Frontend API | `frontend/src/api.js` | One client wrapper per backend API area |
| Backend HTTP | `backend/main.py` (`app`) | Middleware, service wiring, routes, job launch |
| Persistence | `backend/db.py` (`Database`) | Schema, SQLite CRUD, migrations, startup repairs |
| Local runtime | `start.sh` | Starts FastAPI on 8000 and Vite on 5173 |
| Tests | `tests/conftest.py` | Isolates backend imports and points storage at a temp DB |

Backend modules use bare imports (`from db import ...`) and expect `backend/` on
`sys.path`; do not introduce package-prefixed imports without a deliberate migration.

## Data flow

```text
React page
  -> frontend/src/api.js
  -> FastAPI route in backend/main.py
  -> domain service / validation module
  -> Database in backend/db.py
  -> backend/data/coldemailer.db

Long operation
  -> route creates jobs row and starts a background thread
  -> discovery / generation / deep research / send service updates jobs row
  -> frontend polls GET /api/jobs/{id}
```

The database holds settings, companies, contacts, resumes, emails, campaigns,
suppressions, jobs, and events. Resume PDFs live under `backend/data/resumes/`.
Both are local user data and are gitignored.

## Backend responsibility boundaries

- `main.py`: transport concerns and orchestration. It currently also contains send,
  follow-up, import/export, and enrichment workflows; prefer extracting tested domain
  logic to a focused module instead of adding another large route helper.
- `models.py`: request schemas and boundary validation.
- `db.py`: storage only, plus legacy migration and idempotent startup repairs. Keep SQL
  out of routes when a reusable database operation exists.
- `discovery.py`, `deep_research.py`, `generation.py`: background job orchestration.
- `enrichment.py`, `web_scraper.py`, `text_cleaner.py`, `ddg_search.py`: untrusted web
  acquisition, cleanup, identity checks, and evidence extraction.
- `contact_ingest.py`, `contact_verify.py`, `contact_enrich.py`: the contact-validation
  boundary. All scraped/imported contact paths converge here before persistence.
- `email_composer.py`, `email_sender.py`, `response_checker.py`, `thread_reader.py`:
  draft, send, and reply/thread responsibilities. Sending and Gmail reads are live I/O.
- `campaigns.py`, `pipeline.py`, `analytics.py`, `suppression.py`, `send_window.py`,
  `research_digest.py`: focused domain calculations; keep them framework-independent.
- `llm_client.py`: provider-neutral LLM interface for Gemini, OpenAI, and OpenRouter.
  Callers own honest template or refusal fallbacks.

## Frontend responsibility boundaries

- `App.jsx` owns global context and page selection.
- `pages/` owns screen-level state and workflows; `components/` owns reusable domain UI.
- `ui.jsx` contains shared primitives and job polling; `api.js` owns HTTP request shapes;
  `config.js` owns runtime API configuration; `styles.css` is the shared design system.
- Backend response-shape changes normally require a matching `api.js` wrapper and a
  focused page/component test.

## Dependency rules and invariants

- UI depends on the API contract, not backend implementation modules.
- HTTP routes depend on Pydantic models and domain services; services may depend on
  `Database`, but pure calculation modules should stay independent of FastAPI.
- Scraped text is untrusted data and must remain fenced and injection-filtered.
- Contact addresses are validated at ingest and again before send.
- Sent history is durable and prevents duplicate first contact; deletion safeguards
  and force semantics are intentional.
- Reply metrics distinguish verified replies from unverified legacy flags.
- Keyless operation must use a supported template or explicit refusal; it must not
  silently pretend AI output exists.
- Startup `repair_*` calls are idempotent migrations and must remain safe to rerun.
