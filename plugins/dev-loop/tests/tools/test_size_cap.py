"""ci/size_cap.sh holds a PR to the size cap; the globs, the cap and the change folders come from
`dl config get` (a temp project with non-default globs). It reads `git diff --numstat` on stdin and
looks for a `Size waiver:` line in the proposal of each change folder the diff touches.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_neutral import check  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is absent")

BASH = shutil.which("bash") or "bash"
PLUGIN = Path(__file__).resolve().parents[2]
PY = {"DL_PYTHON": sys.executable}
CI = PLUGIN / "src" / "dev_loop" / "ci"
SCRIPT = CI / "size_cap.sh"
CONFIG = """\
paths: {changes: work/changes}
size:
  cap: 200
  advise: 100
  production: [app/**, "tools/**/*.sh", "tools/**/*.py"]
  exclude: ["**/tests/**", "**/test_*", "**/*.test.ts"]
  generated: [app/api/generated/**]
"""


def cap(root: Path, *rows: str, config: str = CONFIG) -> subprocess.CompletedProcess[str]:
    (root / "dev-loop.yaml").write_text(config, "utf-8")
    env = os.environ | {"CLAUDE_PROJECT_DIR": str(root), **PY}
    env.pop("SIZE_CAP", None)
    env.pop("SIZE_ADVISE", None)
    return subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [BASH, str(SCRIPT)],
        input="".join(f"{row}\n" for row in rows),
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
        env=env,
    )


def proposal(root: Path, change: str, text: str) -> None:
    folder = root / "work" / "changes" / change
    folder.mkdir(parents=True)
    (folder / "proposal.md").write_text(f"# P\n{text}", "utf-8")


def test_a_250_line_pr_is_refused_without_a_waiver(tmp_path: Path) -> None:
    result = cap(tmp_path, "200\t0\tapp/core/a.py", "30\t20\ttools/ci/b.sh")
    assert result.returncode == 1
    assert "250 production lines" in result.stdout
    assert "Size waiver:" in result.stdout


def test_a_waiver_on_the_proposal_lets_it_through(tmp_path: Path) -> None:
    proposal(tmp_path, "row", "**Size waiver:** lead 2026-01-01 — one migration\n")
    result = cap(tmp_path, "250\t0\tapp/migrations/0100.py", "3\t0\twork/changes/row/status.md")
    assert result.returncode == 0
    assert "work/changes/row/proposal.md" in result.stdout


def test_the_accepted_waiver_line_is_printed_for_the_reviewer(tmp_path: Path) -> None:
    """E3: no local check can tell who wrote the line, so the gate shows it to a person."""
    proposal(tmp_path, "row", "**Size waiver:** lead 2026-01-01 — one migration\n")
    result = cap(tmp_path, "250\t0\tapp/migrations/0100.py", "3\t0\twork/changes/row/status.md")
    last = result.stdout.splitlines()[-1]
    assert last == ("size cap: a person confirms this waiver in review: "
                    "**Size waiver:** lead 2026-01-01 — one migration")


def test_an_empty_waiver_or_one_outside_the_diff_does_not_count(tmp_path: Path) -> None:
    proposal(tmp_path, "row", "Size waiver:\n")
    proposal(tmp_path, "other", "Size waiver: lead 2026-01-01 — x\n")
    result = cap(tmp_path, "250\t0\tapp/a.py", "1\t0\twork/changes/row/status.md")
    assert result.returncode == 1


def test_excluded_generated_unlisted_and_binary_files_do_not_count(tmp_path: Path) -> None:
    result = cap(
        tmp_path,
        "500\t0\tapp/tests/test_a.py",
        "500\t0\tapp/api/generated/types.ts",
        "500\t0\tapp/ui/b.test.ts",
        "500\t0\ttools/test_record.py",
        "500\t0\tdocs/plans/plan.md",
        "500\t0\ttools/README.md",
        "-\t-\tapp/logo.png",
        "10\t0\tapp/with space.tsx",
    )
    assert result.returncode == 0
    assert "10 production lines" in result.stdout


def test_paths_outside_the_project_globs_do_not_count(tmp_path: Path) -> None:
    # no fixed folder counts: only the globs the project's dev-loop.yaml names do
    result = cap(tmp_path, "500\t0\tserver/src/a.py", "500\t0\tscripts/ci/x.sh")
    assert result.returncode == 0
    assert "0 production lines" in result.stdout


def test_deletions_count_and_over_advise_suggests_a_split(tmp_path: Path) -> None:
    result = cap(tmp_path, "0\t150\tapp/old.py")
    assert result.returncode == 0
    assert "150 production lines" in result.stdout
    assert "split" in result.stdout


def test_the_cap_comes_from_the_config(tmp_path: Path) -> None:
    raised = CONFIG.replace("cap: 200", "cap: 300")
    assert cap(tmp_path, "250\t0\tapp/a.py", config=raised).returncode == 0
    assert cap(tmp_path, "200\t0\tapp/a.py").returncode == 0
    assert cap(tmp_path, "201\t0\tapp/a.py").returncode == 1


def test_an_unreadable_config_stops_the_gate_naming_it(tmp_path: Path) -> None:
    alone = tmp_path / "ci" / "size_cap.sh"
    alone.parent.mkdir(parents=True)
    alone.write_bytes(SCRIPT.read_bytes())
    result = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [BASH, str(alone)],
        input="1\t0\tapp/a.py\n",
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=os.environ | PY | {"CLAUDE_PROJECT_DIR": str(tmp_path), "DL": str(tmp_path / "no-dl")},
    )
    assert result.returncode == 1
    assert "cannot read size.cap" in result.stderr
    assert "production lines" not in result.stdout


LEAD_WAIVER = (
    "Size waiver: lead 2026-01-01 — built and reviewed under an older, larger cap\n"
)


@pytest.mark.parametrize(
    ("line", "passes"),
    [
        (LEAD_WAIVER, True),
        ("**Size waiver:** tech lead 2026-01-02 — one migration\n", True),
        ("Size waiver: yes\n", False),
        ("Size waiver: lead 2026-01-01 one migration\n", False),
        ("Size waiver: PO — one migration\n", False),
        ("Size waiver: 2026-10-20 — no one named\n", False),
    ],
)
def test_the_waiver_needs_who_date_em_dash_and_reason(
    tmp_path: Path, line: str, *, passes: bool
) -> None:
    proposal(tmp_path, "row", line)
    result = cap(tmp_path, "250\t0\tapp/a.py", "1\t0\twork/changes/row/tasks.md")
    assert (result.returncode == 0) is passes


@pytest.mark.parametrize("name", ["test-size-cap.sh", "test-stack-rebase.sh"])
def test_the_shell_self_checks_pass(name: str) -> None:
    result = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [BASH, str(PLUGIN / "tests" / name)],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ | PY,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_dl_command_runs_the_stack_rebase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    from dev_loop.ci import stack_rebase  # noqa: PLC0415 - the module under test

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S607
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.autocrlf", "false"], check=True)  # noqa: S607
    (tmp_path / "dev-loop.yaml").write_text("branches: {integration: trunk}\n", "utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert stack_rebase.main([]) == 1  # no origin/trunk: the script's exit code comes back
    assert "origin/trunk" in capfd.readouterr().err


def test_the_files_this_task_wrote_are_neutral() -> None:
    files = [
        CI / "size_cap.sh", CI / "stack_rebase.sh", CI / "stack_rebase.py", Path(__file__),
        PLUGIN / "tests" / "test-size-cap.sh", PLUGIN / "tests" / "test-stack-rebase.sh",
    ]
    assert check(files) == []
