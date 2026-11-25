"""Tests for review_pitfalls.py: packages from `knowledge.packages`, pitfalls, do-not-repeat.

Plants lessons with `Files:` for two packages, a history lesson joined through a merged PR in a
throwaway git repo, and the degraded cases.
"""

from __future__ import annotations

import ast
import subprocess
import tempfile
from pathlib import Path

import pytest
from dev_loop.records.review_pitfalls import (
    change_prs,
    do_not_repeat,
    package_of,
    pitfalls,
    prs_by_change,
)
from test_dev_loop_fixtures import Tree

CONFIG = """\
knowledge:
  packages: ["src/app:2", "tests:1=src/app", "web/src/features:1", "web/src/app:0",
             "web/src/styles:0", "web/src/routes:0", "web/src/store:0", "web/src/api:0",
             "web/src/theme:0"]
  guides: ["src/app/*/CLAUDE.md", "web/CLAUDE.md", "web/src/features/*/CLAUDE.md"]
paths:
  frozen: ["frozen"]
"""
CHANGES = ".dev-loop/changes"
SOURCE = Path(__file__).resolve().parents[1] / "src/dev_loop/records"
ANALYSIS = "src/app/analysis"
WORKSPACE = "src/app/workspace"
DOC = (
    "| # | Class | Mined | Lessons since | Automated guard |\n|---|---|---|---|---|\n"
    "| 3 | Same thing twice | 1 (1) | 0 | review only: r |\n"
    "| 9 | A failure swallowed | 1 (1) | 0 | `tests/guard_a.py` |\n"
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tree: Tree) -> Path:
    """A project whose dev-loop.yaml names packages, guides and the frozen folder."""
    write(tree.root / "dev-loop.yaml", CONFIG)
    return tree.root


def planted(change: str, class_: str, files: str = "", avoid: str = "Avoid: do it right") -> str:
    detail = f"Summary: s\nClass: {class_}\nGuard: review: r\n"
    detail += f"Files: {files}\n" if files else ""
    return (
        f"## [L1] x\n\n**When:** 2026-10-05 08:00 UTC\n\n### Detail\n{detail}\n"
        f"### Action\n{avoid}. Self-check: look.\n"
    )


def git(root: Path, *args: str) -> None:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*cmd, *args], cwd=root, capture_output=True, text=True, check=True)


def test_package_of(root: Path) -> None:
    assert package_of(f"{ANALYSIS}/checks/a.py", root) == f"{ANALYSIS}/checks"
    assert package_of(f"{ANALYSIS}/runner.py", root) == ANALYSIS
    assert package_of("tests/analysis/test_a.py", root) == ANALYSIS
    assert package_of(r"src\app\intake\x.py", root) == "src/app/intake"
    assert package_of("web/src/features/case/a.tsx", root) == (
        "web/src/features/case"
    )
    for shared in ("app", "styles", "routes"):
        assert package_of(f"web/src/{shared}/a.tsx", root) == f"web/src/{shared}"
    assert package_of("web/src/routes/cases/a.tsx", root) == "web/src/routes"
    assert package_of("web/src/main.tsx", root) is None
    assert package_of("README.md", root) is None and package_of("scripts/x.py", root) is None


def test_files_win(root: Path) -> None:
    """A lesson with `Files:` counts for its files' package only, not its PR's other packages."""
    changes = root / ".dev-loop/changes"
    write(root / "REVIEW-PITFALLS.md", DOC)
    write(changes / "fa/learnings.md", planted("fa", "3", f"{ANALYSIS}/runner.py"))
    write(changes / "fb/learnings.md", planted("fb", "9", f"{WORKSPACE}/queue_api.py"))
    write(changes / "fb/status.md", "| 11 | part a | 1 | 0/0/0 | 0/0/0 | 1 |\n")
    for name in (f"{ANALYSIS}/x.py", f"{WORKSPACE}/y.py"):
        write(root / name, "")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Merged PR 11: part a")  # touches both packages
    out = pitfalls(root, [f"{ANALYSIS}/runner.py"])
    lead = "class 3 Same thing twice · 1 lessons in 1 changes (fa) · avoid: do it"
    assert len(out) == 1 and out[0].startswith(lead), out
    assert out[0].endswith("guard: review: r"), out
    out = pitfalls(root, [f"{WORKSPACE}/queue_api.py"])
    assert len(out) == 1 and out[0].startswith("class 9 "), out
    both = pitfalls(root, [f"{ANALYSIS}/runner.py", f"{WORKSPACE}/queue_api.py"])
    assert len(both) == 2, both
    no_lessons = ["no lessons yet for src/app/intake"]
    assert pitfalls(root, ["src/app/intake/z.py"]) == no_lessons
    assert pitfalls(root, ["README.md"]) == ["no package for README.md"]
    assert pitfalls(root, []) == ["pitfalls unavailable: no paths given"]  # never empty
    mixed = pitfalls(root, ["README.md", f"{ANALYSIS}/x.py", "src/app/intake/z.py"])
    assert mixed[0].startswith("class 3 "), mixed
    assert mixed[1:] == ["no lessons yet for src/app/intake", "no package for README.md"]
    many = pitfalls(root, [f"doc{i}.md" for i in range(8)] + ["src/app/intake/z.py"])
    assert many[-1] == "3 more paths without a package" and len(many) == 7, many


