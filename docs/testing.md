# Testing and validation

Run checks from the repository root unless a command says otherwise. The backend
virtual environment is `backend/venv`; frontend dependencies are in
`frontend/node_modules`.

## Environment setup

```bash
./start.sh                         # creates runtime dependencies, then starts both apps
backend/venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm install
```

The development requirements add only Ruff and mypy. Backend pytest uses a temporary
database and stub Gmail paths from `tests/conftest.py`; it does not send email. Do not
use the live API endpoints as a substitute for tests. Frontend tests use Vitest/jsdom.

## Targeted checks

Start with the test nearest the changed symbol:

```bash
make test-backend TEST=unit/backend/test_pipeline.py
make test-backend TEST='unit/backend/test_db.py -k follow_up'
make test-frontend TEST=tests/unit/pipeline.test.jsx
make test-frontend TEST='tests/unit/emails-thread.test.jsx -t quote'
```

Direct equivalents:

```bash
cd tests && ../backend/venv/bin/python -m pytest -q unit/backend/test_pipeline.py
cd frontend && npm test -- tests/unit/pipeline.test.jsx
```

## Package-level checks

```bash
make lint               # conservative Ruff correctness/import rules
make typecheck          # mypy on the currently typed-clean core modules
make test-backend       # all backend tests
make test-frontend      # all frontend tests
make build-frontend     # Vite production build and frontend module check
make check-agent-docs   # canonical docs exist; every adapter pair is identical
```

The type-check target is intentionally scoped to `text_cleaner`, `analytics`,
`pipeline`, `contact_verify`, and `response_checker`. The remaining legacy backend is
not yet mypy-clean. Expand the checked set only when a module passes without suppressing
real errors. The JavaScript frontend has no dedicated type checker; its production
build is the current static module validation. Do not describe either limitation as a
full-repository type-check pass.

## Full validation

```bash
make validate
```

This runs adapter verification, lint, the scoped type check, both test suites, and the
frontend production build. It does not run stress tests, make network calls, use paid
AI, or contact Gmail.

Run the deterministic scraping benchmark only for scraping changes:

```bash
backend/venv/bin/python scripts/evaluate_scraping.py
```

The `--live` benchmark and `stress/` commands require external services or a running
backend and are opt-in. Record them separately from deterministic validation.

## Choosing a tier

- Docs or adapter-only change: adapter check plus review rendered links.
- Pure backend function: focused test, lint, scoped type check if included, then the
  backend suite when shared behavior changed.
- Route/storage/job change: focused tests plus full backend suite.
- Frontend page/component/API change: focused Vitest test plus frontend suite and build.
- Cross-boundary or safety-sensitive change: `make validate` and any focused security,
  send-integrity, prompt-safety, or data-safety tests.

Report each command and outcome. If a check was skipped, unavailable, flaky, or failed
for a pre-existing reason, say so explicitly.
