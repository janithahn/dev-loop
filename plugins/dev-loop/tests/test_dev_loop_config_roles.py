"""Self-check for the config's roles: by agent type, the agent files, the dashboard's rows.

Run: python tests/test_dev_loop_config_roles.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dev_loop.config import load
from dev_loop.dashboard.gate import role_for, roles
from test_dev_loop_config import ROLES, defaults, project
from test_dev_loop_fixtures import AGENT, Tree, assistant, run, user
from test_dev_loop_serving import serving

REPO = Path(__file__).resolve().parents[1]


def check_a_role_is_found_by_agent_type_alone(_: Tree) -> None:
    """The type is the role; a role carries its model and context range."""
    table = roles(defaults())
    assert [role.name for role in table] == ROLES
    backend = role_for(table, "dev-loop:builder-backend")
    assert (backend.model, backend.context) == ("claude-opus-5-5", (200_000, 100_000, 400_000))
    assert role_for(table, "dev-loop:planner").name == "planner"
    assert role_for(table, "dev-loop:code-reviewer").name == "reviewer"
    for kind in ("code-simplifier:code-simplifier", "pr-review-toolkit:code-simplifier"):
        assert role_for(table, kind).name == "simplifier", kind
    coordinator = next(role for role in table if role.name == "coordinator")
    assert coordinator.context is None and coordinator.agent_types == ()


def check_builder_is_builder_frontend(_: Tree) -> None:
    """`builder` is builder-frontend's alias, whatever model it runs on."""
    table = roles(defaults())
    assert role_for(table, "dev-loop:builder").name == "builder-frontend"
    assert role_for(table, "dev-loop:builder-frontend").name == "builder-frontend"


def check_an_untyped_agent_is_general(_: Tree) -> None:
    """An untyped or unnamed subagent is `general`; no subagent runs uncapped."""
    table = roles(defaults())
    for kind in (None, "general-purpose", "Explore", ""):
        general = role_for(table, kind)
        assert (general.name, general.context) == ("general", (200_000, 100_000, 400_000)), kind


def frontmatter_and_body(path: Path) -> tuple[str, str]:
    _, head, body = path.read_text(encoding="utf-8").split("---\n", 2)
    return head, body


def check_agent_files_match_their_role_models(_: Tree) -> None:
    """Each agent file a role names exists for the builders and the researcher, and
    carries its role's model (the full id)."""
    agents, seen = REPO / "agents", []
    for role in roles(load(REPO).values):
        for kind in (k.removeprefix("dev-loop:") for k in role.agent_types):
            path = agents / f"{kind}.md"
            if path.exists():
                head, body = frontmatter_and_body(path)
                model = re.search(r"^model: (\S+)$", head, re.MULTILINE)
                assert model and model[1] == role.model, (kind, role.model)
                assert "budgets.json" not in body and f"`{role.name}`" in body, kind
                seen.append(kind)
    assert {"builder-backend", "builder-frontend", "builder", "researcher"} <= set(seen), seen
    assert {"planner", "code-reviewer"} <= set(seen), seen


def check_builder_alias_body_is_the_frontend_body(_: Tree) -> None:
    """One body, two names (class 15)."""
    agents = REPO / "agents"
    alias, frontend = (
        frontmatter_and_body(agents / f"{n}.md") for n in ("builder", "builder-frontend")
    )
    assert alias[1] == frontend[1]
    assert "name: builder\n" in alias[0] and "name: builder-frontend\n" in frontend[0]


def check_the_state_rows_carry_the_config_roles(tree: Tree) -> None:
    """The dashboard reads its roles from the config per request, by agent type."""
    path = tree.write([user(), assistant("m1", "end_turn")], agent=AGENT)
    meta = {"agentType": "dev-loop:planner", "description": "plan"}
    path.with_name(f"agent-{AGENT}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with serving(tree) as (app, _):
        app.root = tree.root
        project(tree, "roles: {planner: {context: {default: 300000}}}\n")
        agent = next(r for r in app.state()["sessions"] if r["agent_id"])
        caps = {"context": 300_000, "stated": None, "default": 300_000, "clamped": False}
        assert (agent["role"], agent["caps"]) == ("planner", caps), agent
        project(tree, "- a list\n")  # a bad file: the defaults, and the page is told why
        state = app.state()
        agent = next(r for r in state["sessions"] if r["agent_id"])
        assert agent["caps"] == {**caps, "context": 250_000, "default": 250_000}, agent
        assert state["config_error"].startswith("dev-loop.yaml: ValueError"), state["config_error"]


if __name__ == "__main__":
    run("dev-loop config roles", [o for name, o in list(globals().items()) if name[:6] == "check_"])
