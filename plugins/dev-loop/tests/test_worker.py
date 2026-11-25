"""`dl worker`: config-driven checkout, worktrees, base and never list; refuses on a red doctor."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import doctor, schema
from dev_loop.tasks import worker

FILES = [
    "plugins/dev-loop/src/dev_loop/tasks/worker.py",
    "plugins/dev-loop/src/dev_loop/tasks/worker.sh",
    "plugins/dev-loop/tests/test_worker.py",
    "plugins/dev-loop/tests/test_worker_runner.py",
]
GREEN = [("Python", True, "3.12")]
LIMIT_VARIABLES = (
    "SLOTS", "POLL_SECONDS", "TIMEOUT_HOURS", "DOCKER_SLOTS", "DEFAULT_BASE", "DEFAULT_ROLE",
    "MAX_TURNS")
OLD_VARIABLE = "TB" + "ML_WORKER"  # the variable the worker used before the import


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: trunk-x}\n"
        "roles: {coordinator: {model: claude-opus-5-5}}\n"
        "workers:\n  w1:\n"
        f"    checkout: {tmp_path / 'co'}\n    worktrees: {tmp_path / 'wt'}\n"
        "    never: ['keep/*', 'deploy-*']\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DEV_LOOP_WORKER", "w1")
    for variable in ("DEV_LOOP_MODEL", *LIMIT_VARIABLES):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(doctor, "rows", lambda _root: GREEN)
    return tmp_path


@pytest.fixture
def execs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, str]]]:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(worker.os, "execvpe", lambda _f, a, e: calls.append((a, e)))
    return calls


def test_environment_comes_from_config(proj: Path) -> None:
    env = worker.environment(proj, "w1")
    assert env["DEV_LOOP_CHECKOUT"] == str(proj / "co")
    assert env["DEV_LOOP_WORKTREES"] == str(proj / "wt")
    assert env["DEV_LOOP_BASE"] == "trunk-x"
    assert env["DEV_LOOP_NEVER"] == "keep/*, deploy-*"
    assert env["DEV_LOOP_MODEL"] == "claude-opus-5-5"
    assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"  # a headless run has nothing to wake it
    assert env["STALE_HOURS"] == "24"  # the sweep's question/queue limit, overridable by env
    assert env["PATH"].startswith(str(worker.BIN))


def test_limits_default(proj: Path) -> None:
    env = worker.environment(proj, "w1")
    assert (env["SLOTS"], env["POLL_SECONDS"], env["TIMEOUT_HOURS"]) == ("3", "60", "3")
    assert env["DOCKER_SLOTS"] == "1"
    assert env["DEFAULT_BASE"] == "trunk-x"  # branches.integration
    assert env["DEFAULT_ROLE"] == "builder-backend"  # a run's context-cap role default


LIMITS = "    slots: 5\n    poll_seconds: 30\n    timeout_hours: 2\n    docker_slots: 2\n"


def test_limits_from_config(proj: Path) -> None:
    path = proj / "dev-loop.yaml"
    path.write_text(path.read_text() + LIMITS + "    default_base: next-x\n")
    env = worker.environment(proj, "w1")
    assert (env["SLOTS"], env["POLL_SECONDS"], env["TIMEOUT_HOURS"]) == ("5", "30", "2")
    assert (env["DOCKER_SLOTS"], env["DEFAULT_BASE"]) == ("2", "next-x")
    assert env["DEV_LOOP_BASE"] == "trunk-x"  # the pull request target stays the integration


def test_env_overrides_a_limit(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = proj / "dev-loop.yaml"
    path.write_text(path.read_text() + LIMITS)
    monkeypatch.setenv("SLOTS", "7")
    monkeypatch.setenv("DEFAULT_BASE", "other-x")
    env = worker.environment(proj, "w1")
    assert (env["SLOTS"], env["POLL_SECONDS"], env["DEFAULT_BASE"]) == ("7", "30", "other-x")


def test_max_turns_defaults_to_150_and_is_set_by_config(proj: Path) -> None:
    assert worker.environment(proj, "w1")["MAX_TURNS"] == "150"
    path = proj / "dev-loop.yaml"
    config = path.read_text()
    path.write_text(config + "    max_turns: 40\n")
    assert worker.environment(proj, "w1")["MAX_TURNS"] == "40"
    path.write_text(config + "    max_turns: 0\n")
    with pytest.raises(ValueError, match=r"workers\.w1\.max_turns"):
        worker.environment(proj, "w1")


def test_a_wrong_type_is_refused_by_its_dotted_name(proj: Path) -> None:
    path = proj / "dev-loop.yaml"
    path.write_text(path.read_text() + "    slots: many\n")
    with pytest.raises(ValueError, match=r"workers\.w1\.slots"):
        worker.environment(proj, "w1")


@pytest.mark.parametrize("value", ["0", "x", "1.5"])
def test_a_bad_env_limit_is_refused_by_its_dotted_name(
    proj: Path, monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("TIMEOUT_HOURS", value)
    with pytest.raises(ValueError, match=r"workers\.w1\.timeout_hours"):
        worker.environment(proj, "w1")


def test_main_names_a_bad_limit(
    proj: Path, execs: list, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SLOTS", "x")
    assert worker.main([]) == 2
    assert execs == []
    assert "workers.w1.slots" in capsys.readouterr().err


def test_model_env_wins(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_LOOP_MODEL", "other-model")
    assert worker.environment(proj, "w1")["DEV_LOOP_MODEL"] == "other-model"


def test_main_execs_the_script(proj: Path, execs: list) -> None:
    assert worker.main([]) == 0
    (args, env), = execs
    assert args == ["bash", str(worker.SCRIPT)]
    assert env["DEV_LOOP_WORKER"] == "w1"


def test_refuses_while_doctor_is_red(
    proj: Path, execs: list, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor, "rows", lambda _root: [("credential", False, "absent")])
    assert worker.main([]) == 1
    assert execs == []
    assert "credential" in capsys.readouterr().err


def test_agent_models_row_does_not_stop_the_worker(
    proj: Path, execs: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "rows", lambda _root: [("agent models", False, "x runs a")])
    assert worker.main([]) == 0
    assert len(execs) == 1


@pytest.mark.parametrize("name", ["", "nobody"])
def test_refuses_an_unknown_worker(
    proj: Path, execs: list, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    monkeypatch.setenv("DEV_LOOP_WORKER", name)
    assert worker.main([]) == 2
    assert execs == []


def test_refuses_settings_that_tag_every_run(
    proj: Path, execs: list, capsys: pytest.CaptureFixture[str],
) -> None:
    settings = proj / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"env": {"OTEL_RESOURCE_ATTRIBUTES": "a=b"}}', encoding="utf-8")
    assert worker.main([]) == 1
    assert execs == []
    assert str(settings) in capsys.readouterr().err


def test_refuses_unreadable_settings(
    proj: Path, execs: list, capsys: pytest.CaptureFixture[str],
) -> None:
    settings = proj / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{not json", encoding="utf-8")
    assert worker.main([]) == 1
    assert execs == []
    assert str(settings) in capsys.readouterr().err


def test_other_settings_env_is_fine(proj: Path, execs: list) -> None:
    settings = proj / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}}', encoding="utf-8")
    assert worker.main([]) == 0
    assert len(execs) == 1


def test_a_broken_config_fails_closed(
    proj: Path, execs: list, capsys: pytest.CaptureFixture[str],
) -> None:
    (proj / "dev-loop.yaml").write_text("version: 1\nworkers: [\n", encoding="utf-8")
    assert worker.main([]) == 2
    assert execs == []
    assert "dev-loop.yaml" in capsys.readouterr().err


def test_an_empty_base_fails_closed(proj: Path, execs: list) -> None:
    text = (proj / "dev-loop.yaml").read_text(encoding="utf-8")
    (proj / "dev-loop.yaml").write_text(text.replace("trunk-x", "''"), encoding="utf-8")
    assert worker.main([]) == 2
    assert execs == []


def _brief(env: dict[str, str]) -> str:
    done = subprocess.run(["bash", str(worker.SCRIPT), "--brief", "42"], env=env,
                          capture_output=True, text=True, check=True)
    return done.stdout


def test_brief_opens_with_the_headless_paragraph(proj: Path) -> None:
    text = _brief(worker.environment(proj, "w1"))
    assert text.startswith("You are running headless: run every command in the foreground")
    assert "never end your turn to wait for a notification" in text


def test_brief_names_config_values_and_no_literal_branch(proj: Path) -> None:
    text = _brief(worker.environment(proj, "w1"))
    assert "origin/trunk-x" in text
    assert "keep/*, deploy-*" in text
    assert f"{proj / 'wt'}/42" in text
    assert "dl tasks show 42" in text
    assert "rev-parse --verify" in text
    assert "docker-0.lock" in text  # task 42 of one Docker slot
    for literal in ("develop", "main", "master"):
        assert literal not in text
    script = worker.SCRIPT.read_text(encoding="utf-8")
    for literal in ("develop", "origin/main", "claude-", ".agent-worker-run", OLD_VARIABLE):
        assert literal not in script


def test_files_are_neutral() -> None:
    assert check(FILES) == []


def _with_worker_keys(proj: Path, extra: str) -> None:
    path = proj / "dev-loop.yaml"
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")


def test_worker_display_and_may_run_keys_validate(proj: Path) -> None:
    _with_worker_keys(
        proj, "    may_run: [backend]\n    reach: ssh from the laptop\n    accounts: [a, b]\n")
    env = worker.environment(proj, "w1")
    assert env["DEV_LOOP_MAY_RUN"] == "backend"
    assert "may_run" in schema.WORKER_OPTIONAL


def test_without_may_run_the_variable_is_empty_and_the_brief_does_not_restrict(proj: Path) -> None:
    env = worker.environment(proj, "w1")
    assert env["DEV_LOOP_MAY_RUN"] == ""
    assert "takes only these kinds" not in _brief(env)


def test_an_empty_may_run_is_refused(proj: Path) -> None:
    _with_worker_keys(proj, "    may_run: []\n")
    with pytest.raises(ValueError, match=r"workers\.w1\.may_run"):
        worker.environment(proj, "w1")


def test_brief_without_the_variable_is_not_empty(proj: Path) -> None:
    env = {k: v for k, v in worker.environment(proj, "w1").items() if k != "DEV_LOOP_MAY_RUN"}
    assert "dl tasks show 42" in _brief(env)


def test_the_kind_check_comes_before_the_task_is_started(proj: Path) -> None:
    _with_worker_keys(proj, "    may_run: [frontend]\n")
    text = _brief(worker.environment(proj, "w1"))
    assert text.index("takes only these kinds") < text.index("dl tasks start 42")


@pytest.mark.parametrize("extra", [
    "    may_run: [nope]\n", "    may_run: backend\n", "    reach: 3\n", "    accounts: [1]\n"])
def test_a_bad_worker_key_is_refused_by_name(proj: Path, extra: str) -> None:
    _with_worker_keys(proj, extra)
    with pytest.raises(ValueError, match=r"workers\.w1\.(may_run|reach|accounts)"):
        worker.environment(proj, "w1")


def test_brief_names_the_kinds_the_machine_takes(proj: Path) -> None:
    _with_worker_keys(proj, "    may_run: [frontend]\n")
    text = _brief(worker.environment(proj, "w1"))
    assert "takes only these kinds of work: frontend" in text
    assert "dl tasks comment 42" in text
