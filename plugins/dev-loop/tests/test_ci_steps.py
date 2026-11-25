"""`dl ci <step>`: each step's exit code is the wrapped step's; pin-check is red on a mismatch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_neutral import check

from dev_loop.ci import licences, steps
from dev_loop.records import review_records
from dev_loop.review import review_verdict

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "ci" / "azure" / "dev-loop-steps.yml"
FILES = [
    "plugins/dev-loop/src/dev_loop/ci/steps.py",
    "plugins/dev-loop/templates/ci/azure/dev-loop-steps.yml",
    "plugins/dev-loop/tests/test_ci_steps.py",
]


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True)
    return done.stdout.strip()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "noreply@example.invalid")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "dev-loop.yaml").write_text(
        "version: 1\nsize: {cap: 50, advise: 20, production: ['*.py']}\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_size_cap_passes_the_script_exit_through(project: Path) -> None:
    base = git(project, "rev-parse", "HEAD")
    (project / "a.py").write_text("x = 1\n" * 30, encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "small")
    assert steps.main(["size-cap", "--base", base]) == 0
    (project / "b.py").write_text("y = 2\n" * 30, encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "big")
    assert steps.main(["size-cap", "--base", base]) == 1


def test_size_cap_reads_head_when_the_base_config_is_refused(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    good = (project / "dev-loop.yaml").read_text(encoding="utf-8")
    (project / "dev-loop.yaml").write_text("version: 1\nold_key: {a: 1}\n", encoding="utf-8")
    git(project, "commit", "-qam", "old format")
    base = git(project, "rev-parse", "HEAD")
    (project / "dev-loop.yaml").write_text(good, encoding="utf-8")
    (project / "a.py").write_text("x = 1\n" * 60, encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "switch")
    assert steps.main(["size-cap", "--base", base]) == 1
    out = capsys.readouterr().out
    assert "size cap: base config refused (" in out
    assert "the stricter of the working tree's and the default cap: 50" in out


def refused_base(project: Path, head_config: str, lines: int) -> str:
    (project / "dev-loop.yaml").write_text("version: 1\nold_key: {a: 1}\n", encoding="utf-8")
    git(project, "commit", "-qam", "old format")
    base = git(project, "rev-parse", "HEAD")
    (project / "dev-loop.yaml").write_text(head_config, encoding="utf-8")
    (project / "a.py").write_text("x = 1\n" * lines, encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "switch")
    return base


def test_a_refused_base_does_not_let_head_raise_the_cap_above_the_default(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    base = refused_base(
        project, "version: 1\nsize: {cap: 900, advise: 100, production: ['*.py']}\n", 250)
    assert steps.main(["size-cap", "--base", base]) == 1
    assert "default cap: 200" in capsys.readouterr().out


def test_a_refused_base_keeps_a_lower_cap_from_head(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    base = refused_base(
        project, "version: 1\nsize: {cap: 50, advise: 20, production: ['*.py']}\n", 60)
    assert steps.main(["size-cap", "--base", base]) == 1
    assert "default cap: 50" in capsys.readouterr().out


def branch(project: Path, head_config: str, files: dict[str, int]) -> None:
    (project / "dev-loop.yaml").write_text(head_config, encoding="utf-8")
    for name, lines in files.items():
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_text("x = 1\n" * lines, encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-qm", "branch")


def test_a_branch_cannot_raise_its_own_cap(
    project: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    (project / "dev-loop.yaml").write_text(
        "version: 1\nsize: {cap: 200, advise: 100, production: ['*.py']}\n", encoding="utf-8")
    git(project, "commit", "-qam", "cap 200")
    base = git(project, "rev-parse", "HEAD")
    branch(project, "version: 1\nsize: {cap: 1000, advise: 100, production: ['*.py']}\n",
           {"a.py": 300})
    assert steps.main(["size-cap", "--base", base]) == 1
    out = capfd.readouterr().out
    assert "300 production lines is over the 200-line cap" in out
    assert f"cap, advice and globs from {base}" in out


def test_a_branch_cannot_move_its_lines_out_of_the_base_globs(
    project: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    base = git(project, "rev-parse", "HEAD")
    branch(project, "version: 1\nsize: {cap: 50, advise: 20, production: ['*.py'], "
           "exclude: ['a.py']}\n", {"a.py": 60})
    assert steps.main(["size-cap", "--base", base]) == 1
    assert "60 production lines" in capfd.readouterr().out


def test_a_refused_base_counts_by_the_working_tree_globs(
    project: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    (project / "dev-loop.yaml").write_text("version: 1\nold_key: {a: 1}\n", encoding="utf-8")
    git(project, "commit", "-qam", "old format")
    base = git(project, "rev-parse", "HEAD")
    branch(project, "version: 1\nsize: {cap: 200, advise: 150, production: ['**/*.py'], "
           "exclude: ['tests/**']}\n", {"lib/a.py": 120, "tests/test_a.py": 183})
    assert steps.main(["size-cap", "--base", base]) == 0
    out = capfd.readouterr().out
    assert "120 production lines" in out
    assert "default cap: 200; globs from the working tree" in out


def test_a_refused_base_counts_the_same_from_another_project_dir(
    project: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count never depends on which config the script's own `dl config get` would find."""
    (project / "dev-loop.yaml").write_text("version: 1\nold_key: {a: 1}\n", encoding="utf-8")
    git(project, "commit", "-qam", "old format")
    base = git(project, "rev-parse", "HEAD")
    branch(project, "version: 1\nsize: {cap: 200, advise: 150, production: ['**/*.py'], "
           "exclude: ['tests/**']}\n", {"lib/a.py": 120, "tests/test_a.py": 183})
    monkeypatch.chdir(project / "lib")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert steps.main(["size-cap", "--base", base]) == 0
    assert "120 production lines" in capfd.readouterr().out


