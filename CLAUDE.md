# Repository agent instructions

This file is intentionally identical to its sibling adapter and is compatible
with Codex, Claude Code, Cursor, and other coding agents.

Before working:

1. Read the nearest nested instruction file, if one exists.
2. Read [`docs/agent-guidelines.md`](docs/agent-guidelines.md); it is canonical.
3. Use [`docs/repository-map.md`](docs/repository-map.md) to locate changes,
   [`docs/architecture.md`](docs/architecture.md) for boundaries, and
   [`docs/testing.md`](docs/testing.md) for validation.

Do not test live Gmail send/reply endpoints, mutate `backend/data/`, or spend AI
quota. Verify adapter parity with `make check-agent-docs`.
