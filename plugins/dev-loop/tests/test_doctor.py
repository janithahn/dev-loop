"""`dl doctor`: each row red and green; the credential value is never printed."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import doctor
from dev_loop.hosts import HostError, ado, github

SECRET = "PLANTED-DOCTOR-SECRET-77"
STEPS = "verify: {steps: {unit: 'true'}}\n"
HOOKS_PATH = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
              "GIT_CONFIG_VALUE_0": ".githooks"}
FILES = ["plugins/dev-loop/src/dev_loop/doctor.py", "plugins/dev-loop/tests/test_doctor.py"]


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "dev-loop.yaml").write_text(
        "version: 1\nhost: {org: a, project: p, repo: r}\n" + STEPS)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    for key, value in HOOKS_PATH.items():  # core.hooksPath set for every git call, as dl init does
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", str(tmp_path))  # the heartbeat the context-gate row reads
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)  # the row reads the real plugin
    monkeypatch.setattr(doctor, "_tools_row", lambda: ("tools", True, "found"))  # PATH-free
    passing = ("hook self-test", True, "ok")  # PATH-free too; the real row: test_doctor_hooks.py
    monkeypatch.setattr("dev_loop.doctor_hooks.self_test_row", lambda _plugin: passing)
    monkeypatch.setattr("dev_loop.doctor_hooks.gate_state", lambda _plugin: None)  # no self-test
    monkeypatch.setattr(ado, "credential", lambda: SECRET)
    monkeypatch.setattr(ado, "request", lambda *a, **k: {})
    monkeypatch.setattr("dev_loop.clean.disk_pct", lambda _p: 10)  # a low, green disk by default
    monkeypatch.setattr("dev_loop.clean.inode_pct", lambda _p: 0)  # inodes out of the gauge
    (tmp_path / ".dev-loop").mkdir()
    (tmp_path / ".dev-loop" / "RULES.md").write_text("rules\n")
    (tmp_path / "CLAUDE.md").write_text("# p\n\n@.dev-loop/RULES.md\n")
    return tmp_path


def _row(name: str) -> tuple[str, bool, str]:
    import dev_loop.config as cfg

    return next(r for r in doctor.rows(cfg.project_root()) if r[0] == name)


def test_all_green(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "RED" not in out
    assert SECRET not in out


def test_credential_absent_is_red_and_value_never_printed(
    proj: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def none() -> str:
        raise HostError("no credential")

    monkeypatch.setattr(ado, "credential", none)
    assert doctor.main([]) == 1
    assert _row("credential")[1:] == (False, "absent")
    monkeypatch.setattr(ado, "credential", lambda: SECRET)
    doctor.main([])
    assert SECRET not in capsys.readouterr().out


def test_host_unreachable_is_red(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> dict:
        raise HostError("host call failed: URLError")

    monkeypatch.setattr(ado, "request", boom)
    assert _row("host reachable")[1] is False
    assert doctor.main([]) == 1



def test_rules_not_imported_is_red(proj: Path) -> None:
    assert _row("rules imported")[1] is True
    (proj / "CLAUDE.md").write_text("# p\n")
    assert _row("rules imported")[1:] == (False, "run dl init")
    (proj / "CLAUDE.md").write_text("# p\n\n@.dev-loop/RULES.md\n")
    (proj / ".dev-loop" / "RULES.md").unlink()
    assert _row("rules imported")[1:] == (False, "run dl init")
    (proj / "CLAUDE.md").unlink()
    assert _row("rules imported")[1:] == (False, "run dl init")


def test_rules_import_accepted_from_agents_md(proj: Path) -> None:
    pointer = "Follow the working rules in [.dev-loop/RULES.md](.dev-loop/RULES.md)."
    (proj / "CLAUDE.md").write_text("@AGENTS.md\n")
    assert _row("rules imported")[1:] == (False, "run dl init")
    (proj / "AGENTS.md").write_text(f"# law\n\n{pointer}\n")
    assert _row("rules imported")[1:] == (True, "ok")
    (proj / "CLAUDE.md").unlink()
    assert _row("rules imported")[1:] == (True, "ok")
    (proj / "AGENTS.md").write_text("# law\n\n@.dev-loop/RULES.md\n")
    assert _row("rules imported")[1:] == (True, "ok")
    (proj / ".dev-loop" / "RULES.md").unlink()
    assert _row("rules imported")[1:] == (False, "run dl init")


def test_host_web_page_is_named(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.request

    monkeypatch.undo()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setattr(ado, "credential", lambda: SECRET)
    page = io.BytesIO(b"<!DOCTYPE html><html>sign in</html>")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: page)
    row = _row("host reachable")
    assert row[1:] == (
        False,
        "host answered a web page, not the API: check host.org, host.project and the credential",
    )
    assert SECRET not in row[2]

def test_bad_config_is_red(proj: Path) -> None:
    (proj / "dev-loop.yaml").write_text("version: 1\nsize: {cap: not-a-number}\n")
    assert _row("dev-loop.yaml")[1] is False


def test_disk_row_green_below_threshold_red_at_it(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _row("disk")[1] is True  # 10% from the fixture, under the 80% default
    monkeypatch.setattr("dev_loop.clean.disk_pct", lambda _p: 80)
    row = _row("disk")
    assert row[1] is False
    assert "dl clean" in row[2] and ">= 80%" in row[2]
    assert doctor.main([]) == 1


BASE_TIME = 1_700_000_000  # a fixed epoch so day counts are exact, not clock-relative


def _commit(root: Path, text: str, when: int) -> str:
    env = os.environ | {
        "GIT_AUTHOR_DATE": f"@{when} +0000", "GIT_COMMITTER_DATE": f"@{when} +0000",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (root / "f").write_text(text, encoding="utf-8")
    run = lambda *a, **k: subprocess.run(["git", "-C", str(root), *a], check=True,  # noqa: E731
                                         capture_output=True, **k)
    run("add", "f")
    run("commit", "-q", "-m", text, env=env)
    return run("rev-parse", "HEAD", text=True).stdout.strip()


def _repo(root: Path, behind_days: int | None) -> None:
    """A checkout on main; origin/main sits `behind_days` ahead of HEAD (None: no origin/main)."""
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True,
                   capture_output=True)
    head = _commit(root, "c0", BASE_TIME)
    if behind_days is None:
        return
    tip = _commit(root, "c1", BASE_TIME + behind_days * 86400)
    subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", tip],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "reset", "-q", "--hard", head], check=True,
                   capture_output=True)


def test_checkout_fresh_is_green(proj: Path) -> None:
    _repo(proj, 0)
    assert _row("checkout")[1:] == (True, "0d behind origin/main")


def test_checkout_at_threshold_is_green(proj: Path) -> None:
    _repo(proj, doctor.STALE_DAYS)
    assert _row("checkout")[1:] == (True, f"{doctor.STALE_DAYS}d behind origin/main")


def test_checkout_stale_is_red(proj: Path) -> None:
    _repo(proj, doctor.STALE_DAYS + 3)
    row = _row("checkout")
    assert row[1] is False
    assert f"{doctor.STALE_DAYS + 3}d behind origin/main" in row[2] and "git pull" in row[2]
    assert doctor.main([]) == 1


def test_checkout_without_the_ref_is_green(proj: Path) -> None:
    _repo(proj, None)
    assert _row("checkout")[1:] == (True, "no origin/main to compare")


def test_pin_mismatch_is_red_and_match_green(proj: Path) -> None:
    (proj / ".claude").mkdir()
    settings = proj / ".claude" / "settings.json"
    settings.write_text(json.dumps(_pinned("v9.9.9")))
    assert _row("plugin version")[1] is False
    settings.write_text(json.dumps(_pinned(f"v{doctor._plugin_version()}")))
    assert _row("plugin version")[1] is True


def _pinned(ref: str) -> dict:
    """The pin as `dl init` writes it."""
    source = {"source": "github", "repo": "x/dev-loop", "ref": ref}
    return {"extraKnownMarketplaces": {"dev-loop": {"source": source, "autoUpdate": False}}}


def test_missing_pin_is_green(proj: Path) -> None:
    (proj / ".claude").mkdir()
    settings = proj / ".claude" / "settings.json"
    settings.write_text(json.dumps({"extraKnownMarketplaces": {"other": {}}}))
    assert _row("plugin version")[1] is True
    settings.write_text(json.dumps({"extraKnownMarketplaces": {"dev-loop": {"source": {}}}}))
    assert _row("plugin version")[1] is True


def test_old_python_is_red(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "MIN_PYTHON", (99, 0))
    assert _row("Python")[1] is False


def test_pyyaml_missing_is_red(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real = builtins.__import__

    def fake(name: str, *a: object, **k: object):
        if name == "yaml":
            raise ModuleNotFoundError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert _row("PyYAML")[1] is False


def _fake_plugin(tmp_path: Path, *, registered: bool = True, hook: bool = True) -> Path:
    """A plugin dir with a hooks.json and the hook file, to drive the context-gate row."""
    root = tmp_path / "plugin" / "hooks"
    root.mkdir(parents=True)
    call = {"command": 'python3 -S "${CLAUDE_PLUGIN_ROOT}/hooks/context-gate.py"'}
    group = {"hooks": [call]} if registered else {"matcher": "Edit", "hooks": [call]}
    (root / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [group]}}))
    if hook:
        (root / "context-gate.py").write_text("x\n")
    return root.parent


def test_context_gate_green_on_the_real_plugin(proj: Path) -> None:
    row = _row("context gate")
    assert row[1:] == (True, "registered, self-check ok")


def test_context_gate_red_when_not_registered(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(_fake_plugin(proj, registered=False)))
    row = _row("context gate")
    assert row[1] is False and "not registered" in row[2]


def test_context_gate_red_when_the_hook_file_is_missing(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(_fake_plugin(proj, hook=False)))
    row = _row("context gate")
    assert row[1] is False and "missing" in row[2]


def test_context_gate_red_when_the_self_check_fails(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dev_loop.dashboard import gate

    monkeypatch.setattr(gate, "self_check", lambda: False)
    row = _row("context gate")
    assert row[1] is False and "self-check failed" in row[2]


def test_context_gate_notes_a_stale_heartbeat(proj: Path) -> None:
    import os
    import time

    beat = proj / ".claude" / "dev-loop" / "gate-heartbeat"
    beat.parent.mkdir(parents=True)
    beat.write_bytes(b"")
    old = time.time() - 8 * 86400
    os.utime(beat, (old, old))
    row = _row("context gate")
    assert row[1] is False and "gate not seen running since" in row[2]
    seen = time.strftime("%Y-%m-%d", time.localtime(old))
    assert seen in row[2]


def _stale_beat(proj: Path) -> None:
    import os
    import time

    beat = proj / ".claude" / "dev-loop" / "gate-heartbeat"
    beat.parent.mkdir(parents=True)
    beat.write_bytes(b"")
    old = time.time() - 8 * 86400
    os.utime(beat, (old, old))


def test_a_quiet_gate_the_self_test_finds_ok_is_green(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_beat(proj)
    monkeypatch.setattr("dev_loop.doctor_hooks.gate_state", lambda _plugin: "ok")
    assert _row("context gate")[1:] == (True, "registered, self-check ok")


def test_a_gate_the_self_test_names_broken_is_red(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stale_beat(proj)
    monkeypatch.setattr("dev_loop.doctor_hooks.gate_state", lambda _plugin: "broken")
    row = _row("context gate")
    assert row[1] is False and "hook self-test names the gate broken" in row[2]


def test_gate_state_reads_the_self_test_list(tmp_path: Path) -> None:
    from dev_loop import doctor_hooks

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    script = hooks / "self-test.py"
    script.write_text("print('ok done-guard')\nprint('broken context-gate')\n", "utf-8")
    assert doctor_hooks.gate_state(tmp_path) == "broken"
    script.write_text("print('ok context-gate')\n", "utf-8")
    doctor_hooks.self_test_lines.cache_clear()  # as each run of doctor does
    assert doctor_hooks.gate_state(tmp_path) == "ok"
    script.write_text("raise SystemExit(1)\n", "utf-8")
    doctor_hooks.self_test_lines.cache_clear()
    assert doctor_hooks.gate_state(tmp_path) is None


def test_context_gate_stays_green_with_a_fresh_heartbeat(proj: Path) -> None:
    beat = proj / ".claude" / "dev-loop" / "gate-heartbeat"
    beat.parent.mkdir(parents=True)
    beat.write_bytes(b"")  # just written: within the 7-day window
    assert _row("context gate")[1] is True


def test_files_neutral() -> None:
    root = Path(__file__).resolve().parents[3]
    assert check([root / f for f in FILES]) == []


FAKE_GH = """#!/bin/sh
[ "$1 $2" = "auth status" ] || exit 9
echo "Token: {secret}"
exit "$(cat "$(dirname "$0")/code")"
"""


@pytest.fixture
def gh_proj(proj: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (proj / "dev-loop.yaml").write_text(
        "version: 1\nhost: {provider: github, org: o, repo: r}\n" + STEPS)
    bin_dir = proj / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(secret=SECRET))
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return proj


def test_github_logged_in_is_green(gh_proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (gh_proj / "bin" / "code").write_text("0\n")
    assert _row("host reachable")[1:] == (True, "gh logged in")
    assert _row("credential")[1:] == (True, "gh logged in")
    assert doctor.main([]) == 0
    assert SECRET not in capsys.readouterr().out


def test_github_logged_out_is_red(gh_proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (gh_proj / "bin" / "code").write_text("1\n")
    assert _row("host reachable")[1:] == (False, "gh not logged in: run gh auth login")
    assert _row("credential")[1:] == (False, "gh not logged in")
    assert doctor.main([]) == 1
    assert SECRET not in capsys.readouterr().out


@pytest.fixture
def local_proj(proj: Path, tmp_path: Path) -> Path:
    """A git repo whose `hub` remote is a reachable bare repository, provider local."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    subprocess.run(["git", "-C", str(proj), "remote", "add", "hub", str(bare)], check=True)
    (proj / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {remote: hub}\nhost: {provider: local}\n" + STEPS)
    return proj


def test_local_host_reachable_then_unreachable(local_proj: Path) -> None:
    row = _row("host reachable")
    assert row[1] is True and "hub reachable with git" in row[2]
    assert _row("credential")[1:] == (True, "git only; none needed")
    assert doctor.main([]) == 0
    subprocess.run(["git", "-C", str(local_proj), "remote", "set-url", "hub",
                    str(local_proj / "missing.git")], check=True)
    row = _row("host reachable")
    assert row[1] is False and "unreachable with git" in row[2]
    assert doctor.main([]) == 1


def test_github_without_gh_is_red(gh_proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github.shutil, "which", lambda _: None)
    assert _row("host reachable")[1:] == (False, "gh not found")
    assert _row("credential")[1:] == (False, "gh not found")


def _json(proj: Path) -> list[dict[str, str]]:
    import dev_loop.config as cfg

    return doctor.json_rows(cfg.project_root())


def _one(rows: list[dict[str, str]], name: str) -> dict[str, str]:
    return next(r for r in rows if r["name"] == name)


def test_json_shape_every_row_has_the_four_fields(proj: Path) -> None:
    rows = _json(proj)
    assert rows, "at least one row"
    for row in rows:
        assert set(row) == {"name", "status", "detail", "fix"}
        assert row["status"] in {"ok", "red", "optional"}
        assert all(isinstance(value, str) for value in row.values())


def test_json_main_prints_a_json_array_without_the_secret(
    proj: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert doctor.main(["--json"]) == 0
    out = capsys.readouterr().out
    assert SECRET not in out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert {"name", "status", "detail", "fix"} <= set(parsed[0])


def test_json_ok_rows_have_no_fix_and_reds_have_one(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def none() -> str:
        raise HostError("no credential")

    monkeypatch.setattr(ado, "credential", none)
    rows = _json(proj)
    assert _one(rows, "dev-loop.yaml")["fix"] == ""  # green row: no instruction
    cred = _one(rows, "credential")
    assert cred["status"] == "red"
    assert "Git Credential Manager" in cred["fix"]
    assert SECRET not in json.dumps(rows)


def test_json_reds_name_their_fix(proj: Path) -> None:
    (proj / "CLAUDE.md").write_text("# p\n")  # rules no longer imported
    assert _one(_json(proj), "rules imported")["fix"] == "run dl init"
    (proj / "dev-loop.yaml").write_text("version: 1\nsize: {cap: not-a-number}\n")
    rows = _json(proj)
    assert "size.cap" in _one(rows, "dev-loop.yaml")["fix"]  # the error says what to change
    assert {"verify steps", "host reachable", "credential"}.isdisjoint(r["name"] for r in rows)


def test_json_github_credential_fix_is_gh_auth_login(
    gh_proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github.shutil, "which", lambda _: None)
    rows = _json(gh_proj)
    assert _one(rows, "credential")["fix"] == "gh auth login"


def test_json_telemetry_is_optional_off_and_ok_when_on(proj: Path) -> None:
    row = _one(_json(proj), "telemetry")
    assert row["status"] == "optional"
    assert row["fix"] == "open the Telemetry section below"
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"DEV_LOOP_TELEMETRY": "1"}})
    )
    on = _one(_json(proj), "telemetry")
    assert on["status"] == "ok"
    assert on["fix"] == ""


