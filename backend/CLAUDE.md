# Backend

Adds to the root [`AGENTS.md`](../AGENTS.md). Boundaries:
[`../docs/architecture.md`](../docs/architecture.md).

- Imports are intentionally bare (`from db import ...`); modules run with
  `backend/` on `sys.path`. Do not add package prefixes.
- `main.py` (3k lines) and `db.py` (2.3k) are search targets, not read targets:
  grep the route path, table, entity method, or `repair_*` symbol.
- Scraped and imported contacts converge on `contact_ingest.py`. Attaching a
  candidate to a company goes through `verified_channels()` then
  `attach_candidate()`, so discovery, deep research, and re-research cannot
  drift apart on which channels survive or how a collision is reported.
- A scrape result becomes company columns through `discovery.research_updates()`.
- Background jobs (`discovery`, `generation`, `deep_research`) share the
  cancellation contract in `jobs.py`.
