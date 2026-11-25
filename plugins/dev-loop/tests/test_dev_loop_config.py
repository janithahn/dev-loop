"""Self-check for the dev loop's config: defaults, merge, the CLI, routing.

The validator's refusals: test_dev_loop_schema.py.

Run: python -m pytest plugins/dev-loop/tests -q -k "config or schema"
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from dev_loop import config
from dev_loop.config import Loaded, load, route, validate
from test_dev_loop_fixtures import Tree

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src/dev_loop/config.py"
ROLES = ["coordinator", "planner", "researcher", "builder-backend", "builder-frontend"]
ROLES += ["reviewer", "review-fixer", "simplifier", "general"]
TASKS = """# Tasks
- [ ] T1 Kind: backend — test: `scripts/x.py`.
- [ ] T2 Kind: frontend — `page/a.js`.
- [x] T3 — the page: `scripts/dev_loop/dashboard/page/a.js` and
  `scripts/a.py`.
- [ ] T4 — no path at all.
- [ ] T5 — a stylesheet: `page/a.css`.
"""


def project(tree: Tree, text: str) -> Loaded:
    (tree.root / "dev-loop.yaml").write_text(text, encoding="utf-8")
    return load(tree.root)


def defaults() -> dict[str, Any]:
    return yaml.safe_load(config.DEFAULTS.read_text(encoding="utf-8"))


def cli(*argv: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ | ({"CLAUDE_PROJECT_DIR": str(root)} if root else {})
    command = [sys.executable, str(SCRIPT), *argv]
    return subprocess.run(command, capture_output=True, text=True, env=env, check=False)


def test_the_defaults_are_complete_and_valid(tree: Tree) -> None:
    """Every section and the eight roles, each with a source; no project file: defaults."""
    loaded = load(tree.root)
    assert loaded.error is None and loaded.values == defaults()
    assert list(loaded.values["roles"]) == ROLES, list(loaded.values["roles"])
    assert all(role["source"] for role in loaded.values["roles"].values())
    assert set(loaded.sources.values()) == {"default"}, loaded.sources
    assert loaded.values["roles"]["researcher"]["model"] == "claude-opus-5-5"
    validate(defaults())


def test_a_project_key_overrides_only_itself(tree: Tree) -> None:
    """A mapping merges key by key; the sibling keys keep their defaults."""
    loaded = project(tree, "roles: {planner: {context: {default: 300000}}}\nsize: {cap: 300}\n")
    planner = loaded.values["roles"]["planner"]
    assert planner["context"] == {"default": 300_000, "min": 125_000, "max": 500_000}, planner
    assert planner["model"] == "claude-opus-5-5" and loaded.values["size"]["advise"] == 100
    assert loaded.sources["roles.planner.context.default"] == "project"
    assert loaded.sources["roles.planner.context.min"] == "default"
    assert (loaded.sources["size.cap"], loaded.sources["size.advise"]) == ("project", "default")
    assert loaded.get("size.cap") == 300 and loaded.get("roles.planner.context.max") == 500_000


def test_a_project_list_replaces_the_default(tree: Tree) -> None:
    """An ordered rule list is replaced whole, never appended to."""
    loaded = project(tree, "routing: {paths: [{glob: '**', kind: frontend}]}\n")
    assert loaded.values["routing"]["paths"] == [{"glob": "**", "kind": "frontend"}]
    assert loaded.values["routing"]["kinds"] == defaults()["routing"]["kinds"]
    assert loaded.sources["routing.paths"] == "project"


def test_a_bad_project_file_falls_back_to_the_defaults_with_its_error(tree: Tree) -> None:
    """Not YAML, not a mapping, a refused value: the defaults, the caps on, the error said."""
    for text, said in (
        ("size: {cap: [", "dev-loop.yaml: bad YAML line"),
        ("- a list\n", "dev-loop.yaml: ValueError: the file holds no mapping"),
        ("caps: {enabled: maybe}\n", "dev-loop.yaml: ValueError: caps.enabled: true or false"),
    ):
        loaded = project(tree, text)
        assert (loaded.error or "").startswith(said), (text, loaded.error)
        assert loaded.values == defaults() and loaded.values["caps"]["enabled"] is True
        done = cli("get", "caps.enabled", root=tree.root)  # the CLI refuses: one line
        assert (done.returncode, done.stdout) == (1, ""), done
        assert len(done.stderr.strip().splitlines()) == 1, done.stderr


def test_a_missing_pyyaml_is_one_clear_line(tree: Tree) -> None:
    """The defaults are YAML too: no PyYAML is one line naming the fix, never a traceback
    and never a number, so a reader (pr-review.sh, size_cap.sh) stops loud."""
    code = "import runpy, sys; sys.modules['yaml'] = None; sys.argv = ['c', 'get', 'size']; "
    code += f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    said = "dev-loop config: PyYAML is missing: pip install pyyaml"
    assert (done.returncode, done.stderr.strip(), done.stdout) == (1, said, ""), done
    lines = (REPO / "src/dev_loop/requirements.txt").read_text("utf-8").splitlines()
    assert any(line.lower().startswith("pyyaml") for line in lines), lines


def tasks(tree: Tree) -> None:
    folder = tree.root / ".dev-loop/changes/x"
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(TASKS, encoding="utf-8")


def test_route_by_kind(tree: Tree) -> None:
    """A task's Kind: names its agent type; the CLI prints it."""
    tasks(tree)
    assert route(tree.root, "x", "T1") == "dev-loop:builder-backend"
    assert route(tree.root, "x", "T2") == "dev-loop:builder-frontend"
    done = cli("route", "x", "T1", root=tree.root)
    assert (done.returncode, done.stdout.strip()) == (0, "dev-loop:builder-backend"), done