def test_json_context_gate_red_until_the_plugin_is_registered(proj: Path) -> None:
    row = _one(_json(proj), "context gate")
    assert row["status"] == "red"
    assert row["fix"] == "restart Claude Code; check the plugin is enabled"
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {"dev-loop": {"source": {}}}})
    )
    assert _one(_json(proj), "context gate")["status"] == "ok"


def test_json_context_gate_red_when_enabled_but_not_registered(
    proj: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {"dev-loop": {"source": {}}}})
    )
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(_fake_plugin(proj, registered=False)))
    row = _one(_json(proj), "context gate")
    assert row["status"] == "red"
    assert "not registered" in row["fix"]


def test_githooks_unset_is_red_with_its_fix(proj: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _row("githooks")[1:] == (True, "core.hooksPath .githooks")
    monkeypatch.delenv("GIT_CONFIG_COUNT")
    fix = "git config core.hooksPath .githooks"
    assert _row("githooks")[1:] == (False, f"core.hooksPath unset, the githooks never run: {fix}")
    assert _one(_json(proj), "githooks")["fix"] == fix
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", ".husky")
    row = _row("githooks")
    assert row[1] is False
    assert "is .husky, not .githooks" in row[2]
    assert doctor.main([]) == 1


def test_verify_steps_empty_is_red_with_its_fix(proj: Path) -> None:
    assert _row("verify steps")[1:] == (True, "1 step: unit")
    host = "host: {org: a, project: p, repo: r}"
    (proj / "dev-loop.yaml").write_text(f"version: 1\n{host}\nverify: {{steps: {{}}}}\n")
    fix = "add a command under verify.steps in dev-loop.yaml"
    assert _row("verify steps")[1:] == (False, f"none with a command, dl verify is red: {fix}")
    assert _one(_json(proj), "verify steps")["fix"] == fix
    assert doctor.main([]) == 1
    (proj / "dev-loop.yaml").write_text(f"version: 1\n{host}\n")
    assert _row("verify steps")[1] is False


def test_tools_row_names_each_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the proj fixture's stand-in is not wanted here
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None if tool == "flock" else "/x")
    name, ok, detail = doctor._tools_row()
    assert (name, ok) == ("tools", False)
    assert "flock" in detail and "jq" not in detail


