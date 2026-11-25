"""`dl pr`: release refused, the integration branch by default, git and the host adapter stubbed."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from test_neutral import check

from dev_loop import __main__ as dl_main
from dev_loop import pr
from dev_loop.hosts import ado
from dev_loop.records import record

ROOT = Path(__file__).resolve().parents[3]


class Fake:
    """Records every command and PR call; git answers from `branch`, the size cap from `cap`."""

    def __init__(self, branch: str = "dev-loop/c1", cap: int = 0) -> None:
        self.branch, self.cap = branch, cap
        self.commands: list[list[str]] = []
        self.created: list[tuple[str, str, str, str]] = []
        self.existing: int | None = None
        self.log = ""
        self.porcelain, self.rebase_err, self.at_head = "", "", False

    def sh(self, root: Path, cmd: list[str], stdin: str | None = None) -> object:
        self.commands.append(cmd)
        out, code = "", 0
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            out, code = (f"{self.branch}\n", 0) if self.at_head else ("", 1)
        elif cmd[:2] == ["git", "rev-parse"]:
            out = f"{self.branch}\n"
        elif cmd[:2] == ["git", "status"]:
            out = self.porcelain
        elif cmd[:2] == ["git", "rebase"] and cmd[2:] != ["--abort"] and self.rebase_err:
            return subprocess.CompletedProcess(cmd, 1, "", self.rebase_err)
        elif cmd[:2] == ["git", "log"]:
            out = self.log
        elif cmd[:2] == ["git", "config"]:
            out = "me@x\n"
        elif cmd[0] == "bash":
            code = self.cap
        return subprocess.CompletedProcess(cmd, code, out, "")

    def create(self, source: str, target: str, title: str, body: str) -> int:
        self.created.append((source, target, title, body))
        return 42


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    cfg = {"version": 1, "branches": {"integration": "trunk", "release": "stable",
                                      "protected": ["trunk", "stable"]}}
    (tmp_path / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    folder = tmp_path / ".dev-loop" / "changes" / "c1"
    folder.mkdir(parents=True)
    (folder / "proposal.md").write_text("# Proposal: add the widget\n\nWhy.\n", encoding="utf-8")
    (folder / "spec.md").write_text("## Acceptance criteria\n- `x` prints y\n", encoding="utf-8")
    record.append(folder, "c1", {"kind": "approval", "by": "user message", "at": "now"})
    record.append(folder, "c1", {"kind": "simplifier", "edits": "none"})
    stub = Fake()
    monkeypatch.setattr(pr, "sh", stub.sh)
    monkeypatch.setattr(pr, "size_cap", lambda root, base: stub.sh(
        root, ["git", "diff", "--numstat", "--no-renames", base, "HEAD"]).returncode or stub.cap)
    monkeypatch.setattr(ado, "pr_create", stub.create)
    monkeypatch.setattr(ado, "pr_for_branch", lambda branch: stub.existing)
    monkeypatch.setattr(ado, "pr_get", lambda pr_id: {"targetRefName": "refs/heads/trunk"})
    return stub


def footer(root: Path, line: str) -> None:
    cfg = yaml.safe_load((root / "dev-loop.yaml").read_text(encoding="utf-8"))
    cfg["host"] = {"pr_footer": line}
    (root / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_default_target_is_integration(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    assert pr.main(["c1"]) == 0
    assert fake.created == [("dev-loop/c1", "trunk", "add the widget",
                             fake.created[0][3])]
    assert ["git", "rebase", "--rebase-merges", "origin/trunk"] in fake.commands
    assert ["git", "push", "--force-with-lease", "origin", "dev-loop/c1"] in fake.commands
    assert capsys.readouterr().out.strip().endswith("42")


def test_table_only_spec_is_refused(fake: Fake, tmp_path: Path) -> None:
    spec = tmp_path / ".dev-loop" / "changes" / "c1" / "spec.md"
    spec.write_text("## Acceptance criteria\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    assert pr.main(["c1"]) == 2
    assert not fake.commands[1:] and not fake.created


def test_named_target(fake: Fake) -> None:
    assert pr.main(["c1", "--target", "dev-loop/c0"]) == 0
    assert fake.created[0][1] == "dev-loop/c0"
    assert ["git", "rebase", "--rebase-merges", "origin/dev-loop/c0"] in fake.commands


def test_release_target_is_refused(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    assert pr.main(["c1", "--target", "stable"]) == 2
    assert fake.created == [] and fake.commands == []
    assert "release" in capsys.readouterr().err


def test_push_only_from_the_change_branch(fake: Fake) -> None:
    fake.branch = "trunk"
    assert pr.main(["c1"]) == 2
    assert not any(cmd[:2] == ["git", "push"] for cmd in fake.commands)
    assert fake.created == []


def test_size_cap_stops_before_push(fake: Fake) -> None:
    fake.cap = 1
    assert pr.main(["c1"]) == 1
    assert not any(cmd[:2] == ["git", "push"] for cmd in fake.commands)
    assert fake.created == []


def test_body_ends_with_configured_footer(fake: Fake, tmp_path: Path) -> None:
    footer(tmp_path, "Made with the dev loop")
    assert pr.main(["c1"]) == 0
    assert fake.created[0][3].rstrip().endswith("Made with the dev loop")


def test_no_footer_by_default(fake: Fake) -> None:
    assert pr.main(["c1"]) == 0
    assert "Generated" not in fake.created[0][3] and "Co-Authored" not in fake.created[0][3]


def test_a_dirty_tracked_file_is_no_conflict(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    fake.porcelain = " M src/a.py\n M src/b.py\n"
    assert pr.main(["c1"]) == 1
    assert ("dl pr: uncommitted changes to tracked files (src/a.py, 2 in all); commit or stash "
            "them: nothing rebased, nothing pushed") in capsys.readouterr().err
    assert not [c for c in fake.commands if c[:2] in (["git", "rebase"], ["git", "push"])]


def test_a_failed_rebase_shows_gits_text(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    fake.rebase_err = "CONFLICT (content): Merge conflict in a.py"
    assert pr.main(["c1"]) == 1
    assert ("stopped; aborted, nothing pushed: CONFLICT (content): Merge conflict in a.py"
            in capsys.readouterr().err)


def test_a_branch_at_the_remote_head_is_not_pushed(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.at_head = True
    assert pr.main(["c1"]) == 0
    assert not [c for c in fake.commands if c[:2] == ["git", "push"]]
    assert "push: origin/dev-loop/c1 is at HEAD" in capsys.readouterr().out
    assert fake.created


def test_a_pushed_branch_runs_no_push_under_a_failing_hook(tmp_path: Path) -> None:
    def git(*args: str, cwd: Path = tmp_path / "w") -> None:
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *args],
                       cwd=cwd, check=True, capture_output=True)

    git("init", "-q", "--bare", "r.git", cwd=tmp_path)
    git("clone", "-q", "r.git", "w", cwd=tmp_path)
    (tmp_path / "w" / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", "one")
    git("push", "-q", "origin", "HEAD:trunk")
    git("checkout", "-qb", "dev-loop/c1")
    git("push", "-q", "origin", "dev-loop/c1")
    hook = tmp_path / "r.git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (tmp_path / "w" / "dev-loop.yaml").write_text("version: 1\n")
    assert pr.publish(tmp_path / "w", "dev-loop/c1", "trunk") == 0


def test_a_pushed_branch_runs_no_push_under_a_failing_client_hook(tmp_path: Path) -> None:
    def git(*args: str, cwd: Path = tmp_path / "w") -> None:
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *args],
                       cwd=cwd, check=True, capture_output=True)

    git("init", "-q", "--bare", "r.git", cwd=tmp_path)
    git("clone", "-q", "r.git", "w", cwd=tmp_path)
    (tmp_path / "w" / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", "one")
    git("push", "-q", "origin", "HEAD:trunk")
    git("checkout", "-qb", "dev-loop/c1")
    git("push", "-q", "origin", "dev-loop/c1")
    hook = tmp_path / "w" / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (tmp_path / "w" / "dev-loop.yaml").write_text("version: 1\n")
    assert pr.publish(tmp_path / "w", "dev-loop/c1", "trunk") == 0


def test_a_failed_push_shows_the_hooks_last_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def git(*args: str, cwd: Path = tmp_path / "w") -> None:
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *args],
                       cwd=cwd, check=True, capture_output=True)

    git("init", "-q", "--bare", "r.git", cwd=tmp_path)
    git("clone", "-q", "r.git", "w", cwd=tmp_path)
    (tmp_path / "w" / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", "one")
    git("push", "-q", "origin", "HEAD:trunk")
    git("checkout", "-qb", "dev-loop/c1")
    (tmp_path / "w" / "g").write_text("2")
    git("add", "g")
    git("commit", "-qm", "two")
    hook = tmp_path / "w" / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nseq 101 130\necho 'typecheck: FAIL src/a.py:3'\nexit 1\n")
    hook.chmod(0o755)
    (tmp_path / "w" / "dev-loop.yaml").write_text("version: 1\n")
    assert pr.publish(tmp_path / "w", "dev-loop/c1", "trunk") == 1
    err = capsys.readouterr().err
    assert "typecheck: FAIL src/a.py:3" in err
    assert "130" in err
    assert "101" not in err
    assert "dl pr: push failed: error: failed to push some refs" in err


def test_the_push_names_its_target_to_the_hook(tmp_path: Path) -> None:
    def git(*args: str, cwd: Path = tmp_path / "w") -> None:
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *args],
                       cwd=cwd, check=True, capture_output=True)

    git("init", "-q", "--bare", "r.git", cwd=tmp_path)
    git("clone", "-q", "r.git", "w", cwd=tmp_path)
    (tmp_path / "w" / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", "one")
    git("push", "-q", "origin", "HEAD:main")
    git("push", "-q", "origin", "HEAD:parent")
    git("checkout", "-qb", "dev-loop/c1")
    (tmp_path / "w" / "g").write_text("2")
    git("add", "g")
    git("commit", "-qm", "two")
    seen = tmp_path / "seen"
    hook = tmp_path / "w" / ".git" / "hooks" / "pre-push"
    hook.write_text(f'#!/bin/sh\necho "$PREPUSH_BASE" > "{seen}"\nexit 0\n')
    hook.chmod(0o755)
    (tmp_path / "w" / "dev-loop.yaml").write_text("version: 1\n")
    assert pr.publish(tmp_path / "w", "dev-loop/c1", "parent") == 0
    assert seen.read_text().strip() == "origin/parent"
    assert "PREPUSH_BASE" not in os.environ


def test_the_hook_gets_the_users_pythonpath_not_the_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*args: str, cwd: Path = tmp_path / "w") -> None:
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *args],
                       cwd=cwd, check=True, capture_output=True)

    git("init", "-q", "--bare", "r.git", cwd=tmp_path)
    git("clone", "-q", "r.git", "w", cwd=tmp_path)
    (tmp_path / "w" / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", "one")
    git("push", "-q", "origin", "HEAD:main")
    git("checkout", "-qb", "dev-loop/c1")
    (tmp_path / "w" / "g").write_text("2")
    git("add", "g")
    git("commit", "-qm", "two")
    seen = tmp_path / "seen"
    hook = tmp_path / "w" / ".git" / "hooks" / "pre-push"
    hook.write_text(f'#!/bin/sh\necho "x${{PYTHONPATH}}x" > "{seen}"\nexit 0\n')
    hook.chmod(0o755)
    (tmp_path / "w" / "dev-loop.yaml").write_text("version: 1\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "w"))
    src = str(Path(pr.__file__).parent.parent)
    monkeypatch.setenv("PYTHONPATH", f"{src}{os.pathsep}/users/own")
    dl_main.main(["config", "get", "branches.remote"])  # the shim's PYTHONPATH is shed at start
    assert pr.publish(tmp_path / "w", "dev-loop/c1", "main") == 0
    assert seen.read_text().strip() == "x/users/ownx"
    monkeypatch.setenv("PYTHONPATH", src)
    dl_main.main(["config", "get", "branches.remote"])
    assert "PYTHONPATH" not in os.environ


def test_a_set_hook_base_is_put_back_after_the_push(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PREPUSH_BASE", "origin/old")
    pr.main(["c1"])
    assert os.environ["PREPUSH_BASE"] == "origin/old"


def test_open_pr_is_reported_not_duplicated(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    fake.existing = 7
    assert pr.main(["c1"]) == 0
    assert fake.created == []
    assert capsys.readouterr().out.strip().endswith("7")


def test_an_open_stacked_part_is_judged_from_its_own_target(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.existing = 7
    monkeypatch.setattr(ado, "pr_get", lambda pr_id: {"targetRefName": "refs/heads/part-a"})
    assert pr.main(["c1"]) == 0
    assert ["git", "rebase", "--rebase-merges", "origin/part-a"] in fake.commands
    assert any(c[:3] == ["git", "diff", "--numstat"] and "origin/part-a" in c
               for c in fake.commands)


def stacked_part(root: Path, monkeypatch: pytest.MonkeyPatch, on: int | None) -> None:
    """Part b of a two-part change, raised on part a's branch, whose PR is `on`."""
    tasks = root / ".dev-loop" / "changes" / "c1" / "tasks.md"
    tasks.write_text("## Part a\n\n### T1: one\n- Status: done\n\n"
                     "## Part b\n\n### T2: two\n- Status: done\n", encoding="utf-8")
    monkeypatch.setattr(ado, "pr_for_branch", lambda branch: on if branch == "part-a" else None)


