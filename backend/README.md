# Backend (FastAPI)

API for company discovery, the contact database, resume versions, email
generation, Gmail sending, and reply tracking.

Normally you don't run this directly — `./start.sh` from the project root starts
the backend and frontend together.

```bash
cd backend
venv/bin/python -m uvicorn main:app --reload --port 8000
```

API on http://localhost:8000, interactive docs at http://localhost:8000/docs.

Everything persists to `data/coldemailer.db` (SQLite, WAL) and uploaded resume
PDFs to `data/resumes/` — both gitignored real user data. The table inventory is
in [`../docs/architecture.md`](../docs/architecture.md#data-flow).

Module responsibilities and the security invariants this API enforces are in
[`../docs/architecture.md`](../docs/architecture.md); where to make a given change
is in [`../docs/map.md`](../docs/map.md); test commands are in
[`../docs/testing.md`](../docs/testing.md).
