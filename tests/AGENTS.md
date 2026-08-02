# Test agent adapter

Read [`../docs/agent-guidelines.md`](../docs/agent-guidelines.md) and
[`../docs/testing.md`](../docs/testing.md).

- Start with the test matching the changed module and use `-k` or `-t` to narrow it.
- Backend tests must keep `tests/conftest.py` isolation intact; never point at real data,
  Gmail credentials, or network AI providers.
- Add regression coverage beside the closest existing behavior, then run the relevant
  package suite when a shared fixture or contract changes.
