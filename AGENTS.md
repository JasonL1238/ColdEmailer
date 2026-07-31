# CLAUDE.md

Guidance for Claude Code (and any other agent) working in this repository.

## This file and AGENTS.md are the same file

`CLAUDE.md` and `AGENTS.md` in this repo must stay **byte-identical**. Edit
whichever one you like — a global `PostToolUse` hook
(`~/.claude/hooks/sync-agents-md.sh`) copies it over the other automatically.
Never hand-edit both in one turn; make the change once and let it mirror.

## What this is

"Reach" — a cold-outreach app. Search for companies in plain English, scrape
their sites for contact emails and talking points, generate a tailored email
per contact (application / coffee chat / sales / custom), send from Gmail,
track replies and follow-ups.

React + Vite SPA on a FastAPI backend, everything in one SQLite database.
User-facing setup lives in `README.md`; per-module notes in `backend/README.md`;
current state and open items in `HANDOFF.md`.

## Safety rules — read before running anything

1. **There is a live Gmail token.** `POST /api/emails/send` sends real email to
   real people. Never call it to "test" something. `POST /api/emails/check-replies`
   hits the Gmail API — call it at most once.
2. **The database holds real user data** (`backend/data/coldemailer.db`,
   gitignored): real companies, contacts, and sent mail. Don't mutate real rows.
   Prefix any test data `ZZTEST` and delete it afterwards, including rows in the
   `events` table.
3. **Discovery and generation burn paid Gemini quota.** For testing, pass
   `use_template_only: true` (the "Skip AI" checkbox) — it makes no network
   calls at all.
4. **The test suite is safe.** `tests/conftest.py` points `COLD_DB_PATH` at a
   temp database and stubs the Gmail paths, so pytest never touches real data or
   credentials.

## Commands

```bash
./start.sh                                            # backend :8000 + frontend :5173
```

```bash
cd tests && ../backend/venv/bin/python -m pytest      # backend tests
```

```bash
cd frontend && npm test                               # frontend tests (vitest)
```

```bash
cd frontend && npm run build                          # vite production build
```

```bash
backend/venv/bin/python scripts/evaluate_scraping.py  # deterministic scraping benchmark
```

The venv is `backend/venv` and is created on first `./start.sh`. Backend logs go
to `/tmp/reach_backend.log`, frontend to `/tmp/reach_frontend.log`.

## Layout

```
frontend/  React + Vite SPA
  src/pages/      Dashboard, Discover, DatabasePage, DeepDive, Emails,
                  Resumes, Settings, ComposeModal
  src/ui.jsx      shared primitives (Modal, Drawer, Chip, job polling hook)
  src/api.js      API client
  src/styles.css  design system
  tests/unit/     vitest suites

backend/   FastAPI
  main.py              all HTTP routes; wires the services together
  db.py                SQLite layer + legacy CSV/JSON migration + repair_* fns
  models.py            Pydantic request models and input validators
  discovery.py         background company-discovery jobs
  enrichment.py        per-company scraping, email extraction, LLM metadata
  deep_research.py     deep intel + criteria-matched contact hunting
  contact_enrich.py    post-scrape gap filling (LinkedIn search, email lookup)
  contact_verify.py    person-level email/LinkedIn verification
  contact_ingest.py    shared validation for contacts entering the DB
  generation.py        background email-generation jobs
  email_composer.py    email types, prompts, offline template fallbacks
  email_sender.py      Gmail send with attachments + recipient safety checks
  response_checker.py  genuine-reply detection
  resume_service.py    resume PDF upload/parse/versioning
  linkedin_outreach.py drafts LinkedIn messages (never logs in or sends)
  llm_client.py        provider abstraction (Gemini / OpenAI / OpenRouter)
  web_scraper.py       layered fetch (JSON API → HTTPX → Playwright), SSRF
                       guards, per-domain rate limits. No robots.txt.
  text_cleaner.py      strips nav/footer boilerplate from scraped text
  ddg_search.py        DuckDuckGo wrapper that degrades gracefully
  rate_limiter.py      send/generation/research caps

tests/     backend pytest suites (tests/unit/, conftest.py sets COLD_DB_PATH)
scripts/   evaluate_scraping.py, pre-commit-secrets.sh
stress/    k6 load tests and concurrency/memory probes
```

Long operations (discovery, generation, deep research, sending) run as
background threads that report progress through the `jobs` table; the frontend
polls `GET /api/jobs/{id}`. Nothing blocks the browser.

## Design decisions — don't "fix" these

- **Reply rate shows 0% with unverified counts alongside it.** Correct. The old
  checker counted bounces, auto-replies and the user's own thread messages as
  replies, producing a fake ~90%. Those now sit in
  `emails.response_verified_at IS NULL` and are reported separately.
- **Sent emails cannot be deleted**; companies/contacts with sent history need
  `?force=true`. That record is what prevents emailing someone twice.
- **The `custom` email type refuses the offline template** (raises
  `TemplateUnavailable`) rather than emitting an internship email that ignores
  the user's instructions.
- **Some companies show "Wrong site found."** Their scraped profile came from an
  unrelated website and was cleared rather than quoted into an email.
  Re-research fixes them; don't invent a profile.
- **Startup runs idempotent repairs** (`repair_*` in `db.py`, called from
  `main.py`). Safe to re-run — keep them.
- **Scraped text is untrusted input.** It is fenced as data in prompts, and a
  page that tries to inject instructions has its text dropped. Don't relax this.
- **Addresses are validated twice** — at ingest and again at send — so a comma
  or newline can never add a recipient or a `Bcc:` header.

## Conventions

- Backend is plain modules importing each other by bare name (`from db import …`)
  and run with `backend/` as cwd — no package prefix.
- Keyless operation is a supported mode, not a degraded accident: every AI path
  needs a template or explicit-refusal fallback, and the UI shows which mode is
  active.
- New scraped-contact paths go through `contact_ingest.py` for validation rather
  than writing to the DB directly.
