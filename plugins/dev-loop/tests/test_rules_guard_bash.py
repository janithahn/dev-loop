"""E: the rules guard on a Bash command: a write of a change's records.jsonl or of a size waiver is
denied, by what the command writes; a read, the writer and an ordinary commit pass."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "hooks" / "rules-guard.py"
RECORDS = "rules-guard: records go through the record writer (dl record), never a hand edit"
WAIVED = "rules-guard: only a person adds a size waiver: ask the owner to write the line"
C1 = ".dev-loop/changes/c1"


def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".dev-loop" / "changes" / "c1").mkdir(parents=True)
    (root / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")
    return root


def bash(command: str, root: Path, **more: str) -> subprocess.CompletedProcess[str]:
    event = {"tool_name": "Bash", "tool_input": {"command": command}, **more}
    env = {k: v for k, v in os.environ.items() if k != "DEV_LOOP_ROLE"}
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
        check=False, timeout=30, env={**env, "CLAUDE_PROJECT_DIR": str(root)},
    )


@pytest.mark.parametrize(("command", "reason"), [
    (f"echo '{{}}' >> {C1}/records.jsonl", RECORDS),
    (f"printf x > {C1}/records.jsonl", RECORDS),
    (f"echo x | tee -a {C1}/records.jsonl", RECORDS),
    (f"sed -i 's/a/b/' {C1}/records.jsonl", RECORDS),
    (f"cp /tmp/x {C1}/records.jsonl", RECORDS),
    (f"python3 -c \"open('{C1}/records.jsonl', 'a').write('x')\"", RECORDS),
    (f"echo '**Size waiver:** lead 2026-01-01 — r' >> {C1}/proposal.md", WAIVED),
    (f"printf 'Size waiver: x' | tee -a {C1}/proposal.md", WAIVED),
    (f"sed -i '$a Size waiver: x' {C1}/proposal.md", WAIVED),
    (f"cd {C1} && echo '{{}}' >> records.jsonl", RECORDS),
    (f"echo '{{}}' 2> {C1}/records.jsonl", RECORDS),
    (f"bash -c \"echo x >> {C1}/records.jsonl\"", RECORDS),
    (f"dd if=/tmp/x of={C1}/records.jsonl", RECORDS),
    (f"rm -f {C1}/records.jsonl", RECORDS),
    (f"mv {C1}/records.jsonl /tmp/gone", RECORDS),
    (f"mv /tmp/new {C1}/records.jsonl", RECORDS),
    (f"git checkout -- {C1}/records.jsonl", RECORDS),
    (f"git checkout HEAD~1 -- {C1}/records.jsonl", RECORDS),
    (f"git restore {C1}/records.jsonl", RECORDS),
    (f"git rm -q {C1}/records.jsonl", RECORDS),
    (f"perl -pi -e 's/a/b/' {C1}/records.jsonl", RECORDS),
    (f"sed -E -i 's/a/b/' {C1}/records.jsonl", RECORDS),
    (f"sed -e 's/a/b/' -i {C1}/records.jsonl", RECORDS),
    (f"sed -Ei.bak 's/a/b/' {C1}/records.jsonl", RECORDS),
    (f"sed --in-place 's/a/b/' {C1}/records.jsonl", RECORDS),
    (f"truncate -s 0 {C1}/records.jsonl", RECORDS),
    (f"node -e \"require('fs').appendFileSync('{C1}/records.jsonl', 'x')\"", RECORDS),
    (f"python3 -c \"from pathlib import Path; Path('{C1}/records.jsonl').open('a').write('x')\"",
     RECORDS),
    (f"python3 -c \"import os; os.remove('{C1}/records.jsonl')\"", RECORDS),
    (f"cd {C1} && echo x >> records.json*", RECORDS),
    (f"cd {C1} && rm records.j*", RECORDS),
])
def test_a_bash_write_of_records_or_a_waiver_is_denied(
    command: str, reason: str, tmp_path: Path
) -> None:
    done = bash(command, project(tmp_path))
    assert done.returncode == 2
    assert done.stderr.strip() == reason


@pytest.mark.parametrize("command", [
    "dl record c1 note x=1",
    f"cat {C1}/records.jsonl",
    f"grep -c lesson {C1}/records.jsonl",
    f"python3 -c \"import json; json.load(open('{C1}/records.jsonl'))\"",
    "git commit -m 'Size waiver: explained in the PR'",
    f"echo hi >> {C1}/notes.md",
    f"cat {C1}/records.jsonl 2>/dev/null",
    f"tail -3 {C1}/records.jsonl 2>&1",
    f"grep lesson {C1}/records.jsonl > /tmp/x",
    f"jq 'select(.n > 3)' {C1}/records.jsonl",
    f"cp {C1}/records.jsonl /tmp/b",
    f"git add {C1}/records.jsonl && git commit -m records 2>&1",
    "git commit -m 'records.jsonl -> writer only'",
    "dl record c1 note 'see records.jsonl' > /dev/null",
    "mkdir -p tests/fixtures && echo x > tests/fixtures/records.jsonl",
    f"grep -n 'Size waiver:' {C1}/proposal.md 2>/dev/null",
    f"grep -c 'Size waiver:' {C1}/proposal.md > /tmp/n",
    f"sed -n '1,5p' {C1}/records.jsonl",
    f"cp {C1}/proposal.md /tmp/p && echo 'Size waiver: x' >> /tmp/p",
    "rm -f /tmp/records.jsonl",
    "git checkout main",
    f"git restore --staged {C1}/notes.md",
    f"git log --oneline -- {C1}/records.jsonl",
    f"git diff HEAD -- {C1}/records.jsonl",
    f"perl -ne 'print if /lesson/' {C1}/records.jsonl",
    f"sed -E -n '/lesson/p' {C1}/records.jsonl",
    f"node -e \"console.log(require('fs').readFileSync('{C1}/records.jsonl', 'utf8').length)\"",
    f"ls -l {C1}/records.json*",
    f"wc -l {C1}/records.jsonl | tee /tmp/count",
    "mv /tmp/a /tmp/b",
])
def test_a_bash_command_that_only_reads_or_uses_the_writer_is_allowed(
    command: str, tmp_path: Path
) -> None:
    done = bash(command, project(tmp_path))
    assert (done.returncode, done.stderr) == (0, "")


def test_a_command_the_guard_cannot_parse_fails_open_and_loud(tmp_path: Path) -> None:
    done = bash(f"cat {C1}/records.jsonl 'unclosed", project(tmp_path))
    assert done.returncode == 1
    assert done.stderr.strip() == "rules-guard: ValueError, call allowed"


LINE = "Size waiver: lead 2026-01-01 — one module"


@pytest.mark.parametrize("command", [
    f"mv w.md {C1}/proposal.md",
    f"cp w.md {C1}/proposal.md",
    f"cat w.md >> {C1}/proposal.md",
    f"cat w.md | tee -a {C1}/proposal.md",
    f"cd {C1} && cat ../../../w.md >> proposal.md",
    f"cd {C1} && echo 'Size waiver: x' >> proposal.m*",
    f"cp {C1}/../c0/proposal.md {C1}/proposal.md",
])
def test_a_waiver_carried_in_from_another_file_is_denied(command: str, tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "w.md").write_text(f"{LINE}\n", encoding="utf-8")
    (root / C1 / "proposal.md").write_text("# P\n", encoding="utf-8")
    (root / ".dev-loop/changes/c0").mkdir()
    (root / ".dev-loop/changes/c0/proposal.md").write_text(f"# P0\n{LINE}\n", encoding="utf-8")
    done = bash(command, root)
    assert done.returncode == 2
    assert done.stderr.strip() == WAIVED


@pytest.mark.parametrize("command", [
    f"mv plain.md {C1}/proposal.md",
    f"cat plain.md >> {C1}/proposal.md",
    f"cat held.md >> {C1}/proposal.md",
    f"cat w.md >> {C1}/notes.md",
    f"cat w.md {C1}/proposal.md > /tmp/both",
    f"echo '**Status:** Draft' >> {C1}/proposal.md",
])
def test_a_proposal_write_that_brings_no_new_waiver_is_allowed(
    command: str, tmp_path: Path
) -> None:
    root = project(tmp_path)
    (root / "w.md").write_text(f"{LINE}\n", encoding="utf-8")
    (root / "plain.md").write_text("# P\nSize is small.\n", encoding="utf-8")
    (root / "held.md").write_text("**Size waiver:** owner 2026-01-02 — held\n", encoding="utf-8")
    (root / C1 / "proposal.md").write_text(
        "# P\n**Size waiver:** owner 2026-01-02 — held\n", encoding="utf-8")
    done = bash(command, root)
    assert (done.returncode, done.stderr) == (0, "")


SETTINGS = "rules-guard: a subagent never edits Claude Code settings: ask the coordinator"


@pytest.mark.parametrize("command", [
    "jq '.x = 1' .claude/settings.json > t && mv t .claude/settings.json",
    "echo '{}' > .claude/settings.local.json",
    "cd .claude && cp /tmp/s settings.json",
    "sed -i 's/a/b/' .claude/settings.json",
    """python3 -c "open('.claude/settings.json', 'w').write('{}')" """,
    "rm .claude/settings.json",
    "echo '{}' > .Claude/Settings.json",
])
def test_a_subagent_bash_write_of_claude_settings_is_denied(command: str, tmp_path: Path) -> None:
    done = bash(command, project(tmp_path), agent_id="a1")
    assert done.returncode == 2
    assert done.stderr.strip() == SETTINGS


@pytest.mark.parametrize("command", [
    "cat .claude/settings.json",
    "jq . .claude/settings.json > /tmp/x",
    "cp .claude/settings.json /tmp/b",
    "echo x > .claude/agents/x.md",
    "git diff -- .claude/settings.json",
    "grep -rn settings . 2>/dev/null | head -3",
    "echo '{}' > app/settings.json",
])
def test_a_subagent_bash_read_of_claude_settings_is_allowed(command: str, tmp_path: Path) -> None:
    done = bash(command, project(tmp_path), agent_id="a1")
    assert (done.returncode, done.stderr) == (0, "")


def test_the_coordinator_may_write_claude_settings_through_bash(tmp_path: Path) -> None:
    done = bash("echo '{}' > .claude/settings.json", project(tmp_path))
    assert (done.returncode, done.stderr) == (0, "")


APPROVE = "rules-guard: approving a distilled skill is the owner's step, in their own terminal"


@pytest.mark.parametrize("command", [
    "dl records approve my-skill",
    "/opt/x/plugins/dev-loop/bin/dl records approve my-skill",
    "python -m dev_loop records approve my-skill",
    "python3 -m dev_loop records approve my-skill",
    "cd a && dl records approve my-skill",
    "dl records approve --help",
])
def test_approving_a_distilled_skill_is_denied(command: str, tmp_path: Path) -> None:
    done = bash(command, project(tmp_path))
    assert (done.returncode, done.stderr.strip()) == (2, APPROVE)


@pytest.mark.parametrize("command", [
    "dl records distil",
    'echo "dl records approve"',
    'grep -n "records approve" README.md',
    'git commit -m "the dl records approve step"',
    "dl records recall c1",
])
def test_the_twins_of_the_approve_step_pass(command: str, tmp_path: Path) -> None:
    done = bash(command, project(tmp_path))
    assert (done.returncode, done.stderr) == (0, "")