def test_history_and_five(root: Path) -> None:
    """A lesson without `Files:` follows its change's merged PR; only five lines, ranked."""
    changes = root / ".dev-loop/changes"
    write(root / "REVIEW-PITFALLS.md", DOC)
    write(changes / "h/learnings.md", planted("h", "9"))
    write(changes / "h/status.md", "| 12 | part a | 1 | 0/0/0 | 0/0/0 | 1 |\n")
    write(changes / "n/learnings.md", planted("n", "9"))  # no PR row: contributes nothing
    write(changes / "m/learnings.md", planted("m", "9"))  # a PR id (13), never merged
    write(changes / "m/status.md", "| 13 | part a | 1 | 0/0/0 | 0/0/0 | 1 |\n")
    write(root / f"{WORKSPACE}/h.py", "")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Merged PR 12: history")
    history = pitfalls(root, [f"{WORKSPACE}/q.py"])
    assert history[0].startswith("class 9 "), "history by PR"
    assert history[-1] == "2 changes without a merged PR", history  # n and m: never silent
    # 5253 major 1: the tail also follows "no lessons yet", as their lessons might be here
    assert pitfalls(root, [f"{ANALYSIS}/q.py"]) == [
        f"no lessons yet for {ANALYSIS}",
        "2 changes without a merged PR",
    ]
    assert change_prs(root, "h") == [12] and change_prs(root, "n") == []
    frozen = "| PR | Change |\n| 7 | n PR a (round 1 whole PR) | 1 |\n"  # name, then free text
    write(root / "frozen/review-metrics.md", frozen)
    assert change_prs(root, "n") == [7], "the frozen per-PR table names its PR"
    for i, count in enumerate((3, 2, 1, 1, 1, 1, 1)):  # seven classes, five lines
        for n in range(count):
            files = f"{ANALYSIS}/f{n}.py"
            write(changes / f"c{i}{n}/learnings.md", planted("c", f"new: Class {i}", files))
    write(changes / "c9/learnings.md", planted("c", "9", f"{ANALYSIS}/f9.py"))  # ties on count
    out = pitfalls(root, [f"{ANALYSIS}/q.py"])
    assert len(out) == 6 and out[-1] == "2 changes without a merged PR", out  # five and the tail
    order = ("new: Class 0", "new: Class 1", "class 9 ", "new: Class 2", "new: Class 3")
    for line, want in zip(out, order, strict=False):  # count desc, class number, then name
        assert line.startswith(want), out


def test_degraded(root: Path) -> None:
    """No git history: class-level counts and a stated note, still five lines at most."""
    write(root / "REVIEW-PITFALLS.md", DOC)
    write(root / ".dev-loop/changes/h/learnings.md", planted("h", "9"))
    out = pitfalls(root, [f"{WORKSPACE}/q.py"], ref="no-such-ref")
    assert out[0].startswith("note: ") and "class-level" in out[0], out
    assert out[1].startswith("class 9 "), out
    rules = do_not_repeat(root, [f"{WORKSPACE}/q.py"], ref="no-such-ref")  # no git: same note
    assert rules[1].startswith("note: ") and rules[2].startswith("- class 9 "), rules


