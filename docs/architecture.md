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
  `is_cancelled(db, job_id)` treats a vanished job as cancelled. `SingleSlotJob`
  adds the one-at-a-time discipline for `person_finder` and `deep_research`:
  claim an in-process slot, refuse a second start, and hold the slot until the
  worker thread exits — past a cancel, because the worker is still scraping.
  `discovery` and `generation` deliberately stay off it; their crash handlers
  omit `only_if_running`, so adopting it would change the cancel/finish race.
- `discovery.py`, `deep_research.py`, `person_finder.py`, `generation.py`:
  background job orchestration. `discovery.research_updates()` is the single
  mapping from a scrape result to company columns, including how a wrong-site
  verdict clears stale research. Person-finder candidates are staged in the
  job's `result` JSON and enter `contacts` only through
  `verified_channels()`/`attach_candidate()` at the approve route; a
  user-confirmed non-matching address follows the contact-edit
  reclassification semantics and never gains verified flags.
- `enrichment.py`, `web_scraper.py`, `text_cleaner.py`, `ddg_search.py`: untrusted
  web acquisition, cleanup, identity checks, evidence extraction.
- `found_email.py`, `mailbox_verify.py`, `address_corroborate.py`: pure adapters
  with no `Database`, no FastAPI and no `WebScraper` instance. `person_finder`
  calls all three and `address_corroborate` reuses `found_email.guarded_get_*`;
  their HTTP and SMTP seams remain injectable for offline tests. The last two
  answer different questions about the same address and neither subsumes the
  other: `mailbox_verify` asks a mail server whether the mailbox accepts mail,
  `address_corroborate` asks public corpora whether the address has ever been
  used and by whom. The production mailbox connector resolves public MX socket
  addresses through the shared SSRF guard, tries direct port 25 first, and
  spends a provider lookup only when SMTP is unavailable or inconclusive.
- `contact_ingest.py`, `contact_verify.py`, `contact_enrich.py`: the contact
  boundary. Every scraped or imported contact converges here before persistence,
  and `attach_candidate()` is the one place that decides which scraped channels
  survive, how a cross-company collision is reported, and what actually landed.
- `email_composer.py`, `email_sender.py`, `response_checker.py`, `thread_reader.py`:
  draft, send, reply, and thread duties. Sending and Gmail reads are live I/O.
- `campaigns.py`, `pipeline.py`, `analytics.py`, `suppression.py`, `send_window.py`,
  `research_digest.py`, `mail_domain.py`: focused domain calculations; keep them
  framework-independent. `mail_domain.py` is the one shared path that may
  distinguish a company's employee-mail domain from its website domain.
- `domain_names.py`, `phrase_match.py`, `ttl_cache.py`: stdlib-only leaves that
  import nothing from this app, so anything may import them. They exist because
  the rule each holds had been written out three or four times and had drifted —
  a second `registered_domain` that did not know two-part TLDs made unrelated
  `.co.uk` employers compare equal. Add to the leaf; never re-derive the rule.
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
  into localhost or cloud metadata endpoints. `found_email.guarded_get_*` is the
  second place that re-checks every hop; it exists because `fetch_json` pins
  same-origin and cannot carry an auth header.
- Direct SMTP uses the same public-address rule and connects to the already
  checked socket address, so an MX record cannot redirect a mailbox probe into
  localhost, a private LAN, or cloud metadata.
- The mailbox probe never issues `DATA`/`BDAT` — `mailbox_verify.ALLOWED_VERBS`
  makes it structurally impossible.
- Neither the mailbox probe nor corroboration writes `email_verified` or changes
  `origin`: both answer a question about the address, not about who owns it.
- Corroboration is positive-only. A hit means the address exists in a public
  corpus; a miss means nothing and must never be reported as evidence the
  address is fake.
- A LinkedIn URL supplied to person finder is an identity constraint, not a
  score bonus: only that canonical `/in/` profile survives candidate assembly.
  Loose no-profile evidence never gets merged into a profile. Generic
  GitHub/arXiv/website searches are disabled for anchored runs; source adapters
  can use only a direct channel URL that was already correlated. An
  exact-profile search that explicitly names somebody else is surfaced as a
  conflict and is not stored as a verified LinkedIn channel. Country LinkedIn
  subdomains canonicalize to `www` so they remain first-class profiles.
- A guessed address becomes sendable only through the approve route, and only
  when all of: a mailbox probe returned `deliverable` OR corroboration returned
  `name_match is True`; the address matches the person (`email_person_match`);
  and the caller sent `confirm_email_ownership`. `name_match` is tri-state —
  `None` (no source gave a name) never qualifies, and is not `False` (a source
  gave someone else's name).
- Missing named-person addresses try ten bounded company patterns over
  direct SMTP before Hunter. Before constructing them, normal company
  enrichment and person finder both resolve a separate `mail_domain`; the
  website `domain` remains unchanged for identity, crawling, and persistence.
  Definitive rejections advance to the next pattern;
  the first deliverable result stops without spending Hunter, while blocked,
  greylisted, or catch-all SMTP stops pattern probing and permits the provider
  fallback. Person finder hides an inconclusive guess unless a public corpus
  independently ties that exact address to the target name. A live guessed
  mailbox remains a guess and gains no verified flag.
- The placeholder-address filter applies to the found path only. Mail-domain
  discovery consumes template-dense search evidence through `mail_domain.py`;
  person finder never treats search-result snippets as found-address evidence.
  See decisions.md for the measurement that forbids merging the two evidence
  paths.
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
- Scraping caches are scoped to one crawl, never to the process. `EnrichmentService`
  is a module-level singleton, so a cache that outlived a crawl would let one
  company's results decide another's: `enrich()` creates the page-parse cache and
  calls `reset_dead_api_probes()` at the top of every run. The MX cache is the one
  exception — it is keyed by domain and time-bounded, because the answer is a
  property of the domain rather than of the crawl.
- A cache miss must only ever cost work, never change an answer. The page-parse
  cache is keyed on the html as well as the URL, the dead-API-probe cache on the
  exact URL (so `/api/team` cannot speak for `/api/team/`) and only for 404/410 —
  never for a refusal a server can recover from.
