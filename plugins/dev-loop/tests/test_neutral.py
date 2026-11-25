"""Neutrality guard: no private name, internal provenance, address or branch default in the plugin.

Other tests call `check(paths)` on the files they wrote; it returns the offending paths.
The private names are never in this repository: they are read from the file named by
DEV_LOOP_PRIVATE_NAMES, else ~/.config/dev-loop/private-names.txt (one case-insensitive regex
per line, `#` comments). Without that file only the generic rules below hold.
KNOWN lists accepted offenders; it is empty, and a stale entry fails.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pytest is imported only to run the tests: the tools import check alone
    import pytest

ROOT = Path(__file__).resolve().parents[3]
SELF = "plugins/dev-loop/tests/test_neutral.py"
PRIVATE_DEFAULT = Path.home() / ".config" / "dev-loop" / "private-names.txt"

DENY = [
    # `develop` used as a default branch value
    re.compile(r"(default|integration|base|origin/)\w*[\W]{0,4}develop\b", re.IGNORECASE),
    re.compile(r"[:=]\s*[\"']?develop[\"']?\s*$", re.MULTILINE),
    # any email address except noreply ones
    re.compile(r"\b(?!noreply@)[\w.+-]+@[\w-]+(\.[\w-]+)+"),
    # an IPv4 address other than loopback and any-address
    re.compile(r"(?<![\w.])(?!(127\.0\.0\.1|0\.0\.0\.0)(?![\w.]))(\d{1,3}\.){3}\d{1,3}(?![\w.])"),
]

# Shipped code and docs state a rule or its reason, never the review thread or dated decision
# it came from, nor an id from the origin project's planning (a requirement, roadmap row, plan
# task, ADR or numbered decision).
PROVENANCE = re.compile(r"\bPR #?\d+\b.{0,12}\bthreads?\b|\bthreads? #?\d{3,}\b"
                        r"|\bPR \d+,? \d{4,}\b|\bPO 20\d\d|Product Owner"
                        r"|\bN?FR-[A-Za-z0-9]+\b|\brow \d+[a-z]*\b|\bLP\d+\b|\btask #?\d{3,}\b"
                        r"|\bADR-\d{4}\b|\bdecision \d+\b")
PROVENANCE_DIRS = tuple(f"plugins/dev-loop/{name}/" for name in
                        ("src", "hooks", "skills", "agents", "templates", "commands"))
PROVENANCE_FILES = ("CHANGELOG.md", "README.md")

KNOWN: list[str] = []  # emptied at v0.1: the guard holds on the whole tree


def private_file() -> Path:
    """The private deny list's path: DEV_LOOP_PRIVATE_NAMES, else the default under ~/.config."""
    named = os.environ.get("DEV_LOOP_PRIVATE_NAMES")
    return Path(named).expanduser() if named else PRIVATE_DEFAULT


def private_names() -> list[re.Pattern[str]]:
    """The private deny list's patterns; none when its file is absent."""
    path = private_file()
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [re.compile(line, re.IGNORECASE) for line in lines if line and line[0] != "#"]


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _rules(rel: str, private: list[re.Pattern[str]]) -> list[re.Pattern[str]]:
    scoped = rel.startswith(PROVENANCE_DIRS) or rel in PROVENANCE_FILES
    return DENY + private + ([PROVENANCE] if scoped else [])


def check(paths: list[str | Path], root: Path = ROOT) -> list[str]:
    """Return the paths (posix, relative to root) whose text hits the denylist."""
    private = private_names()
    bad = []
    for item in paths:
        path = Path(item) if Path(item).is_absolute() else root / item
        rel = path.resolve().relative_to(root).as_posix()
        if rel != SELF and any(rx.search(_text(path)) for rx in _rules(rel, private)):
            bad.append(rel)
    return bad


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return out.split("\n")[:-1] if out else []


def test_no_new_offender() -> None:
    assert sorted(set(check(_tracked())) - set(KNOWN)) == []


def test_known_has_no_stale_entry() -> None:
    assert sorted(set(KNOWN) - set(check(_tracked()))) == []


def test_no_private_name() -> None:
    """Every tracked file, this one included, is clear of the private list; names never print."""
    import pytest  # noqa: PLC0415 - release.sh and merge.sh import this file without pytest

    private = private_names()
    if not private:
        pytest.skip(f"no private name list at {private_file()} (set DEV_LOOP_PRIVATE_NAMES)")
    hits = [name for name in _tracked()
            if any(rx.search(_text(ROOT / name)) for rx in private)]
    assert hits == []


def test_planted_violation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_LOOP_PRIVATE_NAMES", str(tmp_path / "absent.txt"))
    scoped = "plugins/dev-loop/src/x.py"
    (tmp_path / scoped).parent.mkdir(parents=True)
    for name, text in (("x.md", "mail a@b.com"), ("x.md", "branch: develop"),
                       ("x.md", "host 203.0.113.9"), ("x.md", "ssh 10.0.0.12:22"),
                       (scoped, "# fixed in PR 12 thread 3456"), (scoped, "# thread 9876"),
                       (scoped, "# PR 12 3456"), (scoped, "# PO 2026-01-01"),
                       (scoped, "# the Product Owner asked"), (scoped, "# rooted (FR-4)"),
                       (scoped, "# fast (NFR-b1)"), (scoped, "# see row 45s"),
                       (scoped, "# see row 9"), (scoped, "# LP25: discover"),
                       (scoped, "# from task #303"), (scoped, "# from task 1234"),
                       (scoped, "# per ADR-0017"), (scoped, "# per decision 52")):
        (tmp_path / name).write_text(text)
        assert check([name], root=tmp_path) == [name], text
    (tmp_path / scoped).write_text("# PR 7 is open; each review thread is resolved; a row of 3, "
                                   "the row's task, task #12, ADR-12, a decision to merge, "
                                   "SFR-1, HELP25")
    (tmp_path / "x.md").write_text("mail noreply@anthropic.com, janithahn, 127.0.0.1:8080, "
                                   "0.0.0.0, version 0.1.4, 1.2.3.4.5, PR 12 thread 3456, "
                                   "zorblax, notes # a comment")
    assert check(["x.md", scoped], root=tmp_path) == []

    (tmp_path / "private.txt").write_text("# a comment\nzorblax\n")
    monkeypatch.setenv("DEV_LOOP_PRIVATE_NAMES", str(tmp_path / "private.txt"))
    assert check(["x.md"], root=tmp_path) == ["x.md"]
    (tmp_path / "x.md").write_text("a ZorBlax bank, notes # a comment")
    assert check(["x.md"], root=tmp_path) == ["x.md"]
    (tmp_path / "x.md").write_text("notes # a comment")
    assert check(["x.md"], root=tmp_path) == []


def test_manifests_are_neutral() -> None:
    manifests = [".claude-plugin/marketplace.json", "plugins/dev-loop/.claude-plugin/plugin.json",
                 "pyproject.toml"]
    assert check(manifests) == []
