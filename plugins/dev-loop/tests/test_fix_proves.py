"""`dl ci fix-proves`: a fix's changed test must fail against the base's source, never a silent pass.

A toy project: `src/calc.py` holds a bug on the base; the head fixes it and adds a test. The
test command runs through `verify.runner`, which here puts `src` on the path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import config, schema
from dev_loop.ci import steps

PLUGIN = Path(__file__).resolve().parents[1]
FILES = [
    "plugins/dev-loop/src/dev_loop/ci/fix_proves.sh",
    "plugins/dev-loop/tests/test_fix_proves.py",
]
CONFIGURED = (
    "version: 1\n"
    "verify: {runner: 'env PYTHONPATH=src'}\n"
    "fix_proves:\n"
    "  src: ['src/*.py']\n"
    "  tests: ['tests/test_*.py']\n"
    f"  command: '{sys.executable} -m pytest -q -p no:cacheprovider'\n"
)
TEST = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True)
    return done.stdout.strip()


def commit(root: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def toy(root: Path, monkeypatch: pytest.MonkeyPatch, config: str) -> str:
    """A project whose base holds a buggy add(); returns the base commit."""
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "noreply@example.invalid")
    git(root, "config", "user.name", "t")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN))
    monkeypatch.setenv("HOME", str(root / "home"))
    for name in ("FIX_PROVES_BRANCH", "FIX_PROVES_TARGET", *sum(steps.PR_BRANCHES.values(), ())):
        monkeypatch.delenv(name, raising=False)  # a CI running this suite sets its own
    files = {"dev-loop.yaml": config, "src/calc.py": "def add(a, b):\n    return a - b\n"}
    base = commit(root, files, "base")
    git(root, "checkout", "-q", "-b", "fix/x")  # a fix PR, as the gate runs only for those
    return base


def test_a_fix_whose_new_test_fails_on_the_base_is_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    commit(tmp_path, {"src/calc.py": "def add(a, b):\n    return a + b\n",
                      "tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "fails on the base" in capfd.readouterr().out
    assert not list(tmp_path.glob(".fix-proves-*"))  # the base copy is removed


def test_a_fix_whose_new_test_passes_on_the_base_is_red_naming_the_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    passing = "from calc import add\n\n\ndef test_zero() -> None:\n    assert add(0, 0) == 0\n"
    commit(tmp_path, {"src/calc.py": "def add(a, b):\n    return a + b\n",
                      "tests/test_zero.py": passing}, "not a proof")
    assert steps.main(["fix-proves", "--base", base]) == 1
    out = capfd.readouterr().out
    assert "FAILED" in out
    assert "tests/test_zero.py" in out


def test_a_test_command_that_cannot_start_is_red_not_a_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    config = CONFIGURED.replace(sys.executable, "/no/such/python")
    base = toy(tmp_path, monkeypatch, config)
    commit(tmp_path, {"tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 3
    assert "could not run" in capfd.readouterr().out


def test_not_configured_says_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, "version: 1\n")
    commit(tmp_path, {"tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 1
    assert "not run: fix_proves not configured" in capfd.readouterr().out


def test_no_changed_test_is_skipped_and_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    commit(tmp_path, {"src/calc.py": "def add(a, b):\n    return a + b\n"}, "fix, no test")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "skipped" in capfd.readouterr().out


def test_the_schema_refuses_a_bad_fix_proves_value() -> None:
    with pytest.raises(ValueError, match="fix_proves.src"):
        schema.validate_section({"src": [1], "tests": [], "command": None}, "fix_proves",
                                schema.TEXT["fix_proves"])


def test_files_are_neutral() -> None:
    assert check(FILES) == []


FIXED = {"src/calc.py": "def add(a, b):\n    return a + b\n"}
FIXTURE_ERROR = ("from pytest import fixture\n\n\n@fixture\ndef boom() -> None:\n"
                 "    raise RuntimeError(\"setup\")\n\n\ndef test_add(boom) -> None:\n    pass\n")
VERDICTS = {  # row: (test file text, config tweak, exit, a phrase of the line)
    "no_test_selected_exit_5": ("def helper() -> None:\n    pass\n", None, 0, "skipped"),
    "no_test_ran_no_report": (TEST, "-p no:cacheprovider", 3, "no test ran"),
    "assertion_failure": (TEST, None, 0, "fails on the base"),
    "collection_error": ("from calc import nothing\n\n\ndef test_x() -> None:\n    pass\n",
                         None, 0, "cannot import"),
    "other_error": (FIXTURE_ERROR, None, 3, "not proved"),
    "all_pass": ("def test_ok() -> None:\n    assert True\n", None, 1, "FAILED"),
}


@pytest.mark.parametrize("row", VERDICTS)
def test_the_verdict_table_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], row: str,
) -> None:
    text, bogus, code, phrase = VERDICTS[row]
    config = CONFIGURED.replace("-p no:cacheprovider", "--no-such-option") if bogus else CONFIGURED
    base = toy(tmp_path, monkeypatch, config)
    commit(tmp_path, {**FIXED, "tests/test_row.py": text}, row)
    assert steps.main(["fix-proves", "--base", base]) == code
    assert phrase in capfd.readouterr().out


def test_a_branch_that_is_not_a_fix_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    git(tmp_path, "checkout", "-q", "-b", "feature/y")
    commit(tmp_path, {**FIXED, "tests/test_calc.py": "def test_ok() -> None:\n    pass\n"}, "x")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "not a fix PR" in capfd.readouterr().out


def test_a_marker_in_a_changed_status_makes_any_branch_a_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    git(tmp_path, "checkout", "-q", "-b", "feature/y")
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST,
                      ".dev-loop/changes/c/status.md": "fixes-defect: yes\n"}, "x")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "fails on the base" in capfd.readouterr().out


def test_a_pr_aimed_at_another_branch_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    monkeypatch.setenv("FIX_PROVES_TARGET", "release")
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "targets release" in capfd.readouterr().out


def test_a_base_git_cannot_diff_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    toy(tmp_path, monkeypatch, CONFIGURED)
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", "no-such-ref"]) == 1
    assert "not run, git diff failed" in capfd.readouterr().out


def test_a_target_given_as_a_full_ref_is_read_as_its_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    integration = config.load(tmp_path).get("branches.integration")
    monkeypatch.setenv("FIX_PROVES_TARGET", f"refs/heads/{integration}")
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "fails on the base" in capfd.readouterr().out


def test_a_detached_head_with_no_branch_from_the_host_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST}, "fix")
    git(tmp_path, "checkout", "-q", "--detach")
    assert steps.main(["fix-proves", "--base", base]) == 1
    assert "not run, cannot tell the branch" in capfd.readouterr().out


@pytest.mark.parametrize("source,target,prefix", [
    ("GITHUB_HEAD_REF", "GITHUB_BASE_REF", ""),
    ("SYSTEM_PULLREQUEST_SOURCEBRANCH", "SYSTEM_PULLREQUEST_TARGETBRANCH", "refs/heads/"),
])
def test_a_detached_head_reads_the_pr_branches_from_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    source: str, target: str, prefix: str,
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    integration = config.load(tmp_path).get("branches.integration")
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST}, "fix")
    git(tmp_path, "checkout", "-q", "--detach")
    monkeypatch.setenv(source, f"{prefix}fix/x")
    monkeypatch.setenv(target, f"{prefix}{integration}")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "fails on the base" in capfd.readouterr().out
    monkeypatch.setenv(target, f"{prefix}release")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "targets release" in capfd.readouterr().out


NOT_PYTEST = (  # takes the report path first and refuses any option
    "import sys\n"
    "assert not [a for a in sys.argv[1:] if a.startswith('--')], sys.argv\n"
    "body = '<testsuite><testcase name=\"t\"><failure/></testcase></testsuite>'\n"
    "open(sys.argv[1], 'w').write(body)\n"
    "raise SystemExit(1)\n"
)


def test_a_command_that_is_not_pytest_names_its_report_with_a_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    config_text = CONFIGURED.replace("-m pytest -q -p no:cacheprovider", "run.py {report}")
    base = toy(tmp_path, monkeypatch, config_text)
    commit(tmp_path, {**FIXED, "run.py": NOT_PYTEST, "tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "fails on the base" in capfd.readouterr().out


def test_no_report_says_the_command_must_write_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED.replace("-p no:cacheprovider", "--no-such-option"))
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 3
    assert "must write a junit report" in capfd.readouterr().out


def test_a_report_left_in_the_tree_is_never_read_as_this_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED.replace("-p no:cacheprovider", "--no-such-option"))
    old = '<testsuite><testcase name="t"><failure/></testcase></testsuite>'
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST, ".fix-proves-junit.xml": old}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 3
    assert "no test ran" in capfd.readouterr().out


def test_no_python_to_read_the_report_is_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    base = toy(tmp_path, monkeypatch, CONFIGURED)
    commit(tmp_path, {**FIXED, "tests/test_calc.py": TEST}, "fix")
    launcher = tmp_path.parent / f"{tmp_path.name}-dl"  # dl keeps a real Python; the script has none
    launcher.write_text(f'DL_PYTHON="{sys.executable}" exec bash "{PLUGIN}/bin/dl" "$@"\n',
                        encoding="utf-8")
    env = {**os.environ, "DL": str(launcher), "DL_PYTHON": "/no/such/python"}
    monkeypatch.setattr(steps, "script_env", lambda: env)
    assert steps.main(["fix-proves", "--base", base]) == 1
    assert "not run, /no/such/python not found" in capfd.readouterr().out


def test_a_project_below_the_git_top_names_its_paths_from_the_top(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    below = (CONFIGURED.replace("PYTHONPATH=src", "PYTHONPATH=proj/src")
             .replace("'src/*.py'", "'proj/src/*.py'")
             .replace("'tests/test_*.py'", "'proj/tests/test_*.py'"))
    toy(tmp_path, monkeypatch, "version: 1\n")
    base = commit(tmp_path, {"proj/dev-loop.yaml": below,
                             "proj/src/calc.py": "def add(a, b):\n    return a - b\n"}, "base")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    commit(tmp_path, {"proj/src/calc.py": FIXED["src/calc.py"],
                      "proj/tests/test_calc.py": TEST}, "fix")
    assert steps.main(["fix-proves", "--base", base]) == 0
    assert "fails on the base" in capfd.readouterr().out
