# Backend tests

Adds to the root [`AGENTS.md`](../AGENTS.md). Tiers:
[`../docs/testing.md`](../docs/testing.md).

Backend tests live in `unit/backend/`; frontend tests live in
`../frontend/tests/unit/`. `conftest.py` points `COLD_DB_PATH` at a temporary
database and stubs the Gmail credential paths — keep that isolation intact and
never reach for real data, live credentials, or a network AI provider.

Start at the test matching the changed module and narrow with `-k`. Add
regression coverage beside the closest existing behavior, then run the package
suite when a shared fixture or contract changed.

```bash
make test-backend TEST='unit/backend/test_db.py -k follow_up'
```
