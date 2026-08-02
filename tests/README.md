# Tests

Backend tests live in `unit/backend/`; frontend tests live in
`../frontend/tests/unit/`. The backend fixture in `conftest.py` points
`COLD_DB_PATH` at a temporary database and stubs Gmail credential paths.

From the repository root, start with a focused test, then expand according to risk:

```bash
make test-backend TEST=unit/backend/test_db.py
make test-frontend TEST=tests/unit/api.test.js
make validate
```

The canonical environment setup, validation tiers, lint/type/build limitations, and
full command list are in [`../docs/testing.md`](../docs/testing.md). Module-to-test
routing is in [`../docs/repository-map.md`](../docs/repository-map.md).
