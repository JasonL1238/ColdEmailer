# Frontend

Adds to the root [`AGENTS.md`](../AGENTS.md). Boundaries:
[`../docs/architecture.md`](../docs/architecture.md).

- Start in the affected page or component. Search the rendered label, handler,
  or state name before opening `Emails.jsx`, `DeepDive.jsx`, or `DatabasePage.jsx`.
- HTTP shapes belong in `src/api.js`; primitives and job polling in `src/ui.jsx`;
  design tokens in `src/styles.css`. Reuse before adding a pattern.
- Tests are Vitest/jsdom in `tests/unit/`. A response-shape change normally needs
  an `api.js` wrapper plus a focused page test.

```bash
npm test -- tests/unit/pipeline.test.jsx
```
