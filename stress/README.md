# Stress Testing

Load, concurrency, large-input, and memory tests for the backend API.

## Setup

- **k6** (load tests): `brew install k6` (macOS) or see [k6.io](https://k6.io/)
- **Node:** `cd stress && npm install`

## Commands

| Command | Description |
|--------|-------------|
| `npm run stress:light` | Light load (10 users), k6 |
| `npm run stress:medium` | Medium load (50 users) |
| `npm run stress:heavy` | Heavy load (200 users) |
| `npm run concurrency` | Concurrency safety (unique IDs, no overwrites) |
| `npm run large-input` | Large payloads (100 chars – 1M chars) |
| `npm run memory-leak` | Memory over ~5 min (set `BACKEND_PID` optional) |

Backend must be running at http://localhost:8000 (or set `BASE_URL`).

## Slow API simulation

In backend `.env`: `STRESS_TEST_MODE=true`. Backend then adds random 0–5s delay and ~10% failure rate.

## Expected behavior

- **Load:** Light &lt;500ms, medium &lt;1s, heavy may time out
- **Concurrency:** All requests succeed, unique contact IDs
- **Large input:** Small/medium OK; large payloads get 400/413, no crash
- **Memory:** Stabilizes; warning if &gt;20% growth over run
