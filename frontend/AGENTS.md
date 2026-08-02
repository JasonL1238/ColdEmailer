# Frontend agent adapter

Read [`../docs/agent-guidelines.md`](../docs/agent-guidelines.md), then the frontend
boundaries in [`../docs/architecture.md`](../docs/architecture.md) and validation in
[`../docs/testing.md`](../docs/testing.md).

- Start in the affected page or component; keep HTTP request shapes in `src/api.js`.
- Reuse `src/ui.jsx` primitives and `src/styles.css` tokens before adding patterns.
- Search labels, handlers, or state names before opening the largest page files.
- Run the focused Vitest file, then the frontend suite and production build as needed.
