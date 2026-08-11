# Architecture

Reach is a local React/Vite single-page app backed by FastAPI and one SQLite
database. The browser talks only to `/api`; Vite proxies that path to the backend
in development. Module-level routing is in [`map.md`](map.md).

## Entry points

| Boundary | Entry point | Responsibility |
|---|---|---|
| Frontend | `frontend/src/main.jsx` → `App.jsx` | Navigation, app context, page composition |
| Frontend API | `frontend/src/api.js` | One client wrapper per backend API area |
| Backend HTTP | `backend/main.py` (`app`) | Middleware, service wiring, routes, job launch |
| Persistence | `backend/db.py` (`Database`) | Schema, CRUD, migrations, startup repairs |
| Local runtime | `start.sh` | FastAPI on 8000, Vite on 5173 |
| Tests | `tests/conftest.py` | Isolates backend imports, points storage at a temp DB |

Backend modules use bare imports (`from db import ...`) and expect `backend/` on
`sys.path`. Do not introduce package-prefixed imports without a deliberate migration.

## Data flow

```text
React page
  -> frontend/src/api.js
  -> FastAPI route in backend/main.py
  -> domain service / validation module
  -> Database in backend/db.py
  -> backend/data/coldemailer.db

Long operation
  -> route creates a jobs row and starts a background thread
  -> discovery / generation / deep research / send service updates that row
  -> frontend polls GET /api/jobs/{id}
```

The database holds settings, companies, contacts, resumes, emails, campaigns,
suppressions, jobs, and events. Resume PDFs live under `backend/data/resumes/`.
Both are local user data and are gitignored.

## Backend boundaries

- `main.py`: transport and orchestration. It still contains send, follow-up,
  import/export, and enrichment workflows; prefer extracting tested domain logic
  to a focused module over adding another large route helper.
- `models.py`: request schemas and boundary validation.
- `db.py`: storage only, plus legacy migration and idempotent startup repairs.
  Keep SQL out of routes when a reusable database operation exists.
- `jobs.py`: the cancellation contract shared by every background service —
  `cancel(db, job_id, job_type)` refuses to kill a job of the wrong type, and
  `is_cancelled(db, job_id)` treats a vanished job as cancelled.
- `discovery.py`, `deep_research.py`, `generation.py`: background job orchestration.
  `discovery.research_updates()` is the single mapping from a scrape result to
  company columns, including how a wrong-site verdict clears stale research.
- `enrichment.py`, `web_scraper.py`, `text_cleaner.py`, `ddg_search.py`: untrusted
  web acquisition, cleanup, identity checks, evidence extraction.
- `contact_ingest.py`, `contact_verify.py`, `contact_enrich.py`: the contact
  boundary. Every scraped or imported contact converges here before persistence,
  and `attach_candidate()` is the one place that decides which scraped channels
  survive, how a cross-company collision is reported, and what actually landed.
- `email_composer.py`, `email_sender.py`, `response_checker.py`, `thread_reader.py`:
  draft, send, reply, and thread duties. Sending and Gmail reads are live I/O.
- `campaigns.py`, `pipeline.py`, `analytics.py`, `suppression.py`, `send_window.py`,
  `research_digest.py`: focused domain calculations; keep them framework-independent.
- `llm_client.py`: provider-neutral interface for Gemini, OpenAI, and OpenRouter.
  Callers own honest template or refusal fallbacks.

## Frontend boundaries

- `App.jsx` owns global context and page selection.
- `pages/` owns screen state and workflows; `components/` owns reusable domain UI.
- `ui.jsx` holds shared primitives, job polling, and cross-page result summaries;
  `api.js` owns HTTP request shapes; `config.js` owns runtime API configuration;
  `styles.css` is the shared design system.
- A backend response-shape change normally needs a matching `api.js` wrapper and a
  focused page or component test.

## Dependency rules

- UI depends on the API contract, not on backend implementation modules.
- Routes depend on Pydantic models and domain services; services may depend on
  `Database`, but pure calculation modules stay independent of FastAPI.

## Invariants

Behavioral rules that tests exist to protect. The reasoning behind the ones that
read as bugs is in [`decisions.md`](decisions.md).

- Scraped text is untrusted data: fenced in prompts and injection-filtered.
- Every scraped URL and every redirect hop must resolve to a public IP — no SSRF
  into localhost or cloud metadata endpoints.
- Contact addresses are validated at ingest and again before send, so a comma or
  newline can never add a recipient or a `Bcc:` header.
- State-changing requests from a non-allowlisted `Origin` are rejected, so another
  website cannot drive the local backend.
- API keys are read from the environment only, never logged, never returned to
  the frontend.
- Sent history is durable and prevents duplicate first contact; deletion
  safeguards and force semantics are intentional.
- Reply metrics distinguish verified replies from unverified legacy flags.
- Keyless operation uses a supported template or an explicit refusal; it never
  pretends AI output exists.
- Startup `repair_*` calls are idempotent and must stay safe to rerun.
