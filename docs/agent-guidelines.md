# Agent guidelines

These are the canonical instructions for every coding agent in this repository.
Root and nested instruction files are adapters, not separate policy sources.

## Start and explore

- Start with the smallest module that can answer or implement the request.
- Read the nearest `AGENTS.md` or `CLAUDE.md`, then this file. Use
  `repository-map.md`, `architecture.md`, and `testing.md` only as needed.
- Search for filenames and symbols before opening large files. Prefer `rg` and
  narrow line ranges; avoid repo-wide scans unless the task truly crosses boundaries.
- Ignore generated files, logs, coverage, build output, caches, virtual
  environments, vendored code, and raw/local datasets unless directly relevant.
- Check `git status` before editing. Treat existing changes and local data as user-owned.

## Edit

- Reuse existing types, utilities, services, validation paths, and UI patterns.
- Keep edits limited to the requested behavior; do not rewrite unrelated working code.
- Preserve dependency direction described in `architecture.md`. New scraped-contact
  paths must use `backend/contact_ingest.py` rather than write directly to the database.
- Keep generated and handwritten code separate. Do not hand-edit generated output.
- Update architecture, repository-map, or testing docs when their facts change.
- Split large files only behind a clear responsibility seam and with tests protecting
  behavior. A smaller diff is more valuable than speculative cleanup.

## Safety

- `POST /api/emails/send` sends real email. Never call it as a test.
- `POST /api/emails/check-replies` uses the live Gmail API; call it at most once and
  only when the task explicitly requires it.
- `backend/data/coldemailer.db` and ignored legacy data contain real user data. Tests
  must use the temporary database configured by `tests/conftest.py`. If manual test
  rows are unavoidable, prefix them `ZZTEST` and remove them and their events.
- Discovery, enrichment, and generation can spend paid AI quota. Tests should use
  `use_template_only: true` or mocked providers.
- Preserve the product invariants documented in `architecture.md`, especially
  recipient validation, prompt-injection filtering, sent-history retention, honest
  reply verification, and explicit keyless fallbacks.

## Validate

- Run the narrowest relevant test first, then package-level lint/type/build checks,
  then full validation when risk or scope warrants it. See `testing.md`.
- Keep command output concise and inspect failures; do not rerun blindly.
- Never claim a check passed unless the command completed successfully.
- Do not substitute a build or syntax check for a test, lint, or type check.

## Parallel work

- Do not use subagents for simple work.
- Use parallelism only for independent, non-overlapping scopes with clear ownership.
- Give each subagent a narrow deliverable and validation responsibility. The primary
  agent must reconcile shared boundaries and run final checks.

## Complete

- Review the final diff and confirm only intended files changed.
- Report files changed, structural effects, commands run and their results, deferred
  work, and remaining uncertainty.
- Stop only when the requested outcome is implemented and proportionately validated,
  or when a concrete blocker requires user input.
