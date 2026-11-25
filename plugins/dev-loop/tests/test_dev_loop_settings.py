"""Self-check for the config API: view with sources, write and drift.

Every check writes dev-loop.yaml and agent files in a temporary root, never the repository's.
Run: python tests/test_dev_loop_settings.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import yaml
from dev_loop import config, config_write
from dev_loop.dashboard import store
from test_dev_loop_fixtures import Tree, run
from test_dev_loop_serving import DL, call, serving

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

AGENT = "---\nname: {name}\nmodel: claude-opus-5-5\ntools: Read\n---\n\nmodel: keep me\n"
FRONTEND = "---\nname: {name}\nmodel: claude-sonnet-5-5\n---\nbody\n"
CONFIG = ("GET", "/api/config")
SHIPPED = config.DEFAULTS
SAVE = ("POST", "/api/config")


def local_types(tree: Tree) -> None:
    """Point the defaults at a copy whose builders also name the project's own agent files.

    The plugin's types (`dev-loop:*`) have no file in a project; a model write and drift act on a
    project's own `.claude/agents/<type>.md`, so the checks give the builders such types.
    """
    defaults = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    for role, kinds in (("builder-backend", ["builder-backend"]),
                        ("builder-frontend", ["builder-frontend", "builder"])):
        defaults["roles"][role]["agent_types"] += kinds
    copy = tree.root / "defaults.yaml"
    copy.write_text(yaml.safe_dump(defaults), encoding="utf-8")
    config.DEFAULTS = config_write.DEFAULTS = copy


def project(tree: Tree, text: str | None = None) -> Path:
    """Return a root with the builders' agent files and, given text, its dev-loop.yaml."""
    local_types(tree)
    root = tree.root / "repo"
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "builder-backend.md").write_bytes(AGENT.format(name="builder-backend").encode())
    for name in ("builder-frontend", "builder"):
        (agents / f"{name}.md").write_bytes(FRONTEND.format(name=name).encode())
    if text is not None:
        (root / "dev-loop.yaml").write_text(text, encoding="utf-8")
    return root


def held(root: Path) -> Any:  # noqa: ANN401 - the parsed project file
    return yaml.safe_load((root / "dev-loop.yaml").read_text(encoding="utf-8"))


@contextlib.contextmanager
def writes_failing(on: Callable[[Path], None]) -> Iterator[None]:
    """Run `on(path)` before each durable write; it may edit a file or raise."""
    real = store.write_durable

    def patched(path: Path, text: str, guard: Callable[[], None] | None = None) -> None:
        on(path)
        real(path, text, guard)

    store.write_durable = patched
    try:
        yield
    finally:
        store.write_durable = real


def check_config_view_names_each_source(tree: Tree) -> None:
    """Values, defaults, a source and a range per dotted key, the error and the path."""
    with serving(tree) as (app, port):
        app.root = project(tree, "size: {cap: 300}\n")
        status, view = call(port, CONFIG)
    assert status == 200, view
    assert view["path"] == str(app.root / "dev-loop.yaml") and view["error"] is None
    assert (view["values"]["size.cap"], view["defaults"]["size.cap"]) == (300, 200)
    assert view["sources"]["size.cap"] == "project"
    assert view["sources"]["handoff.threshold"] == "default"
    assert view["ranges"]["handoff.threshold"] == [0.5, 1.0]
    assert view["ranges"]["roles.planner.context.default"] == [20_000, 1_000_000]
    assert "caps.enabled" not in view["ranges"] and view["drift"] == []


def check_drift_is_shown(tree: Tree) -> None:
    """An agent file whose model: differs from its role's config is named."""
    root = project(tree, "roles: {builder-frontend: {model: claude-opus-5-5}}\n")
    want = {"role": "builder-frontend", "file": "claude-sonnet-5-5", "config": "claude-opus-5-5"}
    found = config_write.drift(root, config.load(root))
    assert found == [want | {"agent_type": t} for t in ("builder-frontend", "builder")], found
    with serving(tree) as (app, port):
        app.root = root
        assert call(port, CONFIG)[1]["drift"] == found


def check_a_value_out_of_range_writes_nothing(tree: Tree) -> None:
    """A 400 naming the key and its range; no file, no .bak; no X-DL is a 403."""
    root = project(tree)
    with serving(tree) as (app, port):
        app.root = root
        status, body = call(port, SAVE, {"key": "handoff.threshold", "value": 7}, DL)
        assert status == 400 and "handoff.threshold" in body["error"], body
        assert "0.5..1.0" in body["error"], body
        assert call(port, SAVE, {"key": "nope.key", "value": 1}, DL)[0] == 400
        assert call(port, SAVE, {"key": "size.cap"}, DL)[0] == 400  # no value: never a reset
        assert call(port, SAVE, {"key": "size.cap", "value": 300})[0] == 403
    assert not (root / "dev-loop.yaml").exists() and not (root / "dev-loop.yaml.bak").exists()