def test_only_the_size_section_is_read_from_the_base(
    project: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    base = git(project, "rev-parse", "HEAD")
    (project / "notes" / "row").mkdir(parents=True)
    (project / "notes" / "row" / "proposal.md").write_text(
        "Size waiver: owner 2026-01-01 — one module\n", encoding="utf-8")
    branch(project, "version: 1\npaths: {changes: notes}\n"
           "size: {cap: 1000, advise: 100, production: ['*.py']}\n", {"a.py": 60})
    assert steps.main(["size-cap", "--base", base]) == 0
    assert "over the 50-line cap with the waiver in notes/row/proposal.md" in capfd.readouterr().out


def test_size_cap_bad_base_is_red(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert steps.main(["size-cap", "--base", "no-such-ref"]) == 1
    assert "size cap: not run, git diff no-such-ref failed" in capsys.readouterr().err


def test_a_step_with_no_base_takes_the_hooks(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PREPUSH_BASE", "no-such-ref")
    assert steps.main(["size-cap"]) != 0
    assert "git diff no-such-ref failed" in capsys.readouterr().err
    assert steps.main(["size-cap", "--base", "HEAD"]) == 0


@pytest.mark.parametrize(("step", "record"), [
    ("records-current", "current"), ("rules-current", "rules"), ("unparsed", "unparsed"),
])
def test_record_steps_pass_the_exit_through(
    project: Path, monkeypatch: pytest.MonkeyPatch, step: str, record: str,
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(review_records, "main", lambda argv: seen.append(argv) or 3)
    assert steps.main([step]) == 3
    assert seen == [[record, "--base", "origin/main"]]
    assert steps.main([step, "--base", "HEAD~1"]) == 3
    assert seen[-1] == [record, "--base", "HEAD~1"]


def test_verdict_selftest_green_then_red_when_the_contract_stops_refusing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert steps.main(["verdict-selftest"]) == 0
    monkeypatch.setattr(review_verdict, "errors", lambda *_: [])
    assert steps.main(["verdict-selftest"]) == 1


def test_hooks_selftest_runs_the_registered_hooks(project: Path) -> None:
    assert steps.main(["hooks-selftest"]) == 0


def fake_plugin(tmp_path: Path, version: str, hook: str = "exit 0") -> Path:
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
    (root / "hooks").mkdir()
    (root / "hooks" / "h.sh").write_text(hook + "\n")
    command = 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/h.sh"'
    registered = {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}
    (root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": registered}))
    return root


def test_hooks_selftest_red_on_a_crashing_hook(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(fake_plugin(project, "1.0.0", "exit 1")))
    assert steps.main(["hooks-selftest"]) == 1
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(fake_plugin(project / "b", "1.0.0", "exit 2")))
    assert steps.main(["hooks-selftest"]) == 0  # 2 is a hook's answer, not a crash


def test_hooks_selftest_without_the_plugin_is_not_run(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(project / "absent"))
    assert steps.main(["hooks-selftest"]) == 1


def settings(project: Path, pin: str | None = None, ref: str | None = None) -> None:
    body: dict[str, object] = {}
    if pin:
        body["pluginPins"] = {"dev-loop": pin}
    if ref:
        body["extraKnownMarketplaces"] = {"dev-loop": {"source": {"source": "git", "ref": ref}}}
    (project / ".claude").mkdir(exist_ok=True)
    (project / ".claude" / "settings.json").write_text(json.dumps(body))


def test_pin_check_green_when_every_pin_matches(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(fake_plugin(project, "0.2.0")))
    settings(project, pin="0.2.0", ref="v0.2.0")
    assert steps.main(["pin-check", "--tag", "v0.2.0"]) == 0


@pytest.mark.parametrize(("pin", "ref", "tag"), [
    ("0.1.0", "v0.2.0", "v0.2.0"), ("0.2.0", "v0.1.0", "v0.2.0"), ("0.2.0", "v0.2.0", "v0.1.0"),
])
def test_pin_check_red_on_a_mismatch(
    project: Path, monkeypatch: pytest.MonkeyPatch, pin: str, ref: str, tag: str,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(fake_plugin(project, "0.2.0")))
    settings(project, pin=pin, ref=ref)
    assert steps.main(["pin-check", "--tag", tag]) == 1


def test_pin_check_red_with_no_pin_at_all(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(fake_plugin(project, "0.2.0")))
    assert steps.main(["pin-check"]) == 1


def test_licences_step_dispatches_to_the_checker(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(licences, "check", lambda root: seen.append(root) or 1)
    assert steps.main(["licences"]) == 1
    assert seen == [project]


def test_done_rule_judges_a_finished_part(
        project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = project / ".dev-loop" / "changes" / "c1"
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(
        "## Part a\n### T1: a\n- Status: done\n## Part b\n### T2: b\n- Status: todo\n",
        encoding="utf-8")
    assert steps.done_rule(project, ["c1"]) == 1
    assert "c1: missing" in capsys.readouterr().out


def test_unknown_step_is_refused() -> None:
    with pytest.raises(SystemExit):
        steps.main(["no-such-step"])


def test_template_installs_the_pinned_tag_from_a_variable() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "://" not in text  # the repo URL is a template variable, never a literal
    assert '"dev-loop @ git+${{ parameters.repo }}@${{ parameters.tag }}"' in text
    template = yaml.safe_load(text)
    names = {p["name"] for p in template["parameters"]}
    assert {"repo", "tag"} <= names
    scripts = " ".join(s.get("script", "") for s in template["steps"])
    ran = {word.split()[0] for word in scripts.split("dl ci ")[1:]}
    # fix-proves is unconfigured by default (exit 1, not run), so no template runs it
    assert ran == set(steps.STEPS) - {"fix-proves"}


def test_files_are_neutral() -> None:
    assert check(FILES) == []


GITHUB = PLUGIN / "templates" / "ci" / "github" / "dev-loop.yml"


def test_github_template_installs_the_pinned_tag_and_runs_every_step() -> None:
    text = GITHUB.read_text(encoding="utf-8")
    assert "://" not in text  # the repo URL is a repository variable, never a literal
    assert "${{ vars.DEV_LOOP_REPO }}" in text
    assert '"dev-loop @ git+${DEV_LOOP_REPO}@${DEV_LOOP_TAG}"' in text
    flow = yaml.safe_load(text)
    assert flow["env"]["DEV_LOOP_TAG"] == "{{tag}}"
    job = next(iter(flow["jobs"].values()))
    scripts = " ".join(s.get("run", "") for s in job["steps"])
    ran = {word.split()[0] for word in scripts.split("dl ci ")[1:]}
    # fix-proves is unconfigured by default (exit 1, not run), so no template runs it
    assert ran == set(steps.STEPS) - {"fix-proves"}


def test_github_template_is_neutral() -> None:
    assert check(["plugins/dev-loop/templates/ci/github/dev-loop.yml"]) == []
