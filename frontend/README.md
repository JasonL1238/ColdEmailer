# Frontend (React + Vite)

UI for the whole outreach loop: find companies, review what was scraped, generate
emails, send them, track replies.

```bash
cd frontend
npm install       # first time
npm run dev       # also: build | preview | test | test:watch
```

App on http://localhost:5173. The backend is expected at http://localhost:8000;
`vite.config.js` proxies `/api` there.

Frontend boundaries are in [`../docs/architecture.md`](../docs/architecture.md),
change routing in [`../docs/map.md`](../docs/map.md), and validation tiers in
[`../docs/testing.md`](../docs/testing.md).
