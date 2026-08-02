# Backend agent adapter

Read [`../docs/agent-guidelines.md`](../docs/agent-guidelines.md), then the backend
boundaries in [`../docs/architecture.md`](../docs/architecture.md) and validation in
[`../docs/testing.md`](../docs/testing.md).

- Search for a route, service, or database symbol before opening `main.py` or `db.py`.
- Run backend modules with `backend/` on `sys.path`; imports are intentionally bare.
- Route new contact inputs through `contact_ingest.py` and preserve send/data guards.
- Use the temp-database pytest suite. Never call live Gmail endpoints or mutate
  `data/`; avoid paid AI by using template mode or mocks.
