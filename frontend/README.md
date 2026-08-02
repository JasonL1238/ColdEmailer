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

See [`../docs/architecture.md`](../docs/architecture.md) for frontend boundaries and
[`../docs/repository-map.md`](../docs/repository-map.md) for where screen, API,
component, and style changes belong. Validation tiers are in
[`../docs/testing.md`](../docs/testing.md).