def test_duplicate_hooks_row_is_red_naming_the_copy(proj: Path) -> None:
    assert _row("duplicate hooks")[1] is True
    call = {"type": "command", "command": "sh .claude/hooks/done-guard.sh"}
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [call]}]}}))
    _, ok, detail = _row("duplicate hooks")
    assert not ok and "done guard (project-local)" in detail


def test_guides_without_packages_is_red(proj: Path) -> None:
    assert _row("guides")[1] is True
    (proj / "dev-loop.yaml").write_text(
        (proj / "dev-loop.yaml").read_text() + 'knowledge: {guides: ["*/CLAUDE.md"]}\n')
    (proj / "a").mkdir()
    (proj / "a" / "CLAUDE.md").write_text("# a\n")
    ok, detail = _row("guides")[1:]
    assert ok is False and "knowledge.packages is empty" in detail


def _roles(proj: Path, roles: str) -> None:
    (proj / "dev-loop.yaml").write_text(
        "version: 1\nhost: {org: a, project: p, repo: r}\n" + STEPS + "roles: %s\n" % roles)


def test_agent_models_green_by_default(proj: Path) -> None:
    assert _row("agent models")[1] is True


def test_agent_models_warns_on_a_plugin_agent_drift(proj: Path) -> None:
    _roles(proj, "{builder-backend: {model: claude-sonnet-5-5}}")
    ok, detail = _row("agent models")[1:]
    assert ok
    assert detail.startswith("warn: ")
    for text in ("builder-backend", "claude-sonnet-5-5", "claude-opus-5-5"):
        assert text in detail
    row = next(r for r in doctor.json_rows(proj) if r["name"] == "agent models")
    assert row["status"] == "optional"
    assert "bare type" in row["fix"]


