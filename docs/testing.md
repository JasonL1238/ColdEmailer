# Testing and validation

Run from the repository root. The backend virtual environment is `backend/venv`;
frontend dependencies are in `frontend/node_modules`.

```bash
./start.sh                                              # creates both, then runs the app
backend/venv/bin/pip install -r backend/requirements-dev.txt   # adds Ruff + mypy
cd frontend && npm install
```

Backend pytest uses a temporary database and stub Gmail paths from
`tests/conftest.py`; it does not send email. Frontend tests are Vitest/jsdom.
Live API endpoints are never a substitute for a test.

## Targeted first

```bash
make test-backend TEST=unit/backend/test_pipeline.py
make test-backend TEST='unit/backend/test_db.py -k follow_up'
make test-frontend TEST=tests/unit/pipeline.test.jsx
make test-frontend TEST='tests/unit/emails-thread.test.jsx -t quote'
```

Without `make`:

```bash
cd tests && ../backend/venv/bin/python -m pytest -q unit/backend/test_pipeline.py
cd frontend && npm test -- tests/unit/pipeline.test.jsx
```

## Package level

```bash
make lint               # conservative Ruff correctness/import rules
make typecheck          # mypy over the currently typed-clean core modules
make test-backend       # all backend tests
make test-frontend      # all frontend tests
make build-frontend     # Vite production build
make check-agent-docs   # canonical docs exist, adapters pair, no dead links
make validate           # all of the above
```

`make validate` runs no stress tests, makes no network calls, spends no AI quota,
and never contacts Gmail.

## What the checks do not cover

The type check is scoped to `text_cleaner`, `analytics`, `pipeline`,
`contact_verify`, `response_checker`, and `jobs`. The rest of the backend is not
yet mypy-clean; expand the set only when a module passes without suppressing real
errors. The JavaScript frontend has no type checker — its production build is the
only static module validation. Do not describe either as a full-repository
type check.

## Choosing a tier

| Change | Run |
|---|---|
| Docs or adapters | `make check-agent-docs`, then read the rendered links |
| Pure backend function | focused test, `make lint`, `make typecheck` if in scope |
| Route, storage, or job | focused tests plus the full backend suite |
| Frontend page/component/API | focused Vitest test, frontend suite, build |
| Cross-boundary or safety-sensitive | `make validate` plus the focused security, send-integrity, prompt-safety, and data-safety tests |

Report each command and its outcome. If a check was skipped, unavailable, flaky,
or failed for a pre-existing reason, say so.

## Opt-in, recorded separately

```bash
backend/venv/bin/python scripts/evaluate_scraping.py           # deterministic
backend/venv/bin/python scripts/evaluate_scraping.py --live …  # hits real sites
```

`stress/` needs a running backend. See [`../stress/README.md`](../stress/README.md).

## Measuring a scraping change against real sites

Claims about extraction quality or crawl cost are settled by replaying a capture
of ~30 real sites, not by reasoning about the code — several confident readings
of it turned out to be wrong (see the scraping section of
[`decisions.md`](decisions.md)). The corpus is gitignored and regenerable:

```bash
backend/venv/bin/python scripts/scrape_corpus.py capture   # ~300MB, one polite pass
```

Then diff the working tree against any baseline. The measurement runs each
version in its own subprocess, so the two copies of the module cannot collide:

```bash
git archive HEAD backend | tar -x -C /tmp/base
backend/venv/bin/python scripts/measure_shipped.py /tmp/base/backend
```

It reports addresses extracted, machine-generated addresses rejected, MX lookups
per refresh pass, links discovered, and sitemap people-page coverage. No network,
no AI quota, no database. Regressions for everything it measures live in
`tests/unit/backend/test_scraping_efficiency.py`.
