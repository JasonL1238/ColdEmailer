# Frontend (React + Vite)

UI for contacts, email review, and sending.

## Run

```bash
cd frontend
npm install   # first time
npm run dev
```

App: http://localhost:5173 (backend expected at http://localhost:8000).

## Scripts

- `npm run dev` — dev server
- `npm run build` — production build
- `npm run test` — run tests (see `tests/README.md` for full test layout)

## Main pieces

- **CSVManager** — Contacts (upload, add, edit, delete), tabs: Emailed / Generated / No emails
- **EmailReview** — Generate emails, accept/trash, settings, send accepted (with optional resume)
- **api.js** — API client for backend