def test_agent_models_green_when_the_override_agrees(proj: Path) -> None:
    _roles(proj, "{builder-backend: {model: claude-opus-5-5}}")
    assert _row("agent models")[1] is True


def test_agent_models_reads_the_projects_copy_of_a_bare_type(proj: Path) -> None:
    agents = proj / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "mine.md").write_text("---\nname: mine\nmodel: claude-opus-5-5\n---\n")
    _roles(proj, "{mine: {agent_types: [mine], model: claude-sonnet-5-5, source: s, "
               "context: {default: 200000, min: 100000, max: 400000}}}")
    ok, detail = _row("agent models")[1:]
    assert not ok
    assert "mine" in detail


def _project_agent(proj: Path, model: str, eol: str = "\n") -> None:
    agents = proj / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: mine\nmodel: {model}\n---\nbody\n".replace("\n", eol)
    (agents / "mine.md").write_bytes(text.encode())
    _roles(proj, "{mine: {agent_types: [mine], model: claude-sonnet-5-5, source: s, "
                 "context: {default: 200000, min: 100000, max: 400000}}}")


@pytest.mark.parametrize("model", ["sonnet", "inherit", "claude-sonnet-5-5"])
def test_agent_models_accepts_an_alias_or_inherit_in_a_project_agent(
    proj: Path, model: str,
) -> None:
    _project_agent(proj, model)
    assert _row("agent models")[1] is True


def test_agent_models_reads_a_crlf_project_agent(proj: Path) -> None:
    _project_agent(proj, "claude-opus-5-5", eol="\r\n")
    ok, detail = _row("agent models")[1:]
    assert not ok
    assert "claude-opus-5-5" in detail
