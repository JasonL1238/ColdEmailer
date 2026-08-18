# Repository map

Enter the smallest relevant area before searching broadly.

## Backend — `backend/`

| Path | Purpose | First test to run |
|---|---|---|
| `main.py` | FastAPI app, routes, orchestration | matching `test_*.py` for the route |
| `db.py` | SQLite schema, CRUD, migrations, startup repairs | `test_db.py` + the domain test |
| `models.py` | Pydantic request models and boundary validation | endpoint or `test_security.py` |
| `jobs.py` | Shared background-job cancellation contract, plus the `SingleSlotJob` one-at-a-time mixin | `test_pipeline.py`, job tests |
| `discovery.py` | Company discovery jobs, junk-site filtering | `test_discovery_*.py` |
| `deep_research.py` | Deep company/contact research, criteria, evidence | `test_deep_research.py` |
| `person_finder.py` | Find-one-person search, staged review, approval into the contact boundary | `test_person_finder.py` |
| `found_email.py` | Self-published address sources (GitHub commits, arXiv, EDGAR) behind an injectable, SSRF-guarded HTTP seam | `test_found_email.py` |
| `mailbox_verify.py` | "Does this mailbox exist" over SMTP or an HTTPS provider; never sends mail | `test_mailbox_verify.py` |
| `address_corroborate.py` | "Has this exact address ever been used, and by whom" against free public corpora | `test_address_corroborate.py` |
| `enrichment.py` | Site research, contact extraction and ranking | `test_enrichment.py`, scraping tests |
| `web_scraper.py` | Layered public-page fetching | `test_web_scraper_layers.py`, `test_security.py` |
| `contact_ingest.py` | **The contact boundary** — validation plus company attach | `test_contact_ingest.py`, `test_discovery_conflicts.py` |
| `contact_verify.py`, `contact_enrich.py` | Address/LinkedIn safety, gap filling | `test_contact_*.py` |
| `generation.py`, `email_composer.py` | Draft jobs and composition | `test_email_composer.py`, `test_prompt_safety.py` |
| `email_sender.py` | Gmail send boundary | `test_send_integrity.py` — never call live send |
| `response_checker.py`, `thread_reader.py` | Reply classification, Gmail thread parsing | reply and thread-reader tests |
| `analytics.py`, `campaigns.py`, `pipeline.py`, `suppression.py`, `send_window.py`, `research_digest.py`, `rate_limiter.py`, `text_cleaner.py` | Focused domain policies and helpers, no framework | the same-named test file |
| `ddg_search.py`, `resume_service.py`, `linkedin_outreach.py` | Search adapter, resume PDF storage, draft-and-copy LinkedIn message | `test_deep_research.py`; `test_send_integrity.py`; `test_linkedin_outreach.py` |
| `domain_names.py`, `phrase_match.py`, `ttl_cache.py` | Stdlib-only leaves: company-name/domain normalisation, token and phrase boundary matching, the one TTL cache. Import these rather than re-deriving the rule | the importing module's test |
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
| `scripts/` | `check_agent_docs.py` (doc integrity), `evaluate_scraping.py` (benchmark), `scrape_corpus.py` + `measure_shipped.py` (offline scraping measurement), `probe_smtp_feasibility.py` (is outbound TCP/25 open here), `pre-commit-secrets.sh` (opt-in git hook), `corpus_sites.txt` (the 30-site corpus list) |
| `stress/` | Opt-in k6 load probes; needs a live backend, and is on the do-not-read list until a task names it. [`../stress/README.md`](../stress/README.md) |

## Search, do not open

These files are too large to read whole. Grep first:

- `main.py` — the route path or the route function name.
- `db.py` — the entity method, table, migration, or `repair_*` symbol.
- `deep_research.py` — criteria, scoring, evidence, or the service method.
- `enrichment.py`, `web_scraper.py` — the extraction or fetch layer.
- `person_finder.py`, `found_email.py` — the stage name, the source adapter, or
  the budget constant.
- `pages/Emails.jsx`, `DeepDive.jsx`, `DatabasePage.jsx` — the rendered label,
  handler, state name, or API wrapper.

Paths not to open at all: the list in [`../AGENTS.md`](../AGENTS.md).

Commands, tiers, and what the checks do not cover: [`testing.md`](testing.md).
