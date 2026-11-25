"""`dl review fix-brief <change> <thread>...`: a fix builder's brief, the review's standard in it.

    echo '{"pr": <id>}' | dl host threads --json | dl review fix-brief <change> <thread>...

Stdin is the thread list; each named thread must be a finding (a `crid:` footer). The reviewer's
context (`dl review context` of the finding files) is written under the git dir and listed in
Read first; Decisions holds the proposal's limits, Done when the spec bullets naming those files.
Exit 2 on bad input or a thread that is not a finding, 1 on a refused config or git failing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from functools import partial
from pathlib import Path

from ..config import load, project_root
from ..hosts import cli
from ..records.record import changes_dir
from .review_verdict import spec_lines
from .run import context

silent_stdin = partial(cli.silent_stdin, 120)  # longer than the host listing (up to 60 s)

FOOTER = re.compile(r"`crid:[0-9a-f]+`\s*$")
SEVERITY = re.compile(r"\s*\*\*(blocker|major|minor)\*\*")
LIMITS = re.compile(r"#{1,6}\s.*(out of scope|guardrail)", re.IGNORECASE)
RULES = """- One new test function per thread, on the input it names; red first.
- Tests are only added: never weaken, skip or delete one.
- Change only what the finding needs.
- Every rule in the context file in Read first holds for the fix."""


class Refused(Exception):
    """Bad input: said on stderr, exit 2."""


def findings(raw: str, ids: list[str]) -> list[dict]:
    """Return the named threads from the `dl host threads --json` list, each a finding."""
    try:
        threads = {str(t["id"]): t for t in json.loads(raw)}
    except (ValueError, TypeError, KeyError) as error:
        raise Refused(f"stdin is not the thread list: {error}") from error
    if not ids:
        raise Refused("no thread named")
    for tid in ids:
        if tid not in threads:
            raise Refused(f"thread {tid} is not in the input")
        comments = threads[tid].get("comments") or [{}]
        if not FOOTER.search(comments[0].get("content") or ""):
            raise Refused(f"thread {tid} has no crid: footer (a person's comment, not a finding)")
    return [threads[tid] for tid in ids]


def where(thread: dict) -> tuple[str, str]:
    """Return the thread's file (leading / dropped, '' when none) and its `file:line` label."""
    at = thread.get("threadContext") or {}
    path = (at.get("filePath") or "").lstrip("/")
    line = (at.get("rightFileStart") or {}).get("line")
    return path, f"{path or 'no file'}{f':{line}' if line else ''}"


def goal(threads: list[dict], cut: list[str]) -> str:
    lines = []
    for thread in threads:
        text = FOOTER.sub("", thread["comments"][0]["content"])
        severity = SEVERITY.search(text)  # a posted finding starts with an emoji
        rated = severity[1] if severity else "unrated"
        lines.append(f"Fix review thread {thread['id']} ({where(thread)[1]}, {rated}): "
                     f"{' '.join(text.split())}")
    if cut:
        lines.append(f"(+{len(cut)} more files: {', '.join(cut)})")
    return "\n".join(lines)


def limits(proposal: Path) -> str:
    """Return the `- ` bullets under the proposal's out-of-scope or guardrail headings."""
    found, inside = [], False
    text = proposal.read_text(encoding="utf-8") if proposal.is_file() else ""
    for line in text.splitlines():
        if line.startswith("#"):
            inside = bool(LIMITS.match(line))
        elif inside and line.startswith("- "):
            found.append(line)
    return "\n".join([*found, "- A guardrail is never undone to silence a finding."])


def done_when(threads: list[dict], files: list[str], spec: Path) -> str:
    lines = [f"- Thread {t['id']}: its new test fails before the fix and passes after."
             for t in threads]
    text = spec.read_text(encoding="utf-8") if spec.is_file() else ""
    tails = {"/".join(parts[i:]) for parts in (f.split("/") for f in files)
             for i in range(len(parts))}  # a path names its file from any of its suffixes
    named = [f"- {line}" for line in spec_lines(text)
             if any(re.search(rf"(?<![\w./-]){re.escape(t)}(?![\w/-])", line) for t in tails)]
    return "\n".join(lines + (named or [
        f"- every acceptance bullet of spec.md still holds (dl verify {spec.parent.name} green)"]))


def write_context(root: Path, files: list[str], first: str) -> Path:
    """Write the reviewer's context for `files` under the git dir; CalledProcessError on git."""
    name = f"dl-fix-brief-{first}.md"
    out = subprocess.run(["git", "rev-parse", "--git-path", name],
                         cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    path = root / out
    path.write_text(context(root, files), encoding="utf-8")
    return path


def brief(root: Path, change: str, threads: list[dict]) -> str:
    get = load(root).get
    files = list(dict.fromkeys(p for p in (where(t)[0] for t in threads) if p))
    keep = max(get("briefs.max_read_first") - 1, 0)
    saved = write_context(root, files, str(threads[0]["id"]))
    folder = changes_dir(root) / change
    read = [f"- {name}" for name in files[:keep]]
    read.append(f"- {saved}: the review's standard and governance for these files; the next "
                "round judges the fix by it.")
    text = {"goal": goal(threads, files[keep:]), "read first": "\n".join(read),
            "decisions": limits(folder / "proposal.md"), "rules": RULES,
            "done when": done_when(threads, files, folder / "spec.md")}
    heads = list(get("briefs.sections"))
    heads += [h for h in ("Decisions", "Rules") if h.lower() not in {x.lower() for x in heads}]
    out = [f"Change: {change}\n"] if get("briefs.change_line") else []
    out += [f"## {head}\n{text.get(head.lower(), 'none')}\n" for head in heads]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    try:
        root = project_root()
        if error := load(root).error:
            print(f"dl review fix-brief: {error}", file=sys.stderr)
            return 1
        if not argv:
            raise Refused("usage: dl review fix-brief <change> <thread>...")
        if silent_stdin():
            raise Refused("no thread list on stdin after 120 s")
        print(brief(root, argv[0], findings(sys.stdin.read(), argv[1:])))
    except (Refused, ValueError) as error:
        print(f"dl review fix-brief: {error}", file=sys.stderr)
        return 2
    except (subprocess.CalledProcessError, OSError) as error:
        print(f"dl review fix-brief: git or the context file failed, no brief: {error}",
              file=sys.stderr)
        return 1
    return 0
