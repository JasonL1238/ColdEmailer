# Handoff notes

State as of the latest commit on `main`. Paste the prompt at the bottom into a
new session, or just point it at this file.

## What this is

"Reach" — a cold-outreach app. Search for companies in plain English, scrape
their sites for contact emails and talking points, generate a tailored email
per contact (application / coffee chat / sales / custom), send from Gmail,
track replies and follow-ups.

Rebuilt from a CSV/JSON pipeline onto SQLite. Architecture and setup are in
`README.md`; per-module responsibilities in `backend/README.md`.

## Current state

- **Green:** 1088 backend tests (`cd tests && ../backend/venv/bin/python -m pytest`),
  225 frontend tests (`cd frontend && npm test`), clean `vite build`.
- **Working tree clean**, `main` == `origin/main`.
- **Run it:** `./start.sh` → app on :5173, API on :8000.
- **Real user data** in `backend/data/coldemailer.db`: 115 companies,
  122 contacts, 161 emails (128 sent), 2 resumes. Gitignored.

## Things a new session must know before touching anything

1. **There is a live Gmail token.** `POST /api/emails/send` sends real email to
   real people. Never call it to "test" something. `POST /api/emails/check-replies`
   hits the Gmail API — call it at most once.
2. **Discovery and generation burn paid Gemini quota.** For testing, use
   `use_template_only: true` (the "Skip AI" checkbox), which makes no network
   calls at all.
3. **Don't mutate the real rows.** Prefix test data `ZZTEST` and delete it
   afterwards, including rows in the `events` table.
4. **The test suite is safe** — `tests/conftest.py` points `COLD_DB_PATH` at a
   temp database and stubs the Gmail paths, so pytest never touches real data.

## The twelve-item roadmap is finished

All twelve shipped, each one built → attacked by adversarial reviewers → every
confirmed finding fixed → every guard mutation-verified (reverted in a scratch
copy, confirmed a test fails) → both suites and `vite build` green → committed.
Items 4 and 10 touch the send path and got two rounds each.

| # | What | Where |
|---|------|-------|
| 1 | Person-level email finding (Hunter + pattern guess) | `contact_enrich.py` |
| 2 | Address verification at send time, bounce handling | `contact_verify.py`, `response_checker.py` |
| 3 | Multi-step follow-up cadences | `db.py` cadence fns, `main.py` |
| 4 | Send scheduling — business hours + timezone | `send_window.py` |
| 5 | Loop-closing analytics | `analytics.py`, `pages/Analytics.jsx` |
| 6 | Read reply threads in-app | `thread_reader.py`, `ThreadPane` |
| 7 | Pipeline board of contact stages | `pipeline.py`, `pages/Pipeline.jsx` |
| 8 | Keyboard-driven draft review | `pages/Emails.jsx` |
| 9 | Campaigns as a first-class object | `campaigns.py`, `pages/Campaigns.jsx` |
| 10 | Suppression / do-not-contact list | `suppression.py` |
| 11 | LLM provider resilience and quota surfacing | `llm_client.py` |
| 12 | Repo hygiene | — |

**Item 4 ships disabled.** The send window is off by default and needs both a
Settings toggle and a per-batch opt-in before any mail leaves unattended.

## Deliberate design decisions — don't "fix" these

- **The do-not-contact list is the only check that fails closed.** Every other
  gate is optimistic — `domain_has_mx` returning "could not check" lets a send
  proceed, so a DNS blip cannot block real mail. Suppression is the opposite:
  if it cannot be evaluated, nothing goes out. Keep it that way.
- **Nothing is backfilled into a campaign.** Rows predating campaigns stay
  `campaign_id IS NULL` forever and are reported as their own bucket. Guessing
  which campaign an old company belonged to would invent the fact the page
  exists to report.
- **The pipeline board derives stages from evidence, not `contacts.status`.**
  That column is written from four places and reset from none, so a contact
  whose draft was deleted stays `drafted` for good.
- **A rate is withheld below `analytics.MIN_SAMPLE` (10 sends)** on every
  surface — analytics, campaigns, the unassigned bucket — and counts are shown
  instead. `MIN_SAMPLE` and `rate_of` are imported, never re-declared.
- **There is no keyboard shortcut for sending, and the help says so.** Every
  other shortcut is reversible; a delivered email is not.

- **Reply rate shows 0% with "115 unverified".** This is correct. The old
  checker counted bounces, auto-replies and the user's own thread messages as
  replies, producing a fake 89.8%. Those flags now sit in
  `emails.response_verified_at IS NULL` and are reported separately. Clicking
  "Re-verify replies" re-checks against Gmail and promotes the genuine ones.
- **Sent emails cannot be deleted; companies/contacts with sent history need
  `?force=true`.** That record is what prevents emailing someone twice.
- **`custom` email type refuses the offline template** (raises
  `TemplateUnavailable`) rather than emitting an internship email that ignores
  the user's instructions.
- **Some companies show "Wrong site found".** Their scraped profile came from
  an unrelated website (e.g. "Sabanto" → an Epson printer-ink page) and was
  cleared rather than left to be quoted into an email. Re-research fixes them.
- **Startup runs idempotent repairs** in `main.py` (`repair_*` functions in
  `db.py`). They are safe to re-run and should stay.

## Known open items

1. **`resume28.pdf` / `resume29.pdf` are committed to a PUBLIC repo** and
   contain the owner's phone number and email. They predate the `*.pdf`
   gitignore rule (added in `bcad104`), so the rule never applied. Untracking
   them leaves them in history; a real purge needs `git filter-repo` + force
   push, or making the repo private. **Owner's decision — do not act
   unilaterally.**
2. **Commit `bcad104` is titled "email gen now with gemni key."** If a real key
   was ever committed, it should be rotated. Not audited.
3. Legacy migration reads `backend/backend/data/` (a nested duplicate path).
   Harmless, but odd — could be cleaned up once no one needs re-migration.
4. **`google-generativeai` is deprecated** upstream in favour of `google-genai`.
   It still works (the app runs on it today), but the SDK will stop getting
   fixes. Migration touches only `llm_client.py`.
5. Keyless discovery quality is mediocre — web search alone returns VC firms
   and startup directories alongside real companies. `discovery.py` has
   `AGGREGATOR_DOMAINS` and `is_junk_site()` to filter these; both could be
   extended.

## Suggested prompt for the next session

> This is "Reach", a cold-email app at ~/Documents/GitHub/ColdEmailer. Read
> HANDOFF.md and README.md first.
>
> Critical: there's a live Gmail token and 122 real contacts in the database.
> Never call POST /api/emails/send or send real email. Use
> `use_template_only: true` for any generation test (no API calls, no quota).
> Prefix any test data ZZTEST and clean it up.
>
> Everything is committed and green: 1088 backend + 225 frontend tests pass.
> Start by running `./start.sh` and both test suites to confirm that still
> holds, then read the "Known open items" section of HANDOFF.md.
>
> The twelve-item roadmap is complete — see the table in HANDOFF.md.
>
> <then state what you actually want, e.g.:>
> - "Add <feature>."
> - "Deploy this somewhere."
> - "Run another adversarial round over <area>."
