"""Self-check for the dev loop config's validator and its shipped defaults.

The loader, the merge and the CLI arrive with config.py (part 2) and its own self-check.

Run: python -m pytest plugins/dev-loop/tests -q -k "config or schema"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dev_loop.schema import validate
from test_dev_loop_fixtures import Tree

REPO = Path(__file__).resolve().parents[1]
ROLES = ["coordinator", "planner", "researcher", "builder-backend", "builder-frontend"]
ROLES += ["reviewer", "review-fixer", "simplifier", "general"]


def defaults() -> dict[str, Any]:
    text = (REPO / "src/dev_loop/defaults.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def test_the_defaults_are_complete_and_valid(tree: Tree) -> None:
    """Every section and the eight roles, each with a source; the defaults validate."""
    values = defaults()
    assert list(values["roles"]) == ROLES, list(values["roles"])
    assert all(role["source"] for role in values["roles"].values())
    assert values["roles"]["researcher"]["model"] == "claude-opus-5-5"
    assert values["review"]["fast_track"] is False  # V4a: fast track is the user's choice, off
    assert values["review"]["fast_track_fix"] == ["law", "security", "crash"]
    assert (values["review"]["runner"], values["review"]["model"]) == ("claude", None)  # B6
    assert values["review"]["runner_command"] is None
    validate(values)


BAD = [  # (dotted key, a refused value, the text the refusal starts with); each edge one past
    ("caps.enabled", 1, "caps.enabled"),
    ("handoff.threshold", 0.49, "handoff.threshold"),
    ("handoff.threshold", 1.01, "handoff.threshold"),
    ("handoff.threshold", True, "handoff.threshold"),
    ("warnings.running_hours", 0, "warnings.running_hours"),
    ("warnings.running_hours", 73, "warnings.running_hours"),
    ("size.cap", 49, "size.cap: a whole number in 50..1000"),
    ("size.cap", 1001, "size.cap"),
    ("size.cap", 200.0, "size.cap"),
    ("size.advise", 9, "size.advise"),
    ("size.advise", 201, "size.advise: a whole number in 10..200"),
    ("branches.change_prefix", "dev-loop", "branches.change_prefix"),
    ("roles.planner.context.min", 19_999, "roles.planner.context.min"),
    ("roles.planner.context.max", 1_000_001, "roles.planner.context.max"),
    ("roles.planner.context.default", 124_999, "roles.planner.context.default"),
    ("roles.planner.context.default", 500_001, "roles.planner.context.default"),
    ("roles.planner.context.default", True, "roles.planner.context.default"),
    ("roles.planner.context", None, "roles.planner.context: a mapping"),
    ("roles.planner.model", "Claude Opus", "roles.planner.model"),
    ("roles.planner.source", "", "roles.planner.source"),
    ("roles.reviewer.tools", ["Read", "Bash"], "roles.reviewer.tools"),
    ("roles.reviewer.max_turns", 0, "roles.reviewer.max_turns"),
    ("roles.reviewer.seconds", 3601, "roles.reviewer.seconds"),
    ("roles.reviewer.usd", 0, "roles.reviewer.usd"),
    ("roles.planner.max_turns", 5, "roles.planner.max_turns: unknown key"),
    ("routing.kinds.backend", "nobody", "routing.kinds.backend"),
    ("routing.paths", [{"glob": "**", "kind": "nobody"}], "routing.paths.0"),
    # unhashable values: refused with the key, never a TypeError from a set or dict lookup
    ("roles.reviewer.tools", [["Read"]], "roles.reviewer.tools"),
    ("routing.kinds.backend", ["builder-backend"], "routing.kinds.backend"),
    ("routing.paths", [{"glob": "**", "kind": ["backend"]}], "routing.paths.0"),
    ("roles.planner.agent_types", [["planner"]], "roles.planner.agent_types"),
    ("roles.general.agent_types", ["general"], "roles: each agent type in one role"),
    ("roles.planner.agent_types", ["dev-loop:builder"], "roles: each agent type in one role"),
    ("caps.typo", True, "caps.typo: unknown key"),
    ("typo", 1, "config.typo: unknown key"),
]
GOOD = [("handoff.threshold", 0.5), ("handoff.threshold", 1.0), ("size.cap", 1000)]
GOOD += [("size.advise", 10), ("warnings.running_hours", 1)]  # size.cap 50: with advise 50 below
GOOD += [("warnings.running_hours", 72), ("roles.planner.context.min", 20_000)]
GOOD += [("roles.planner.context.max", 1_000_000), ("roles.planner.model", None)]


def put(values: dict[str, Any], dotted: str, value: object) -> dict[str, Any]:
    *path, last = dotted.split(".")
    node = values
    for key in path:
        node = node[key]
    node[last] = value
    return values


def test_each_bad_value_is_refused_with_its_key(tree: Tree) -> None:
    """An unknown key, a wrong type, a value one past each edge: refused, key named."""
    for dotted, value, said in BAD:
        try:
            validate(put(defaults(), dotted, value))
        except ValueError as error:
            assert str(error).startswith(said), (dotted, value, str(error))
        else:
            raise AssertionError((dotted, value))
    for dotted, value in GOOD:  # at each edge: kept
        validate(put(defaults(), dotted, value))
    validate(put(put(defaults(), "size.advise", 50), "size.cap", 50))  # advise up to the cap
    said = ""
    try:  # one past the cap: refused, the advice named
        validate(put(put(defaults(), "size.advise", 51), "size.cap", 50))
    except ValueError as error:
        said = str(error)
    assert said.startswith("size.advise: a whole number in 10..50"), said


def test_a_kind_that_is_not_a_string_is_refused_with_its_key(tree: Tree) -> None:
    """An int kind beside a missing kind was a TypeError from sorted()."""
    values = defaults()
    values["routing"]["kinds"][1] = "builder-backend"
    values["routing"]["paths"] = [{"glob": "**", "kind": "missing"}]
    said = ""
    try:
        validate(values)
    except ValueError as error:
        said = str(error)
    assert said.startswith("routing.kinds"), said

SECTION_BAD = [  # one refused value per new section and kind; each names its dotted key
    ("paths.changes", "", "paths.changes"),
    ("paths.frozen", "a/**", "paths.frozen"),
    ("paths.frozen", [""], "paths.frozen"),
    ("branches.integration", None, "branches.integration"),
    ("branches.release", 5, "branches.release"),
    ("branches.protected", [1], "branches.protected"),
    ("host.provider", "gitlab", "host.provider"),
    ("host.org", "", "host.org"),
    ("host.token_env", None, "host.token_env"),
    ("knowledge.read_first", "a.md", "knowledge.read_first"),
    ("knowledge.session_checklist", 1, "knowledge.session_checklist"),
    ("size.production", {"a": 1}, "size.production"),
]
GOOD = [("host.org", "acme"), ("branches.release", "release"), ("paths.frozen", ["x"])]


def test_a_bad_value_in_the_new_sections_is_refused_with_its_key(tree: Tree) -> None:
    for dotted, value, said in SECTION_BAD:
        try:
            validate(put(defaults(), dotted, value))
        except ValueError as error:
            assert str(error).startswith(said), (dotted, str(error))
        else:
            raise AssertionError(dotted)
    for dotted, value in GOOD:
        validate(put(defaults(), dotted, value))


def test_an_unknown_key_in_a_new_section_is_refused_by_its_dotted_name(tree: Tree) -> None:
    values = defaults()
    values["host"]["colour"] = "red"
    try:
        validate(values)
    except ValueError as error:
        assert str(error).startswith("host.colour: unknown key"), str(error)
    else:
        raise AssertionError("accepted")


REST_BAD = [  # LP03b: one refusal per section, its dotted key named
    ("review.governance", "a", "review.governance"),
    ("review.extra", ["a"], "review.extra"),
    ("review.extra", {"a": 1}, "review.extra"),
    ("review.standards", "", "review.standards"),
    ("review.auto_trigger", "yes", "review.auto_trigger"),
    ("review.fast_track", "yes", "review.fast_track"),
    ("review.fast_track", 1, "review.fast_track"),
    ("review.fast_track_fix", "law", "review.fast_track_fix"),
    ("review.fast_track_fix", [1], "review.fast_track_fix"),
    ("verify.runner", None, "verify.runner"),
    ("verify.steps", {"lint": ""}, "verify.steps"),
    ("verify.affected", 3, "verify.affected"),
    ("verify.prepush", [1], "verify.prepush"),
    ("verify.host_steps", [1], "verify.host_steps"),
    ("verify.host_steps", ["nope"], "verify.host_steps: nope is not in verify.steps"),
    ("verify.covers", {"nope": "a/**"}, "verify.covers: nope is not in verify.steps"),
    ("verify.covers", ["a/**"], "verify.covers"),
    ("gates.sides", "x", "gates.sides"),
    ("gates.on_edit", 1, "gates.on_edit"),
    ("metrics.deploy_pipeline", "", "metrics.deploy_pipeline"),
    ("tasks.tag", None, "tasks.tag"),
    ("tasks.cache", "", "tasks.cache"),
    ("managers", "a", "managers"),
    ("managers", [""], "managers"),
    ("workers", ["a"], "workers"),
    ("workers", {"w": {"checkout": "/a", "worktrees": "/b"}}, "workers.w.never: missing"),
    ("workers", {"w": {"checkout": "/a", "worktrees": "/b", "never": "x"}}, "workers.w.never"),
    ("workers", {"w": {"checkout": "", "worktrees": "/b", "never": []}}, "workers.w.checkout"),
    ("review.colour", "red", "review.colour: unknown key"),
    ("review.runner", "", "review.runner"),
    ("review.model", "", "review.model"),
    ("review.runner_command", 1, "review.runner_command"),
    ("review.runner", "other", "review.runner_command: a command template"),  # B6: none given
    ("records.guard_moves", ["a"], "records.guard_moves"),  # G5: not a mapping
    ("records.guard_moves", {"old.py": 1}, "records.guard_moves"),  # a non-string value
    ("records.guard_moves", {"": "new.py"}, "records.guard_moves"),  # an empty key
    ("records.colour", "red", "records.colour: unknown key"),
]
REST_GOOD = [("review.model", "m-x"), ("review.runner_command", "x {prompt}"),
             ("review.extra", {"a": "b"}), ("verify.runner", ""), ("managers", ["me"])]
REST_GOOD += [("workers", {"w": {"checkout": "/a", "worktrees": "/b", "never": ["x/*"]}})]
REST_GOOD += [("records.guard_moves", {"old/a.py": "new/a.py"}),  # G5: a path move
              ("records.guard_moves", {"web/*.ts": "plugin:test_x.py"})]  # a glob to a plugin name


def test_a_bad_value_in_the_remaining_sections_is_refused_with_its_key(tree: Tree) -> None:
    for dotted, value, said in REST_BAD:
        try:
            validate(put(defaults(), dotted, value))
        except ValueError as error:
            assert str(error).startswith(said), (dotted, str(error))
        else:
            raise AssertionError(dotted)
    for dotted, value in REST_GOOD:
        validate(put(defaults(), dotted, value))


def test_the_defaults_name_plugin_agents_and_no_project_figure() -> None:
    values = defaults()
    types = [t for role in values["roles"].values() for t in role["agent_types"]]
    assert all(t.startswith(("dev-loop:", "code-simplifier:", "pr-review-toolkit:")) or t == "*"
               for t in types), types
    assert set(values["routing"]["kinds"].values()) == {"dev-loop:builder-backend",
                                                         "dev-loop:builder-frontend"}
    assert values["tasks"]["tag"] == "dev-loop-agent"
    assert not any(c.isdigit() for r in values["roles"].values() for c in r["source"]), "figures"


def test_a_ci_only_pattern_that_is_not_a_regex_is_refused_with_its_index(tree: Tree) -> None:
    """gates.ci_only: a list of regular expressions; a refusal names the key and the index."""
    assert defaults()["gates"]["ci_only"] == []
    validate(put(defaults(), "gates.ci_only", [r"\bpytest( -q)?$", "^make golden"]))
    for value, said in [(["ok", "("], "gates.ci_only.1: a regular expression"),
                        (["[a"], "gates.ci_only.0: a regular expression"),
                        ("pytest", "gates.ci_only: a list"), ([1], "gates.ci_only: a list")]:
        try:
            validate(put(defaults(), "gates.ci_only", value))
        except ValueError as error:
            assert str(error).startswith(said), (value, str(error))
        else:
            raise AssertionError(value)