def check_reset_removes_the_key(tree: Tree) -> None:
    """Value null returns the key to its default; the old text is the .bak."""
    old = "size: {cap: 300}\nhandoff: {threshold: 0.8}\n"
    with serving(tree) as (app, port):
        app.root = root = project(tree, old)
        status, view = call(port, SAVE, {"key": "size.cap", "value": None}, DL)
    assert status == 200, view
    assert view["sources"]["size.cap"] == "default" and view["values"]["size.cap"] == 200
    assert held(root) == {"handoff": {"threshold": 0.8}}
    assert (root / "dev-loop.yaml.bak").read_text(encoding="utf-8") == old


def check_the_written_file_holds_only_changed_keys(tree: Tree) -> None:
    """Only the keys that differ from the defaults; a default value adds nothing."""
    root = project(tree)
    assert config_write.write(root, {"size.cap": 300, "handoff.threshold": 0.9}) == ["size.cap"]
    assert held(root) == {"size": {"cap": 300}}
    written = config_write.write(root, {"roles.planner.context.default": 300_000})
    assert written == ["roles.planner.context.default", "size.cap"], written
    assert held(root) == {
        "size": {"cap": 300},
        "roles": {"planner": {"context": {"default": 300_000}}},
    }
    assert config_write.write(root, {"size.cap": 200}) == ["roles.planner.context.default"]


def check_a_concurrent_edit_is_refused(tree: Tree) -> None:
    """A file changed since it was read is a 409 and keeps the other writer's text."""
    root = project(tree, "size: {cap: 300}\n")
    theirs = "size: {cap: 400}\n"

    def edit(path: Path) -> None:
        if path.name.startswith("dev-loop.yaml.tmp") or path.name == "dev-loop.yaml":
            (root / "dev-loop.yaml").write_text(theirs, encoding="utf-8")

    with serving(tree) as (app, port), writes_failing(edit):
        app.root = root
        status, body = call(port, SAVE, {"key": "handoff.threshold", "value": 0.8}, DL)
    assert status == 409 and "changed" in body["error"], body
    assert (root / "dev-loop.yaml").read_text(encoding="utf-8") == theirs


def check_a_model_change_rewrites_only_the_frontmatter_line(tree: Tree) -> None:
    """Each agent file of the role gets its model: line, frontmatter only, byte for byte.

    A failed agent-file write is a 500 naming the file, and drift shows it.
    """
    root = project(tree)
    agents = root / ".claude" / "agents"
    config_write.write(root, {"roles.builder-backend.model": "claude-sonnet-5-5"})
    text = AGENT.format(name="builder-backend").replace("opus", "sonnet", 1)
    assert (agents / "builder-backend.md").read_bytes() == text.encode()
    assert held(root) == {"roles": {"builder-backend": {"model": "claude-sonnet-5-5"}}}

    def refuse(path: Path) -> None:
        if path.name.startswith("builder.md"):
            raise PermissionError(13, "denied")

    with serving(tree) as (app, port), writes_failing(refuse):
        app.root = root
        status, body = call(port, SAVE, {"key": "roles.builder-frontend.model", "value": "x-1"}, DL)
        assert status == 500 and "builder.md" in body["error"], body
        drift = call(port, CONFIG)[1]["drift"]
    assert [row["agent_type"] for row in drift] == ["builder-frontend"], drift


def check_a_role_without_a_file_is_read_only(tree: Tree) -> None:
    """A role with no agent file shows its model read-only and refuses a model write."""
    root = project(tree)
    with serving(tree) as (app, port):
        app.root = root
        view = call(port, CONFIG)[1]
        status, body = call(port, SAVE, {"key": "roles.coordinator.model", "value": "x-1"}, DL)
    assert {"roles.coordinator.model", "roles.general.model"} <= set(view["read_only"])
    assert "roles.builder-backend.model" not in view["read_only"]
    assert status == 400 and "roles.coordinator.model" in body["error"], body
    assert not (root / "dev-loop.yaml").exists()


if __name__ == "__main__":
    checks: list[Callable[[Tree], None]] = [
        obj for name, obj in list(globals().items()) if name.startswith("check_")
    ]
    run("dev-loop settings", checks)
