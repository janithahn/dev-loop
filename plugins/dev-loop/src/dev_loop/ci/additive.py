"""Tests stay additive: what a change to test files took away or switched off.

`removed(before, after)` compares the test files' texts before and after (a list each, so a test
moved to another file is no removal). It reports fewer test names (`def test_`, `it(`, `test(`; a
name gone with a new one in its place is a rename), fewer rows in a literal `parametrize` list,
fewer checks (`assert...`, `expect(`, `raises(`, `.should`), more markers that switch a test off,
and an added bare `return` or `if False`. Whole-line comments are dropped first, so a
commented-out test counts as removed.

`check(root, base)` is `dl ci additive-tests`: the same comparison over every file matching
`fix_proves.tests` that differs between the merge base with <base> and HEAD, so a test taken away
by `git rm`, `rm` or `sed -i`, which no edit hook sees, is caught. It is no gate step of the CI
templates: a project runs it before a push by naming it in `verify.steps` and `verify.prepush`.
Tests: tests/test_additive_tests.py (the hook), tests/test_ci_additive.py.

ponytail: patterns, not a parser per language; a string that holds `assert` counts as a check.
"""

from __future__ import annotations

import re
import subprocess
import sys
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

from dev_loop import config

if TYPE_CHECKING:
    from pathlib import Path

COMMENT = re.compile(r"^[ \t]*(?:#|//)[^\n]*|^[ \t]*/\*.*?\*/", re.MULTILINE | re.DOTALL)
NAMES = re.compile(r"\bdef (test_\w+)|\b(?:it|test)(?:\.\w+)?\(\s*(['\"`].*?['\"`])")
CHECKS = re.compile(r"\bassert\w*|\bexpect\(|\braises\(|\.should\b")
EXEMPT = re.compile(  # the markers that switch a test off, never the bare word in a name
    r"@pytest\.mark\.(?:skip|xfail)|\bpytest\.(?:skip|xfail)\(|\bunittest\.skip|\bexpectedFailure\b"
    r"|\.(?:skip|only|todo)\(|\bx(?:it|test|describe)\(|\bt\.Skip"
)
PARAMETRIZE = re.compile(r"\bparametrize\(")
PIECES = re.compile(  # a string, a comment, an opening or closing bracket, a comma
    r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|#[^\n]*|[(\[{]|[)\]}]|,""", re.DOTALL
)
DEAD = re.compile(r"\bif False\b|\bif \(false\)|^[ \t]*return;?[ \t]*$", re.MULTILINE)
MESSAGE = (
    "tests are additive only; if this removal is deliberate, say why in the commit and the PR"
)


def git(folder: Path, *args: str) -> str | None:
    done = subprocess.run(  # noqa: S603 - git, fixed arguments
        ["git", "-C", str(folder), *args],  # noqa: S607
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return done.stdout if done.returncode == 0 else None


def matches(rel: str, globs: list[str]) -> bool:
    """Match as fix_proves.sh does: `*` crosses folders, a leading `**/` also the top level."""
    return any(fnmatchcase(rel, glob) or (glob.startswith("**/") and fnmatchcase(rel, glob[3:]))
               for glob in globs)


def top_level(text: str) -> list[str]:
    """Split text at its commas outside brackets and strings, up to the bracket that closes it."""
    parts: list[str] = []
    depth = start = 0
    for piece in PIECES.finditer(text):
        token = piece.group()
        if token in "([{":
            depth += 1
        elif token in ")]}" and depth:
            depth -= 1
        elif token in ")]}":
            return [*parts, text[start:piece.start()]]
        elif token == "," and not depth:
            parts.append(text[start:piece.start()])
            start = piece.end()
    return [*parts, text[start:]]


def rows(text: str) -> int:
    """Count the rows of every literal `parametrize(names, [rows])` list in the text."""
    total = 0
    for call in PARAMETRIZE.finditer(text):
        args = top_level(text[call.end():])
        values = args[1].strip() if len(args) > 1 else ""
        if values[:1] in {"[", "("}:
            total += sum(bool(row.strip()) for row in top_level(values[1:]))
    return total


def measure(texts: list[str]) -> tuple[set[str], list[int]]:
    """Return the test names and the counts of checks, off markers, dead code and param rows."""
    clean = [COMMENT.sub("", text) for text in texts]
    names = {m.group(1) or m.group(2) for text in clean for m in NAMES.finditer(text)}
    counts = [sum(len(p.findall(text)) for text in clean) for p in (CHECKS, EXEMPT, DEAD)]
    return names, [*counts, sum(map(rows, clean))]


def removed(before: list[str], after: list[str]) -> list[str]:
    """Return what the change took away or switched off, one phrase each."""
    (old, was), (new, now) = measure(before), measure(after)
    said = []
    if len(old - new) > len(new - old) or now[3] < was[3]:  # a renamed test is no removal
        said.append("removes a test")
    if now[0] < was[0]:
        said.append("removes an assert")
    if now[1] > was[1]:
        said.append("adds a skip, xfail or only")
    if now[2] > was[2]:
        said.append("adds an early return or an if False")
    return said


def check(root: Path, base: str) -> int:
    """`dl ci additive-tests`: exit 1 naming what base..HEAD took away; not run is exit 1 too."""
    globs = config.load(root).get("fix_proves.tests") or []
    start = (git(root, "merge-base", base, "HEAD") or "").strip()
    if not globs or not start:
        why = f"no merge base with {base}" if globs else "fix_proves.tests is empty"
        print(f"additive-tests: not run: {why}", file=sys.stderr)
        return 1
    listed = git(root, "diff", "--name-only", "--no-renames", "--relative", start, "HEAD") or ""
    files = [rel for rel in listed.splitlines() if matches(rel, globs)]
    before = [git(root, "show", f"{start}:./{rel}") or "" for rel in files]
    after = [git(root, "show", f"HEAD:./{rel}") or "" for rel in files]
    if said := removed(before, after):
        print(f"additive-tests: {start[:12]}..HEAD {' and '.join(said)}; {MESSAGE}")
        return 1
    print(f"additive-tests: additive ({len(files)} changed test files)")
    return 0
