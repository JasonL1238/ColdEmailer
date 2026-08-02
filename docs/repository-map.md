# Repository map

Use this map to enter the smallest relevant area before searching broadly.

| Path | Purpose | Common changes | First validation |
|---|---|---|---|
| `backend/main.py` | FastAPI app, routes, orchestration | Endpoint wiring, HTTP errors, job launch | Matching `tests/unit/backend/test_*.py` |
| `backend/db.py` | SQLite schema, CRUD, migration, repairs | Persistence and lifecycle rules | `test_db.py` plus domain-specific DB test |
| `backend/models.py` | Pydantic request models | Input constraints and request shape | Endpoint or security test |
| `backend/discovery.py` | Company discovery jobs | Search result filtering and job progress | `test_discovery_*.py` |
| `backend/deep_research.py` | Deep company/contact research | Criteria parsing, evidence, contact hunting | `test_deep_research.py` |
| `backend/enrichment.py` | Site research and contact extraction | Company identity, email extraction/ranking | `test_enrichment.py`, scraping tests |
| `backend/web_scraper.py` | Layered public-page fetching | SSRF, redirects, JSON/HTML/Playwright layers | `test_web_scraper_layers.py`, `test_security.py` |
| `backend/contact_*.py` | Contact ingest, verification, enrichment | Address/LinkedIn safety and gap filling | Corresponding `test_contact_*.py` |
| `backend/generation.py`, `email_composer.py` | Draft jobs and composition | Prompt/template behavior | `test_email_composer.py`, prompt tests |
| `backend/email_sender.py` | Gmail send boundary | MIME, attachment, recipient safety | Send integrity tests; never call live send |
| `backend/response_checker.py`, `thread_reader.py` | Reply classification and Gmail thread parsing | Reply/bounce/quote logic | Reply and thread-reader tests |
| `backend/{analytics,campaigns,pipeline,suppression,send_window}.py` | Focused domain policies | Pure calculations and state rules | Same-named test file |
| `frontend/src/App.jsx` | Shell, navigation, app context | New page wiring, global state | Relevant component test + build |
| `frontend/src/pages/` | Screen workflows | Page behavior and state | Matching `frontend/tests/unit/*.test.jsx` |
| `frontend/src/components/` | Reusable domain components | Shared company/contact UI | Importing page/component tests |
| `frontend/src/ui.jsx` | Generic UI primitives and job polling | Cross-page UI behavior | All affected component tests |
| `frontend/src/api.js` | HTTP client contract | Endpoint paths/payloads | `frontend/tests/unit/api.test.js` |
| `frontend/src/styles.css` | Shared design tokens and styles | Visual/layout changes | Component tests + build + manual review |
| `tests/unit/backend/` | Safe backend unit/route tests | Backend regression coverage | Target file, then backend suite |
| `frontend/tests/unit/` | Vitest/jsdom tests | Frontend regression coverage | Target file, then frontend suite |
| `scripts/` | Maintenance and deterministic checks | Agent-doc verification, scraping benchmark | Run the changed script directly |
| `stress/` | Opt-in load and concurrency probes | Performance investigations | See `stress/README.md`; requires live backend |

## Files to avoid by default

Do not inspect or edit `backend/venv/`, `frontend/node_modules/`, `stress/node_modules/`,
`frontend/dist/`, caches, logs, coverage output, `backend/data/`, `.env`, Gmail
credentials/tokens, PDFs, or legacy raw data unless the task specifically requires it.

## Oversized-file navigation

Search before opening these files in full:

- `backend/main.py`: search for the route path or route function.
- `backend/db.py`: search for the entity method, table, migration, or `repair_*` symbol.
- `backend/deep_research.py`: search for criteria, scoring, evidence, or service method.
- `backend/enrichment.py` and `web_scraper.py`: search for the extraction/fetch layer.
- `frontend/src/pages/Emails.jsx`, `DeepDive.jsx`, and `DatabasePage.jsx`: search for
  the rendered label, handler, state name, or API wrapper.

See `testing.md` for command tiers. The shortest common commands are:

```bash
make test-backend TEST=unit/backend/test_pipeline.py
make test-frontend TEST=tests/unit/pipeline.test.jsx
make lint
make typecheck
make build-frontend
```
