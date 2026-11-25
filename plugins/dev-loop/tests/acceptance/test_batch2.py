"""Acceptance batch 2 (Init and config): each scriptable row of the test plan, run as a user.

Every row drives `dl` through its launcher in a throwaway HOME, config dir, plugin data dir and git
config, against a bare repo as the hub (F-HUB; harness.py). A row that fails on today's code is a strict
xfail naming its AT id, so the suite stays green and a fix flips it.

Run: python -m pytest plugins/dev-loop/tests/acceptance -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from harness import PLUGIN, Run, f_chg, f_hub, f_local, files

URL_ADO = "https://org1" + "@dev.azure.com/org1/My%20Proj/_git/repo1"
URL_ADO_SSH = "git" + "@ssh.dev.azure.com:v3/org1/My%20Proj/repo1"
URL_GITHUB = "https://github.com/own1/repo1.git"
OLD_HOOK_URL = "http://127.0.0.1:8787/hook/pre-tool-use"
BROKEN = "branches:\n  integration: trunk\n  release: main\nsize:\n  cap: 5\n"


@pytest.fixture
def user(tmp_path: Path) -> Run:
    return Run(tmp_path)


def test_c1_init_lists_each_file_then_says_nothing_to_change(user: Run) -> None:
    proj = f_hub(user)
    first = user.dl(proj, "init", "--host", "local")
    assert first.returncode == 0, first.stderr
    wrote = {line.removeprefix("wrote ") for line in first.stdout.splitlines()}
    expected = {"dev-loop.yaml", ".claude/settings.json", ".githooks/pre-commit",
                ".githooks/pre-push", "core.hooksPath", ".dev-loop/RULES.md", "AGENTS.md",
                "CLAUDE.md"}
    assert expected <= wrote, first.stdout
    assert not (proj / "azure").exists() and not (proj / ".github").exists()
    status = user.git(proj, "status", "--porcelain", "--untracked-files=all")
    assert {line[3:] for line in status.splitlines()} == wrote - {"core.hooksPath"}
    before = files(proj)
    second = user.dl(proj, "init", "--host", "local")
    assert (second.returncode, second.stdout.strip()) == (0, "nothing to change"), second
    assert files(proj) == before


def test_c2_existing_files_gain_only_their_line_or_two_keys(user: Run) -> None:
    proj = f_hub(user)
    claude, agents = b"# Ours\r\nkeep \xc3\xa9\n", b"# Law\n\n1. bytes stay"
    settings = {"model": "x", "permissions": {"allow": ["Read"]}, "enabledPlugins": {"a@b": True}}
    (proj / "CLAUDE.md").write_bytes(claude)
    (proj / "AGENTS.md").write_bytes(agents)
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    assert user.dl(proj, "init", "--host", "local").returncode == 0
    assert (proj / "CLAUDE.md").read_bytes() == claude + b"\n@.dev-loop/RULES.md\n"
    pointer = b"Follow the working rules in [.dev-loop/RULES.md](.dev-loop/RULES.md)."
    assert (proj / "AGENTS.md").read_bytes() == agents + b"\n\n" + pointer + b"\n"
    after = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert after.pop("enabledPlugins") == {"a@b": True, "dev-loop@dev-loop": True}
    assert "dev-loop" in after.pop("extraKnownMarketplaces")
    assert after == {k: v for k, v in settings.items() if k != "enabledPlugins"}


def _refused_in_one_line(done: subprocess.CompletedProcess[str]) -> bool:
    return done.returncode != 0 and len((done.stdout + done.stderr).strip().splitlines()) == 1


def test_c3_init_keeps_a_projects_hooks_path(user: Run) -> None:
    proj = f_hub(user)
    user.git(proj, "config", "core.hooksPath", ".husky")
    done = user.dl(proj, "init", "--host", "local")
    assert user.git(proj, "config", "core.hooksPath") == ".husky" or _refused_in_one_line(done)
    if done.returncode == 0:
        assert user.git(proj, "config", "core.hooksPath") == ".husky", done.stdout


def test_c3_init_keeps_an_existing_pre_commit(user: Run) -> None:
    proj = f_hub(user)
    hook = proj / ".githooks" / "pre-commit"
    hook.parent.mkdir()
    hook.write_text("#!/bin/sh\necho ours\n", encoding="utf-8")
    done = user.dl(proj, "init", "--host", "local")
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho ours\n" or \
        _refused_in_one_line(done)
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho ours\n", done.stdout


def test_c5_init_keeps_a_projects_marketplace_source(user: Run) -> None:
    proj = f_hub(user)
    source = {"source": "git", "url": "https://example.invalid/fork/dev-loop.git"}
    market = {"extraKnownMarketplaces": {"dev-loop": {"source": source}}}
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text(json.dumps(market), encoding="utf-8")
    done = user.dl(proj, "init", "--host", "local")
    after = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
    kept = after["extraKnownMarketplaces"]["dev-loop"]["source"] == source
    assert kept or "marketplace" in (done.stdout + done.stderr).lower(), after


@pytest.mark.parametrize(("url", "host"), [
    (URL_ADO, {"provider": "ado", "org": "org1", "project": "My Proj", "repo": "repo1"}),
    (URL_ADO_SSH, {"provider": "ado", "org": "org1", "project": "My Proj", "repo": "repo1"}),
    (URL_GITHUB, {"provider": "github", "org": "own1", "project": None, "repo": "repo1"}),
])
def test_c6_host_detected_and_the_file_still_loads(
    user: Run, url: str, host: dict[str, str | None]
) -> None:
    proj = f_hub(user)
    user.git(proj, "remote", "set-url", "origin", url)
    assert user.dl(proj, "init").returncode == 0
    done = user.dl(proj, "config", "get", "host")
    assert done.returncode == 0 and done.stderr == "", done  # no "defaults are used" line
    got = json.loads(done.stdout)
    assert {key: got[key] for key in host} == host


def test_c7_init_discover_lists_guides_not_templates(user: Run) -> None:
    proj = f_local(user)
    for name in ("docs/x/CLAUDE.md", "templates/CLAUDE.md"):
        (proj / name).parent.mkdir(parents=True, exist_ok=True)
        (proj / name).write_text("x\n", encoding="utf-8")
    assert user.dl(proj, "init", "discover").returncode == 0
    done = user.dl(proj, "config", "get", "knowledge.guides")
    assert (done.returncode, done.stdout.split()) == (0, ["docs/x/CLAUDE.md"]), done


def test_c8_local_init_on_a_remote_named_hub(user: Run) -> None:
    proj = f_hub(user, remote="hub")
    assert user.dl(proj, "init", "--host", "local").returncode == 0
    remote = user.dl(proj, "config", "get", "branches.remote").stdout.strip()
    rows = json.loads(user.dl(proj, "doctor", "--json").stdout)
    host = next(row for row in rows if row["name"] == "host reachable")
    assert remote == "hub" or "branches.remote" in host["fix"], (remote, host)
    assert host["status"] == "ok" or "host.org" not in host["fix"], host


@pytest.mark.parametrize(("text", "key"), [
    ("nope: 1\n", "nope"),
    ("size:\n  cap: 5\n", "size.cap"),
    ("host:\n  provider: gitlab\n", "host.provider"),
    ("briefs:\n  enforce: maybe\n", "briefs.enforce"),
    ("roles:\n  planner:\n    agent_types: ['*']\n  general:\n    agent_types: ['*']\n",
     "roles"),
    ("- a list\n", "mapping"),
    pytest.param("size: {cap: [\n", "dev-loop.yaml"),
])
def test_c9_each_refusal_is_one_stderr_line_naming_the_key(user: Run, text: str, key: str) -> None:
    proj = f_hub(user)
    (proj / "dev-loop.yaml").write_text(text, encoding="utf-8")
    done = user.dl(proj, "config", "get", "size.cap")
    lines = done.stderr.strip().splitlines()
    assert len(lines) == 1 and key in lines[0], done.stderr
    assert "Traceback" not in done.stderr


def _broken(user: Run) -> tuple[Path, Path]:
    proj, work = f_chg(user)
    for root in (proj, work):
        (root / "dev-loop.yaml").write_text(BROKEN, encoding="utf-8")
    user.git(work, "commit", "-q", "-am", "a bad config")  # committed, so dl pr can rebase
    return proj, work


def _said_config_error(done: subprocess.CompletedProcess[str]) -> bool:
    return done.returncode != 0 and "size.cap" in done.stderr


def test_c10_pr_refuses_on_a_bad_config(user: Run) -> None:
    _proj, work = _broken(user)
    done = user.dl(work, "pr", "demo")
    heads = user.git(user.base / "hub.git", "for-each-ref", "--format=%(refname:short)")
    assert _said_config_error(done) and heads == "main", (done.stdout, done.stderr, heads)


def test_c10_propose_refuses_on_a_bad_config(user: Run) -> None:
    proj, _work = _broken(user)
    done = user.dl(proj, "propose", "other")
    made = user.git(proj, "for-each-ref", "--format=%(refname:short)", "refs/heads/*/other")
    assert _said_config_error(done) and made == "", (done.stdout, done.stderr, made)


def test_c10_verify_refuses_on_a_bad_config(user: Run) -> None:
    _proj, work = _broken(user)
    folder = work / ".dev-loop" / "changes" / "demo"
    before = files(folder)
    done = user.dl(work, "verify", "demo")
    assert _said_config_error(done) and files(folder) == before, (done.stdout, done.stderr)


def test_c10_size_cap_refuses_on_a_bad_config(user: Run) -> None:
    _proj, work = _broken(user)
    done = user.dl(work, "ci", "size-cap")
    assert _said_config_error(done), (done.stdout, done.stderr)


def test_c10_push_guard_refuses_on_a_bad_config(user: Run) -> None:
    proj, _work = _broken(user)
    event = {"tool_name": "Bash", "cwd": str(proj),
             "tool_input": {"command": "git push origin trunk"}}
    guard = str(PLUGIN / "hooks" / "push-guard.py")
    done = user.sh(proj, sys.executable, "-S", guard, stdin=json.dumps(event))
    assert '"deny"' in done.stdout, (done.stdout, done.stderr)


def test_c11_config_set_validates_and_keeps_a_backup(user: Run) -> None:
    proj = f_hub(user)
    path = proj / "dev-loop.yaml"
    path.write_text("version: 1\n", encoding="utf-8")
    first = user.dl(proj, "config", "set", "size.cap", "300")
    assert first.returncode == 0, first.stderr
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["size"]["cap"] == 300
    assert (proj / "dev-loop.yaml.bak").read_text(encoding="utf-8") == "version: 1\n"
    written = path.read_bytes()
    for key, value in (("size.cap", "5"), ("nope.key", "1")):
        done = user.dl(proj, "config", "set", key, value)
        assert done.returncode == 1 and len(done.stderr.strip().splitlines()) == 1, done
        assert key in done.stderr and path.read_bytes() == written


def _old_hook_settings(path: Path) -> str:
    hook = {"matcher": "*", "hooks": [{"type": "http", "url": OLD_HOOK_URL}]}
    text = json.dumps({"model": "x", "hooks": {"PreToolUse": [hook]}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def test_c12_migrate_drops_the_old_hook_once(user: Run) -> None:
    proj = f_hub(user)
    settings = Path(user.env["CLAUDE_CONFIG_DIR"]) / "settings.json"
    old = _old_hook_settings(settings)
    first = user.dl(proj, "config", "migrate")
    assert first.returncode == 0 and "removed the dashboard hook" in first.stdout, first
    assert json.loads(settings.read_text(encoding="utf-8")) == {"model": "x", "hooks": {
        "PreToolUse": []}}
    assert settings.with_name("settings.json.bak").read_text(encoding="utf-8") == old
    written = settings.read_bytes()
    second = user.dl(proj, "config", "migrate")
    assert second.returncode == 0 and "nothing changed" in second.stdout, second
    assert settings.read_bytes() == written


def test_c12_migrate_honours_a_non_default_config_dir(user: Run) -> None:
    proj = f_hub(user)
    custom = user.base / "config-dir" / "settings.json"
    _old_hook_settings(custom)
    home = Path(user.env["HOME"]) / ".claude" / "settings.json"
    untouched = _old_hook_settings(home)
    user.env["CLAUDE_CONFIG_DIR"] = str(custom.parent)
    done = user.dl(proj, "config", "migrate")
    assert done.returncode == 0, done.stderr
    assert home.read_text(encoding="utf-8") == untouched
    assert not home.with_name("settings.json.bak").exists()
    assert OLD_HOOK_URL not in custom.read_text(encoding="utf-8")


def test_c14_route_by_kind_by_tsx_path_and_none(user: Run) -> None:
    proj = f_hub(user)
    folder = proj / ".dev-loop" / "changes" / "x"
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(
        "# Tasks\n- [ ] T1 Kind: backend — `src/a.py`.\n- [ ] T2 — the page `web/App.tsx`.\n"
        "- [ ] T3 — no path and no kind.\n", encoding="utf-8")
    for task, agent in (("T1", "dev-loop:builder-backend"), ("T2", "dev-loop:builder-frontend")):
        done = user.dl(proj, "config", "route", "x", task)
        assert (done.returncode, done.stdout.strip()) == (0, agent), done
    third = user.dl(proj, "config", "route", "x", "T3")
    assert third.returncode == 2 and "Kind:" in third.stderr and not third.stdout, third
