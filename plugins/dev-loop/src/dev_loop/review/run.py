"""`dl review`: one PR review round (pr-review.sh), and the reviewer's context from the config.

    dl review                          # runs the round with the env pr-review.sh documents
    dl review context <touched>...     # prints the system prompt: standards, then governance
    dl review fix-brief <change> <thread>...  # a fix builder's brief; threads JSON on stdin

Every path is relative to the project root and read live on each run:
  standards   the plugin's standards.base.txt, then the `review.standards` rubric when set (a named
              rubric that is absent is said in the text, never skipped silently);
  governance  the `review.governance` globs; each `review.extra` path whose glob a touched file
              matches; the `knowledge.pitfalls` file; each `knowledge.guides` file whose folder
              holds a touched file; the nearest CLAUDE.md or AGENTS.md above each touched file
              (cut at 20000 characters, with a marker). A glob's `*` crosses folders.
A refused dev-loop.yaml stops the round (exit 1): the review never runs on the defaults unsaid.
"""

from __future__ import annotations

import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

from ..config import load, project_root

HERE = Path(__file__).resolve().parent
BASE = HERE / "standards.base.txt"
SCRIPT = HERE / "pr-review.sh"
GUIDE_NAMES = ("CLAUDE.md", "AGENTS.md")
GUIDE_CAP = 20_000  # characters per area guide; a longer one is cut with a marker


def governance(root: Path, touched: list[str]) -> list[Path]:
    """Return the governance files a review of `touched` inlines, in order, each once."""
    values = load(root).values
    review, knowledge = values["review"], values["knowledge"]
    found = [path for glob in review["governance"] for path in sorted(root.glob(glob))]
    found += [
        root / path
        for glob, path in review["extra"].items()
        if any(fnmatchcase(name, glob) for name in touched)
    ]
    found.append(root / knowledge["pitfalls"])
    for glob in knowledge["guides"]:
        found += [
            guide
            for guide in sorted(root.glob(glob))
            if any(Path(name).is_relative_to(guide.parent.relative_to(root)) for name in touched)
        ]
    found += nearest_guides(root, touched)
    return [path for path in dict.fromkeys(found) if path.is_file()]


def nearest_guides(root: Path, touched: list[str]) -> list[Path]:
    """Return, for each touched file, the closest CLAUDE.md or AGENTS.md up to the root, once."""
    found: list[Path] = []
    for name in touched:
        for folder in [*(root / name).parents][: len(Path(name).parts)]:
            guide = next((folder / g for g in GUIDE_NAMES if (folder / g).is_file()), None)
            if guide:
                found.append(guide)
                break
    return found


def section(root: Path, path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name in GUIDE_NAMES and len(text) > GUIDE_CAP:
        text = f"{text[:GUIDE_CAP]}\n[guide truncated at {GUIDE_CAP} characters]"
    return f"\n----- {path.relative_to(root).as_posix()} -----\n{text}"


def context(root: Path, touched: list[str]) -> str:
    """Return the reviewer's system prompt for a PR touching `touched`."""
    parts = [BASE.read_text(encoding="utf-8")]
    if rubric := load(root).values["review"]["standards"]:
        path = root / rubric
        parts.append(section(root, path) if path.is_file() else f"\n----- {rubric}: missing -----")
    parts.append("\n===== Repo governance (inlined; source of truth, current as of this run) =====")
    parts += [section(root, path) for path in governance(root, touched)]
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    if argv[:1] == ["fix-brief"]:
        from . import fix_brief  # late: fix_brief imports this module

        return fix_brief.main(argv[1:])
    if argv[:1] != ["context"]:
        return subprocess.run(["bash", str(SCRIPT), *argv], check=False).returncode  # noqa: S603, S607
    try:
        root = project_root()
    except ValueError as error:
        print(f"dl review: {error}", file=sys.stderr)
        return 2
    if error := load(root).error:
        print(f"dl review: {error}", file=sys.stderr)
        return 1
    print(context(root, argv[1:]))
    return 0