def test_old_python() -> None:
    """No backslash or reused quote in an f-string expression (Python < 3.12)."""
    for path in sorted(SOURCE.glob("review_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 10))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            quote = (ast.get_source_segment(source, node) or "").lstrip("rbfRBF")[:1]
            for part in (v for v in node.values if isinstance(v, ast.FormattedValue)):
                expression = ast.get_source_segment(source, part.value) or ""
                assert "\\" not in expression and quote not in expression, (path.name, expression)


def test_shallow(root: Path) -> None:
    """Major 2: a shallow clone says its older PRs are not attributed."""
    write(root / "REVIEW-PITFALLS.md", DOC)
    write(root / ".dev-loop/changes/h/learnings.md", planted("h", "9", f"{WORKSPACE}/h.py"))
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "one")
    write(root / "x.txt", "x")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Merged PR 5: two")
    query = [f"{WORKSPACE}/q.py"]
    assert not pitfalls(root, query)[-1].startswith("history:")
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        git(root, "clone", "-q", "--depth", "1", f"file://{root.as_posix()}", str(clone))
        out = pitfalls(clone, query)
        assert out[-1] == "history: shallow (older PRs not attributed)", out


def test_frozen_names(root: Path) -> None:
    """Minor 7: a `fix(<name>)` cell names its change; only real change folders are names."""
    for name in ("q-stats", "other"):
        write(root / f".dev-loop/changes/{name}/status.md", "x\n")
    frozen = (
        "| 203 | fix(q-stats) (round 2) | 2 |\n| 204 | docs(other) | 1 |\n"
        "| 205 | not-a-change PR | 1 |\n"
    )
    write(root / "frozen/review-metrics.md", frozen)
    assert change_prs(root, "q-stats") == [203] and change_prs(root, "other") == [204]
    assert prs_by_change(root).keys() == {"q-stats", "other"}


def test_old_stdlib() -> None:
    """Major 4: no stdlib name Python 3.10 lacks (`datetime.UTC`)."""
    for path in sorted(SOURCE.glob("review_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                assert "UTC" not in [a.name for a in node.names], (path.name, "datetime.UTC")


def rule(when: str, class_: str, files: str, avoid: str) -> str:
    detail = f"Summary: s\nClass: {class_}\nGuard: review: r\nFiles: {files}\n"
    action = f"Avoid: {avoid}. Self-check: look {avoid}.\n"
    return f"## [L1] x\n\n**When:** {when} UTC\n\n### Detail\n{detail}\n### Action\n{action}"


def test_do_not_repeat(root: Path) -> None:
    """The section prints newest first, once per rule, tagged; frontend maps; none is clear."""
    changes = root / ".dev-loop/changes"
    write(root / "REVIEW-PITFALLS.md", DOC)
    feature = "web/src/features/case"
    plants = {
        "fa": ("2026-10-05 08:00", "3", f"{ANALYSIS}/runner.py", "old rule"),
        "fb": ("2026-10-09 08:00", "9", f"{ANALYSIS}/runner.py", "new rule"),
        "fc": ("2026-10-06 08:00", "3", f"{ANALYSIS}/x.py", "OLD rule"),
        "fd": ("2026-10-08 08:00", "9", f"{WORKSPACE}/q.py", "other"),
        "fe": ("2026-10-07 08:00", "3", f"{feature}/a.tsx", "ui rule"),
    }
    for change, plant in plants.items():
        write(changes / change / "learnings.md", rule(*plant))
    git(root, "init", "-q")  # with git the lessons' own files place them
    git(root, "add", "-A")
    git(root, "commit", "-qm", "start")
    out = do_not_repeat(root, [f"{ANALYSIS}/runner.py"])
    assert out[0] == "Do not repeat" and len(out) == 3, out  # other package, frontend left out
    assert out[1] == "- class 9 · fb:L1 · new rule. Self-check: look new rule.", out  # newest
    assert out[2] == "- class 3 · fc:L1 · OLD rule. Self-check: look OLD rule.", out  # dedup
    front = do_not_repeat(root, [f"{feature}/b.tsx"])
    assert front[1].startswith("- class 3 · fe:L1 · ui rule"), front
    none = ["Do not repeat: none logged for these packages"]
    assert do_not_repeat(root, ["web/src/styles/a.css"]) == none
    assert do_not_repeat(root, ["README.md"]) == none


def test_folder_package(root: Path) -> None:
    """A builder passes folders as often as files; both name the same package."""
    for folder, package in [
        ("src/app/intake", "src/app/intake"),
        ("src/app/analysis/checks/", "src/app/analysis/checks"),
        ("tests/workspace", "src/app/workspace"),
        ("web/src/features/case", "web/src/features/case"),
        ("web/src/app", "web/src/app"),
    ]:
        assert package_of(folder, root) == package, folder


def busy_repo(root: Path) -> None:
    """A lesson and three merged PRs of 200 files each."""
    from dev_loop.records import record

    write(root / "REVIEW-PITFALLS.md", DOC)
    pairs = ["priority=high", "summary=s", "avoid=do it right", "class=9", "guard=review: r",
             "round=1", f"files={ANALYSIS}/x.py"]
    write(root / CHANGES / "old/proposal.md", "p")
    record.write(root, "old", record.parse("lesson", pairs))
    write(root / CHANGES / "c/proposal.md", "p")
    write(root / CHANGES / "old/status.md", "| 11 | part a | 1 | 0/0/0 | 0/0/0 | 1 |\n")
    git(root, "init", "-q")
    for pr in (11, 12, 13):
        for i in range(200):
            write(root / f"{ANALYSIS}/pr{pr}/f{i}.py", "x\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", f"Merged PR {pr}: p")


def test_recall_reads_the_config_once(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recall loaded the config per file of every merged PR (over 10 minutes on a big repo)."""
    import time

    from dev_loop.records import review_pitfalls as module

    busy_repo(root)
    calls = []
    real = module.load
    monkeypatch.setattr(module, "load", lambda r: calls.append(r) or real(r))
    started = time.monotonic()
    module.pr_packages(root)
    assert len(calls) <= 2, calls  # branches once, packages once: never one per file
    assert module.recall(root, "c", [f"{ANALYSIS}/x.py"])
    assert time.monotonic() - started < 5


def test_recall_survives_a_slow_git_log(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dev_loop.records import record, review_records
    from dev_loop.records import review_pitfalls as module

    busy_repo(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    real = subprocess.run

    def slow(cmd: list[str], *a: object, **k: object) -> object:
        if "log" in cmd:
            raise subprocess.TimeoutExpired(cmd, 120)
        return real(cmd, *a, **k)

    monkeypatch.setattr(module.subprocess, "run", slow)
    assert record.unparsed(root) == []
    assert review_records.main(["recall", "c", f"{ANALYSIS}/x.py"]) == 0
    assert "note: no git; only lessons with files placed" in capsys.readouterr().out