def test_route_by_path_glob(tree: Tree) -> None:
    """No Kind: the first glob a named path matches, the project's list over the
    default one."""
    tasks(tree)
    assert route(tree.root, "x", "T3") == "dev-loop:builder-frontend"  # **/*.js, the first path's
    assert route(tree.root, "x", "T5") == "dev-loop:builder-frontend"  # **/*.css
    rules = "[{glob: '*page/*', kind: frontend}, {glob: '**', kind: backend}]"
    project(tree, f"routing: {{paths: {rules}}}")
    assert route(tree.root, "x", "T3") == "dev-loop:builder-frontend"  # `.../page/a.js`
    assert route(tree.root, "x", "T5") == "dev-loop:builder-frontend"  # page/a.css


def test_route_refuses_a_bad_config(tree: Tree) -> None:
    """A refused project file: route exits 1 with the one-line error, never on the defaults."""
    tasks(tree)
    project(tree, "routing: {paths: [{glob: '**', kind: backend}]}\ncaps: {enabled: maybe}\n")
    done = cli("route", "x", "T5", root=tree.root)
    assert (done.returncode, done.stdout) == (1, ""), done
    assert "caps.enabled" in done.stderr and len(done.stderr.strip().splitlines()) == 1
    (tree.root / "dev-loop.yaml").unlink()
    assert cli("route", "x", "T5", root=tree.root).stderr == ""  # a good file: nothing said


def test_no_route_exits_2(tree: Tree) -> None:
    """No kind and no path, or no such task: exit 2 naming the fix."""
    tasks(tree)
    for task, said in (("T4", "no route for T4: add Kind: backend|frontend"), ("T9", "no task")):
        done = cli("route", "x", task, root=tree.root)
        assert done.returncode == 2 and said in done.stderr and not done.stdout, (task, done)


def test_get_prints_json(tree: Tree) -> None:
    """`get` prints the value as JSON for pr-review.sh and size_cap.sh; an unknown key
    exits 1 naming it."""
    done = cli("get", "roles.reviewer", root=tree.root)
    reviewer = json.loads(done.stdout)
    limits = {k: reviewer[k] for k in ("max_turns", "seconds", "usd", "tools", "model")}
    assert limits == {
        "max_turns": 12,
        "seconds": 960,
        "usd": 6,
        "tools": ["Read", "Grep", "Glob"],
        "model": "claude-opus-5-5",
    }, limits
    others = [r for name, r in defaults()["roles"].items() if name != "reviewer"]
    assert not any({"max_turns", "seconds", "usd", "tools"} & set(role) for role in others)
    assert cli("get", "size.cap", root=tree.root).stdout.strip() == "200"
    done = cli("get", "size.nope", root=tree.root)
    assert done.returncode == 1 and "size.nope: unknown key" in done.stderr, done


