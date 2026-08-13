# Repository map

Enter the smallest relevant area before searching broadly.

## Backend — `backend/`

| Path | Purpose | First test to run |
|---|---|---|
| `main.py` | FastAPI app, routes, orchestration | matching `test_*.py` for the route |
| `db.py` | SQLite schema, CRUD, migrations, startup repairs | `test_db.py` + the domain test |
| `models.py` | Pydantic request models and boundary validation | endpoint or `test_security.py` |
| `jobs.py` | Shared background-job cancellation contract | `test_pipeline.py`, job tests |
| `discovery.py` | Company discovery jobs, junk-site filtering | `test_discovery_*.py` |
| `deep_research.py` | Deep company/contact research, criteria, evidence | `test_deep_research.py` |
| `enrichment.py` | Site research, contact extraction and ranking | `test_enrichment.py`, scraping tests |
| `web_scraper.py` | Layered public-page fetching | `test_web_scraper_layers.py`, `test_security.py` |
| `contact_ingest.py` | **The contact boundary** — validation plus company attach | `test_contact_ingest.py`, `test_discovery_conflicts.py` |
| `contact_verify.py`, `contact_enrich.py` | Address/LinkedIn safety, gap filling | `test_contact_*.py` |
| `generation.py`, `email_composer.py` | Draft jobs and composition | `test_email_composer.py`, `test_prompt_safety.py` |
| `email_sender.py` | Gmail send boundary | `test_send_integrity.py` — never call live send |
| `response_checker.py`, `thread_reader.py` | Reply classification, Gmail thread parsing | reply and thread-reader tests |
| `analytics.py`, `campaigns.py`, `pipeline.py`, `suppression.py`, `send_window.py`, `research_digest.py` | Focused domain policies, no framework | the same-named test file |
| `llm_client.py` | Provider-neutral LLM interface | `test_llm_client.py` |

## Frontend — `frontend/src/`

| Path | Purpose | First test to run |
|---|---|---|
| `App.jsx` | Shell, navigation, app context | relevant component test + build |
| `pages/` | Screen workflows | matching `frontend/tests/unit/*.test.jsx` |
| `components/` | Reusable domain components | the importing page test |
| `ui.jsx` | Generic primitives, job polling, shared summaries | all affected component tests |
| `api.js` | HTTP client contract | `api.test.js` |
| `styles.css` | Design tokens | component tests + build + a look |

## Everything else

| Path | Purpose |
|---|---|
| `tests/unit/backend/` | Safe backend unit and route tests |
| `frontend/tests/unit/` | Vitest/jsdom tests |
| `scripts/` | `check_agent_docs.py` (doc integrity), `evaluate_scraping.py` (benchmark), `scrape_corpus.py` + `measure_shipped.py` (offline scraping measurement) |
| `stress/` | Opt-in load probes; needs a live backend. See `stress/README.md` |

## Search, do not open

These files are too large to read whole. Grep first:

- `main.py` — the route path or the route function name.
- `db.py` — the entity method, table, migration, or `repair_*` symbol.
- `deep_research.py` — criteria, scoring, evidence, or the service method.
- `enrichment.py`, `web_scraper.py` — the extraction or fetch layer.
- `pages/Emails.jsx`, `DeepDive.jsx`, `DatabasePage.jsx` — the rendered label,
  handler, state name, or API wrapper.

## Do not read by default

`backend/venv/`, `*/node_modules/`, `frontend/dist/`, caches, logs, coverage,
`backend/data/`, `.env`, `credentials.json`, `token.json`, `resume*.pdf`, and
`skills.md` (the owner's personal bio, not agent skills).

## Shortest commands

```bash
make test-backend TEST=unit/backend/test_pipeline.py
make test-frontend TEST=tests/unit/pipeline.test.jsx
make lint | make typecheck | make build-frontend | make validate
```

Tiers and limitations: [`testing.md`](testing.md).
