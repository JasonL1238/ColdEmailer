# Agent instructions

Reach — a local cold-outreach app. React/Vite SPA → FastAPI → one SQLite file.
`CLAUDE.md` is a byte-identical copy of the `AGENTS.md` beside it, so this is the
policy for Claude Code, Codex, Cursor, and anything else that reads either name.
Edit one; `make check-agent-docs` verifies the pair.

This file is the whole prerequisite. Everything below the routing table is
reference you open only when a row applies.

## Never

- **`POST /api/emails/send` sends real mail to real people.** Never call it, for
  any reason, including "just to check". `POST /api/emails/check-replies` hits
  the live Gmail API — at most once, only when the task requires it.
- **`backend/data/` is real user data.** Tests use the temp database from
  `tests/conftest.py`. If a manual row is unavoidable, prefix it `ZZTEST` and
  delete it along with its `events` rows.
- **Discovery, enrichment, and generation spend paid AI quota.** Use
  `use_template_only: true` or mock the provider.
- **Never report a check as passing unless you ran it and it succeeded.** A
  build is not a substitute for a test, a lint, or a type check.

## Where to look

| When you are… | Open |
|---|---|
| locating code, or about to open a 1,000-line file | [`docs/map.md`](docs/map.md) |
| crossing a module boundary or touching an invariant | [`docs/architecture.md`](docs/architecture.md) |
| deciding what to run before you claim done | [`docs/testing.md`](docs/testing.md) |
| about to "fix" something that looks wrong | [`docs/decisions.md`](docs/decisions.md) |

`backend/`, `frontend/`, and `tests/` each add a short local `AGENTS.md`.

## How to work here

- Enter through the smallest module that can answer the request. Search for the
  route path, symbol, or rendered label before opening a large file.
- Reuse the existing type, helper, or validation path. Every scraped or imported
  contact goes through `backend/contact_ingest.py`, never straight to the DB.
- Keep SQL in `db.py` and FastAPI out of the pure calculation modules.
- Run the narrowest relevant test first and widen by risk; `make validate` is the
  ceiling. `make help` lists the shortest commands.
- Update the doc whose facts you changed: `map.md` for layout, `architecture.md`
  for boundaries and invariants, `testing.md` for commands, `decisions.md` for a
  choice a later reader would mistake for a bug. Then run `make check-agent-docs`.

## Do not read unless the task names them

`backend/venv/`, `*/node_modules/`, `frontend/dist/`, `stress/`, caches and logs,
`backend/data/`, `.env`, `credentials.json`, `token.json`, `resume*.pdf`, and
`skills.md` — which is the owner's personal bio, not an agent-skills file.
