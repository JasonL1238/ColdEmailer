# Frontend (React + Vite)

UI for the whole outreach loop: find companies, review what was scraped,
generate emails, send them, and track replies.

## Run

```bash
cd frontend
npm install   # first time
npm run dev
```

App: http://localhost:5173 (backend expected at http://localhost:8000; `/api` is
proxied there by `vite.config.js`).

## Scripts

- `npm run dev` — dev server
- `npm run build` — production build
- `npm run test` — run tests (see `../tests/README.md` for the full test layout)
- `npm run test:watch` — tests in watch mode

## Main pieces

| File | Role |
|---|---|
| `src/App.jsx` | Shell, sidebar nav, shared settings/dashboard context |
| `src/pages/Dashboard.jsx` | Pipeline counts, 30-day activity, follow-ups due, unverified-reply banner |
| `src/pages/Discover.jsx` | Plain-English company search with live job progress |
| `src/pages/DatabasePage.jsx` | Companies and contacts, with a drawer for scraped research and email history |
| `src/pages/Emails.jsx` | Draft review, bulk approve/trash, send, follow-ups, reply state |
| `src/pages/ComposeModal.jsx` | Pick contacts, email type and resume; generate a draft each |
| `src/pages/Resumes.jsx` | Resume versions, default selection |
| `src/pages/Settings.jsx` | Sender profile, Gmail connection, AI provider, limits |
| `src/ui.jsx` | Shared primitives (Button, Chip, Drawer, Modal, Spinner, job polling) |
| `src/api.js` | API client — one wrapper per backend route |
| `src/styles.css` | Whole design system, token-driven |
