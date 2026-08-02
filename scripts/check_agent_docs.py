#!/usr/bin/env python3
"""Verify that agent adapters stay paired and point at canonical guidance."""

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = (
    ROOT / "docs" / "agent-guidelines.md",
    ROOT / "docs" / "architecture.md",
    ROOT / "docs" / "repository-map.md",
    ROOT / "docs" / "testing.md",
)


def main() -> int:
    errors: list[str] = []
    for path in CANONICAL:
        if not path.is_file():
            errors.append(f"missing canonical document: {path.relative_to(ROOT)}")

    ignored = {"node_modules", "venv", ".git", "dist", "build", "__pycache__"}
    adapters: list[Path] = []
    for directory, children, files in os.walk(ROOT):
        children[:] = [child for child in children if child not in ignored]
        if "AGENTS.md" in files:
            adapters.append(Path(directory) / "AGENTS.md")
    adapters.sort()
    if not adapters:
        errors.append("no AGENTS.md adapters found")

    for agents_path in adapters:
        claude_path = agents_path.with_name("CLAUDE.md")
        relative = agents_path.relative_to(ROOT)
        if not claude_path.is_file():
            errors.append(f"missing sibling for {relative}: CLAUDE.md")
            continue
        if agents_path.read_bytes() != claude_path.read_bytes():
            errors.append(f"adapter pair differs: {relative.parent}")
        content = agents_path.read_text(encoding="utf-8")
        if "agent-guidelines.md" not in content:
            errors.append(f"adapter does not link canonical rules: {relative}")

    if errors:
        print("Agent documentation check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Agent documentation check passed ({len(adapters)} adapter pairs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
