#!/usr/bin/env python3
"""Keep the agent-facing docs honest.

Three failure modes, all of which have happened: a canonical doc gets renamed and
the routing table still points at the old name; an `AGENTS.md` is edited and its
`CLAUDE.md` twin is not, so Codex and Claude Code read different policies; a doc
is deleted and every link to it rots silently.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ("map.md", "architecture.md", "testing.md", "decisions.md")
IGNORED_DIRS = {"node_modules", "venv", ".git", "dist", "build", "__pycache__"}
# `[text](target)`, skipping images and anything with a scheme or an anchor-only
# target. Trailing `#section` is stripped before the file is checked.
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def markdown_files() -> list[Path]:
    found = []
    for directory, children, files in os.walk(ROOT):
        children[:] = [c for c in children if c not in IGNORED_DIRS]
        found.extend(Path(directory) / f for f in files if f.endswith(".md"))
    return sorted(found)


def main() -> int:
    errors: list[str] = []
    docs = ROOT / "docs"

    for name in CANONICAL:
        if not (docs / name).is_file():
            errors.append(f"missing canonical document: docs/{name}")

    adapters = [p for p in markdown_files() if p.name == "AGENTS.md"]
    if not adapters:
        errors.append("no AGENTS.md adapters found")

    for agents_path in adapters:
        relative = agents_path.relative_to(ROOT)
        claude_path = agents_path.with_name("CLAUDE.md")
        if not claude_path.is_file():
            errors.append(f"missing sibling for {relative}: CLAUDE.md")
        elif agents_path.read_bytes() != claude_path.read_bytes():
            errors.append(f"adapter pair differs: {relative.parent}")

    root_agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for name in CANONICAL:
        if f"docs/{name}" not in root_agents:
            errors.append(f"root AGENTS.md does not route to docs/{name}")

    for path in markdown_files():
        for target in LINK_RE.findall(path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            resolved = (path.parent / target.split("#", 1)[0]).resolve()
            if not resolved.exists():
                errors.append(
                    f"dead link in {path.relative_to(ROOT)}: {target}")

    if errors:
        print("Agent documentation check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Agent documentation check passed "
          f"({len(adapters)} adapter pairs, {len(markdown_files())} files linked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