def test_a_stacked_part_names_its_part_and_its_parents_pr(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0.4.22: part b's PR carried part a's title and body; nothing named the parent."""
    stacked_part(tmp_path, monkeypatch, 7)
    assert pr.main(["c1", "--base", "part-a"]) == 0
    _, target, title, body = fake.created[0]
    assert (target, title) == ("trunk", "add the widget (part b)")
    assert body.startswith(
        "Stacked on PR 7 (part-a); merge that PR first.\n\n# Proposal: add the widget")


def test_a_stacked_part_without_a_parent_pr_names_the_branch(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stacked_part(tmp_path, monkeypatch, None)
    proposal = tmp_path / ".dev-loop" / "changes" / "c1" / "proposal.md"
    proposal.write_text(f"# Proposal: {'w' * 140}\n\nWhy.\n", encoding="utf-8")
    assert pr.main(["c1", "--base", "part-a"]) == 0
    _, _, title, body = fake.created[0]
    assert body.startswith("Stacked on part-a; merge that branch first.\n\n")
    assert len(title) <= 128 and title.endswith(" (part b)"), title


def two_parts(root: Path, a: str, b: str) -> None:
    (root / ".dev-loop" / "changes" / "c1" / "tasks.md").write_text(
        f"## Part a\n\n### T1: one\n- Status: {a}\n\n## Part b\n\n### T2: two\n- Status: {b}\n",
        encoding="utf-8")


def test_a_part_is_named_from_its_latest_started_part_whatever_its_parent(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0.4.22 review minor 2: part a on another change was "(part b)"; part b with no known
    parent (part a merged) carried part a's title."""
    two_parts(tmp_path, "doing", "todo")
    monkeypatch.setattr(ado, "pr_for_branch", lambda branch: None)
    assert pr.main(["c1", "--base", "other-change"]) == 0
    _, _, title, body = fake.created[-1]
    assert title == "add the widget (part a)"
    assert body.startswith("Stacked on other-change; merge that branch first.\n\n")
    two_parts(tmp_path, "done", "doing")
    assert pr.main(["c1"]) == 0  # no parent named or remembered
    _, _, title, body = fake.created[-1]
    assert title == "add the widget (part b)"
    assert body.startswith("# Proposal: add the widget"), body


def test_a_malformed_task_head_gives_the_plain_title(fake: Fake, tmp_path: Path) -> None:
    (tmp_path / ".dev-loop" / "changes" / "c1" / "tasks.md").write_text(
        "## Part a\n\n### T1: one\n- Status: done\n\n### Tx: bad\n", encoding="utf-8")
    assert pr.main(["c1", "--base", "part-a"]) == 0
    _, _, title, body = fake.created[-1]
    assert title == "add the widget"
    assert body.startswith("Stacked on part-a; merge that branch first.\n\n")


def source_cfg(root: Path) -> None:
    cfg = yaml.safe_load((root / "dev-loop.yaml").read_text(encoding="utf-8"))
    cfg["size"] = {"production": ["src/**"]}
    (root / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    tasks = root / ".dev-loop" / "changes" / "c1" / "tasks.md"
    tasks.write_text("### T1: add it\n- Files: src/x.py\n", encoding="utf-8")


def test_own_commit_outside_a_task_is_refused_on_first_raise(
    fake: Fake, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_cfg(tmp_path)
    fake.log = ("a1\tme@x\tT1: add it\nb2\tme@x\tc1: simplify\n"
                "c3\tyou@x\tfix by a teammate\n")
    assert pr.main(["c1"]) == 0
    fake.log += "d4\tme@x\tc1: tidy docstring\n"
    fake.created.clear()
    fake.commands.clear()
    assert pr.main(["c1"]) == 2
    err = capsys.readouterr().err
    assert 'd4 "c1: tidy docstring"' in err and "git rebase --onto d4^ d4" in err
    assert not any(cmd[:2] == ["git", "push"] for cmd in fake.commands) and not fake.created


def test_the_source_check_is_scoped_to_the_production_globs(fake: Fake, tmp_path: Path) -> None:
    source_cfg(tmp_path)
    fake.log = ""  # git log over the globs lists nothing for a tests-only commit
    assert pr.main(["c1"]) == 0
    log = next(cmd for cmd in fake.commands if cmd[:2] == ["git", "log"])
    assert log[-2:] == ["--", ":(glob)src/**"]


def test_an_open_pr_takes_a_review_fix_commit(fake: Fake, tmp_path: Path) -> None:
    source_cfg(tmp_path)
    fake.log = "e5\tme@x\tc1: fix (review round 1)\n"
    fake.existing = 7
    assert pr.main(["c1"]) == 0


def test_the_nudge_is_for_a_first_raise_only(
    fake: Fake, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert pr.main(["c1"]) == 0
    assert "[pr-loop]" in capsys.readouterr().err
    fake.existing = 7
    assert pr.main(["c1"]) == 0
    seen = capsys.readouterr()
    assert "[pr-loop]" not in seen.err and seen.out.strip().endswith("open PR dev-loop/c1 -> trunk: 7")


def test_files_are_neutral() -> None:
    files = ["plugins/dev-loop/src/dev_loop/pr.py", "plugins/dev-loop/tests/test_pr.py",
             "plugins/dev-loop/skills/pr/SKILL.md"]
    assert check(files, root=ROOT) == []


@pytest.mark.parametrize(("heading", "title"), [
    ("# Proposal: Smoke: a check", "Smoke: a check"),
    ("# proposal:  Lower case", "Lower case"),
    ("# Smoke: a check", "Smoke: a check"),
])
def test_title_drops_the_proposal_prefix(tmp_path: Path, heading: str, title: str) -> None:
    proposal = tmp_path / "c1" / "proposal.md"
    proposal.parent.mkdir()
    proposal.write_text(f"{heading}\n\nWhy.\n", encoding="utf-8")
    assert pr.title_and_body(proposal, None)[0] == title


def test_the_rebase_keeps_merge_commits(fake: Fake) -> None:
    assert pr.main(["c1"]) == 0
    rebases = [c for c in fake.commands if c[:2] == ["git", "rebase"] and c[2:] != ["--abort"]]
    assert len(rebases) == 1 and "--rebase-merges" in rebases[0]


def test_a_task_done_by_a_merge_survives_the_raise(tmp_path: Path) -> None:
    def git(*args: str, cwd: Path = tmp_path / "w") -> str:
        done = subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *args],
                              cwd=cwd, check=True, capture_output=True, text=True)
        return done.stdout.strip()

    git("init", "-q", "--bare", "r.git", cwd=tmp_path)
    git("clone", "-q", "r.git", "w", cwd=tmp_path)
    (tmp_path / "w" / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", "one")
    git("push", "-q", "origin", "HEAD:trunk")
    git("checkout", "-qb", "side")
    (tmp_path / "w" / "g").write_text("2")
    git("add", "g")
    git("commit", "-qm", "side work")
    side = git("rev-parse", "HEAD")
    git("checkout", "-qb", "dev-loop/c1", "origin/trunk")
    git("merge", "--no-ff", "-m", "T1: Bring", "side")
    (tmp_path / "w" / "dev-loop.yaml").write_text("version: 1\n")
    assert pr.publish(tmp_path / "w", "dev-loop/c1", "trunk") == 0
    assert git("log", "--merges", "--format=%s", "origin/trunk..HEAD") == "T1: Bring"
    git("merge-base", "--is-ancestor", side, "HEAD")


def test_a_repeated_simplifier_after_a_round_opens_the_pr(fake: Fake, tmp_path: Path) -> None:
    folder = tmp_path / ".dev-loop" / "changes" / "c1"
    record.append(folder, "c1", {"kind": "round", "round": 1, "pr": 3, "base": "x", "blocker": 0,
                                 "major": 0, "minor": 0, "fixed": 0, "deferred": 0, "active": 0})
    record.append(folder, "c1", {"kind": "simplifier", "edits": "none"})  # the same as before
    assert pr.main(["c1"]) == 0 and fake.created
