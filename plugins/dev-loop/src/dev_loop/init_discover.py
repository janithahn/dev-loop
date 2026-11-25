"""`dl init discover`: find the project's docs and set knowledge.read_first and guides.

read_first: the root CLAUDE.md, AGENTS.md, ARCHITECTURE.md and CONTRIBUTING.md, every
`*conventions*.md`, and `docs/adr/` when it holds a record. guides: every CLAUDE.md or AGENTS.md
below the root. Each key is written once, appended to dev-loop.yaml so its comments stay; a key
the project file already holds, even an empty list, is never overwritten. Prints paths only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

import yaml

from .config import PROJECT, load, project_root
from .init import git

ROOT_DOCS = ("CLAUDE.md", "AGENTS.md", "ARCHITECTURE.md", "CONTRIBUTING.md")
GUIDES = {"CLAUDE.md", "AGENTS.md"}
SKIP = {"node_modules", "vendor", "dist", "build", "archive", "templates", ".dev-loop"}
KEYS = ("read_first", "guides")


def files(root: Path) -> list[str]:
    """Return the project's Markdown files, tracked or not ignored, outside skipped folders."""
    listed = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md")
    paths = {line for line in listed.splitlines() if line}
    return sorted(p for p in paths if not SKIP & set(PurePosixPath(p).parts[:-1]))


def discover(root: Path) -> dict[str, list[str]]:
    """Return the read_first and guides lists found under root."""
    found = files(root)
    conventions = [p for p in found if "conventions" in PurePosixPath(p).name.lower()]
    adr = ["docs/adr/"] if any(p.startswith("docs/adr/") for p in found) else []
    return {
        "read_first": [p for p in ROOT_DOCS if p in found] + conventions + adr,
        "guides": [p for p in found if "/" in p and PurePosixPath(p).name in GUIDES],
    }


def held(path: Path) -> dict[str, object]:
    """Return the project file's knowledge mapping; empty when the file or section is absent."""
    if not path.exists():
        return {}
    project = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(project.get("knowledge") or {})


def apply(root: Path) -> list[str]:
    """Write each found key the project file does not hold; return the dotted keys written."""
    path = root / PROJECT
    have = held(path)
    found = discover(root)
    new = {key: found[key] for key in KEYS if key not in have and found[key]}
    if not new:
        return []
    old = text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = "".join(f"  {key}: {json.dumps(value)}\n" for key, value in new.items())
    section = re.search(r"^knowledge:[ \t]*(#.*)?\n", text, flags=re.MULTILINE)
    if section:
        text = text[: section.end()] + lines + text[section.end() :]
    elif "knowledge" in (yaml.safe_load(text) or {}):
        msg = f"{PROJECT}: set knowledge.read_first and knowledge.guides by hand (flow style)"
        raise ValueError(msg)
    else:
        text = text + ("" if text.endswith("\n") or not text else "\n") + "knowledge:\n" + lines
    path.write_text(text, encoding="utf-8", newline="\n")
    error = load(root).error
    if error:  # the file as it was; nothing half-written stays
        path.write_text(old, encoding="utf-8", newline="\n")
        raise ValueError(error)
    return [f"knowledge.{key}" for key in new]


def main(argv: list[str]) -> int:
    del argv
    try:
        written = apply(project_root())
    except (OSError, ValueError) as error:
        print(f"dl init discover: {error}")
        return 1
    print("\n".join(f"set {key}" for key in written) or "knowledge keys already set")
    return 0