def test_migrate_removes_the_http_hook_once(tree: Tree) -> None:
    """`migrate` drops the dashboard's PreToolUse HTTP hook from ~/.claude/settings.json
    (a temp HOME here) through merge_settings, a .bak kept; every other hook stays; a second run
    changes nothing and says so."""
    home = tree.root / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    mine = {"type": "command", "command": "sh guard.sh"}
    before: dict[str, Any] = {"model": "opus", "hooks": {"PreToolUse": [{"hooks": [mine]}]}}
    before["hooks"]["Stop"] = [{"hooks": [mine]}]
    kept = json.loads(json.dumps(before))  # what migrate must leave: the user's own hooks
    url = "http://127.0.0.1:8787/hook/pre-tool-use"  # as the old --install-hook installed it
    before["hooks"]["PreToolUse"].append({"matcher": "*", "hooks": [{"type": "http", "url": url}]})
    settings.write_text(json.dumps(before), encoding="utf-8")
    env = os.environ | {"HOME": str(home), "USERPROFILE": str(home)}  # USERPROFILE: Windows
    command = [sys.executable, str(SCRIPT), "migrate"]
    done = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert done.returncode == 0, done.stderr
    assert "removed the dashboard hook http://127.0.0.1:8787/hook/pre-tool-use" in done.stdout
    after = json.loads(settings.read_text(encoding="utf-8"))
    assert after == kept, after
    assert json.loads((home / ".claude" / "settings.json.bak").read_text("utf-8")) == before
    written = settings.read_bytes(), settings.stat().st_mtime_ns
    again = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert again.returncode == 0, again.stderr
    assert "nothing" in again.stdout, again.stdout
    assert (settings.read_bytes(), settings.stat().st_mtime_ns) == written


def test_the_five_sections_have_neutral_defaults(tree: Tree) -> None:
    """No project name, path or branch in a default; change_prefix is dev-loop/."""
    values = load(tree.root).values
    assert values["paths"] == {
        "changes": ".dev-loop/changes",
        "archive": ".dev-loop/changes/archive",
        "frozen": [],
    }
    assert values["branches"] == {
        "integration": "main",
        "remote": "origin",
        "release": None,
        "protected": [],
        "change_prefix": "dev-loop/",
    }
    assert values["host"]["provider"] == "ado" and values["host"]["org"] is None
    assert values["host"]["token_env"] == "DEV_LOOP_HOST_TOKEN"
    template = yaml.safe_load((REPO / "templates/dev-loop.yaml").read_text(encoding="utf-8"))
    assert template["host"]["token_env"] == "DEV_LOOP_HOST_TOKEN"
    assert values["knowledge"]["read_first"] == [] and values["size"]["production"] == []


def test_get_prints_a_text_bare_and_a_list_one_item_a_line(tree: Tree) -> None:
    """`dl config get` for shell callers: one value, or one list item per line."""
    project(tree, "paths: {frozen: [a/**, b.md]}\n")
    assert cli("get", "paths.frozen", root=tree.root).stdout.splitlines() == ["a/**", "b.md"]
    assert cli("get", "branches.integration", root=tree.root).stdout == "main\n"
    assert cli("get", "host.org", root=tree.root).stdout == "null\n"


def test_root_comes_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert config.project_root() == tmp_path


def test_root_comes_from_git_without_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "sub").mkdir()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path / "sub")
    assert config.project_root().resolve() == tmp_path.resolve()


def test_root_is_never_taken_from_the_file(tmp_path: Path) -> None:
    """A copy of config.py placed outside any repository finds the project by git alone."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    for name in ("config.py", "schema.py", "defaults.yaml"):
        shutil.copy(SCRIPT.parent / name, plugin / name)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    subprocess.run(["git", "init", "-q", str(project_dir)], check=True)
    (project_dir / "dev-loop.yaml").write_text("size: {cap: 300}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    command = [sys.executable, str(plugin / "config.py"), "get", "size.cap"]
    done = subprocess.run(
        command, capture_output=True, text=True, env=env, cwd=project_dir, check=False
    )
    assert (done.returncode, done.stdout.strip()) == (0, "300"), done


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "4", None])
def test_build_parallel_is_a_positive_whole_number(tree: Tree, bad: object) -> None:
    """The build skill reads build.parallel; anything but a positive int is refused by name."""
    assert load(tree.root).get("build.parallel") == 4
    with pytest.raises(ValueError, match=r"^build\.parallel: "):
        validate(defaults() | {"build": {"parallel": bad}})
