"""The dev loop config's one validator: a refusal names its key and range.

Used by config.py only; the Settings page (part d) posts and shows this module's reason.
"""

from __future__ import annotations

import re
from typing import Any

SECTIONS = {"version", "caps", "handoff", "warnings", "routing", "roles"}
SECTIONS |= {"paths", "branches", "host", "knowledge", "size"}
SECTIONS |= {"review", "verify", "gates", "metrics", "tasks", "managers", "workers", "build"}
SECTIONS |= {"fix_proves", "briefs", "release_gate", "licences", "cleanup", "records", "rules"}
# The sections of plain values: key -> kind (list of strings, text, text or null, prefix, ...).
TEXT = {
    "paths": {"changes": "text", "archive": "text", "frozen": "list"},
    "branches": {
        "integration": "text",
        "remote": "text",
        "release": "text?",
        "protected": "list",
        "change_prefix": "prefix",
    },
    "host": {
        "provider": "provider",
        "org": "text?",
        "project": "text?",
        "repo": "text?",
        "token_env": "text",
        "pr_footer": "text?",
    },
    "knowledge": {
        "read_first": "list",
        "pitfalls": "text",
        "decisions": "text",
        "guides": "list",
        "packages": "list",
        "session_checklist": "text?",
    },
    "size": {
        "cap": "number",
        "advise": "number",
        "production": "list",
        "exclude": "list",
        "generated": "list",
    },
    "review": {
        "governance": "list",
        "extra": "map",
        "standards": "text?",
        "examples": "text?",
        "auto_trigger": "bool",
        "fast_track": "bool",
        "fallback": "fallback",
        "fast_track_fix": "list",
        "runner": "text",
        "model": "text?",
        "runner_command": "text?",
    },
    "verify": {"runner": "text*", "steps": "map", "affected": "text?", "prepush": "list",
               "host_steps": "list", "covers": "map",
               "step_timeout_minutes": "positive"},
    "gates": {"sides": "map", "on_edit": "text?", "policy": "text?", "ci_only": "patterns"},
    "fix_proves": {"src": "list", "tests": "list", "command": "text?"},
    "metrics": {"deploy_pipeline": "text?"},
    "tasks": {"tag": "text", "cache": "text"},
    "build": {"parallel": "positive"},
    "briefs": {
        "sections": "list",
        "max_read_first": "positive",
        "enforce": "enforce",
        "agent_types": "list",
        "change_line": "bool",
    },
    "release_gate": {"block_on": "severities"},
    "licences": {"allow": "list"},
    "cleanup": {
        "auto": "bool",
        "keep_days": "positive",
        "max_log_mb": "positive",
        "threshold_pct": "number",
        "docker": "bool",
        "docker_label": "text?",
        "caches": "bool",
        "cache_globs": "list",
    },
    "records": {"guard_moves": "map"},
    "rules": {"generator": "text?", "check": "text?"},
}
SEVERITIES = ("blocker", "major", "minor", "unknown")  # a debt record's; none read as unknown
WORKER_KEYS = {"checkout": "text", "worktrees": "text", "never": "list", "slots": "positive"}
WORKER_KEYS |= {"poll_seconds": "positive", "timeout_hours": "positive", "max_turns": "positive"}
WORKER_KEYS |= {"docker_slots": "positive", "default_base": "text?", "default_role": "text"}
# A worker's machine limits, each optional; default_base null: branches.integration.
# default_role is the context-cap role of a run whose brief names no Role: line.
WORKER_DEFAULTS = {"slots": 3, "poll_seconds": 60, "timeout_hours": 3, "docker_slots": 1}
WORKER_DEFAULTS |= {"default_base": None, "default_role": "builder-backend", "max_turns": 150}
# Optional worker keys, in the version-1 snapshot too: may_run (routing kind names, never empty;
# absent: every kind), reach and accounts (display only; the dashboard may show them).
WORKER_OPTIONAL = {"may_run": "list", "reach": "text*", "accounts": "list"}
ROLE_KEYS = {"agent_types", "model", "context", "source"}
PROCESS = {"max_turns": (1, 100), "seconds": (1, 3600), "usd": (0.01, 100)}  # the reviewer's
READ_ONLY = {"Read", "Grep", "Glob"}
MODEL = re.compile(r"[a-z0-9][a-z0-9.-]*")
PREFIX = re.compile(r"[A-Za-z0-9._-]+/")
CONTEXT = (20_000, 1_000_000)
# The fixed numeric ranges; size.advise is also at most size.cap (validate), context.* min..max.
BOUNDS = {
    "handoff.threshold": (0.5, 1.0),
    "warnings.running_hours": (1, 72),
    "size.cap": (50, 1000),
    "size.advise": (10, 200),
    "cleanup.threshold_pct": (1, 100),
}
Value = Any  # a parsed YAML value
SUPPORTED = 1  # the config version this plugin reads; a bump needs config_migrate.MIGRATIONS
IMPLICIT = 1  # a dev-loop.yaml with no `version` was written at version 1


class VersionError(ValueError):
    """dev-loop.yaml is newer than this plugin reads: one line, never prefixed."""


def refuse(dotted: str, wanted: str, value: Value) -> None:
    msg = f"{dotted}: {wanted}, got {value!r}"
    raise ValueError(msg)


def keys(mapping: Value, allowed: set[str], where: str) -> None:
    """Refuse a value that is not a mapping, an unknown key and a missing one."""
    if not isinstance(mapping, dict):
        refuse(where, "a mapping", mapping)
    for key in sorted(set(mapping) ^ allowed, key=str):
        wanted = "unknown key" if key in mapping else "missing"
        refuse(f"{where}.{key}", wanted, mapping.get(key))


def number(value: Value, dotted: str, bounds: tuple[float, float], *, whole: bool = True) -> None:
    """Refuse a value outside low..high; a bool is never a number (class 3)."""
    low, high = bounds
    kinds = (int,) if whole else (int, float)
    if type(value) not in kinds or not low <= value <= high:
        refuse(dotted, f"{'a whole number' if whole else 'a number'} in {low}..{high}", value)


def version_of(project: dict[str, Value]) -> int:
    """Return a project file's version; VersionError when newer than SUPPORTED."""
    version = project.get("version", IMPLICIT)
    if type(version) is not int or version < 1:
        refuse("version", "a whole number of 1 or more", version)
    if version > SUPPORTED:
        msg = f"dev-loop.yaml is version {version}; this plugin reads up to {SUPPORTED}: "
        raise VersionError(msg + "update the plugin")
    return int(version)


def validate(config: Value) -> None:
    """Raise ValueError naming the dotted key and its allowed range."""
    keys(config, SECTIONS, "config")
    if config["version"] != SUPPORTED:
        refuse("version", str(SUPPORTED), config["version"])
    for section, names in (("caps", "enabled"), ("handoff", "threshold")):
        keys(config[section], {names}, section)
    keys(config["warnings"], {"running_hours"}, "warnings")
    if type(config["caps"]["enabled"]) is not bool:
        refuse("caps.enabled", "true or false", config["caps"]["enabled"])
    threshold = config["handoff"]["threshold"]
    number(threshold, "handoff.threshold", BOUNDS["handoff.threshold"], whole=False)
    hours = config["warnings"]["running_hours"]
    number(hours, "warnings.running_hours", BOUNDS["warnings.running_hours"])
    for section, kinds in TEXT.items():
        validate_section(config[section], section, kinds)
    if config["review"]["runner"] != "claude" and config["review"]["runner_command"] is None:
        refuse("review.runner_command", "a command template when review.runner is not claude", None)
    for key in ("host_steps", "covers"):
        for name in config["verify"][key]:
            if name not in config["verify"]["steps"]:
                refuse(f"verify.{key}", f"{name} is not in verify.steps", name)
    validate_list(config["managers"], "managers")
    if not isinstance(config["roles"], dict):
        refuse("roles", "a mapping of role: settings", config["roles"])
    types: list[str] = []
    for name, role in config["roles"].items():
        types += validate_role(str(name), role)
    if len(types) != len(set(types)) or types.count("*") != 1:
        refuse("roles", "each agent type in one role, and one role with '*'", sorted(types))
    validate_routing(config["routing"], set(types))
    validate_workers(config["workers"], set(config["routing"]["kinds"]))


def validate_section(mapping: Value, section: str, kinds: dict[str, str]) -> None:
    """Refuse a bad value of one of the text sections, naming its dotted key."""
    keys(mapping, set(kinds), section)
    for name, kind in kinds.items():
        dotted, value = f"{section}.{name}", mapping[name]
        if kind == "number":
            low, high = BOUNDS[dotted]
            number(value, dotted, (low, min(high, mapping["cap"]) if name == "advise" else high))
        elif kind in ("list", "patterns"):
            validate_list(value, dotted)
            for index, pattern in enumerate(value if kind == "patterns" else []):
                validate_pattern(pattern, f"{dotted}.{index}")
        elif kind == "positive":
            if type(value) is not int or value < 1:
                refuse(dotted, "a whole number of 1 or more", value)
        elif kind == "prefix":
            if not isinstance(value, str) or not PREFIX.fullmatch(value):
                refuse(dotted, f"matching {PREFIX.pattern}", value)
        elif kind == "provider":
            if value not in ("ado", "github", "local"):
                refuse(dotted, "ado, github or local", value)
        elif kind == "enforce":
            if value not in ("warn", "block"):
                refuse(dotted, "warn or block", value)
        elif kind == "fallback":
            if value not in ("never", "subagent"):
                refuse(dotted, "never or subagent", value)
        elif kind == "severities":
            if not isinstance(value, list) or not set(value) <= set(SEVERITIES):
                refuse(dotted, f"a list of {', '.join(SEVERITIES)}", value)
        elif kind == "bool":
            if type(value) is not bool:
                refuse(dotted, "true or false", value)
        elif kind == "map":
            validate_map(value, dotted)
        elif not (value is None and kind == "text?") and not (
            isinstance(value, str) and (value or kind == "text*")
        ):
            refuse(dotted, "null or a non-empty string" if kind == "text?" else "a text", value)


def validate_list(value: Value, dotted: str) -> None:
    if not isinstance(value, list) or not all(isinstance(i, str) and i for i in value):
        refuse(dotted, "a list of non-empty strings", value)


def validate_pattern(pattern: str, dotted: str) -> None:
    try:
        re.compile(pattern)
    except re.error:
        refuse(dotted, "a regular expression", pattern)


def validate_map(value: Value, dotted: str) -> None:
    """Refuse anything but a mapping of non-empty strings to non-empty strings."""
    ok = isinstance(value, dict) and all(
        isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items()
    )
    if not ok:
        refuse(dotted, "a mapping of non-empty strings", value)


def validate_workers(workers: Value, kinds: set[str]) -> None:
    """Refuse a worker without its checkout, worktrees and never globs, or a bad limit or key."""
    if not isinstance(workers, dict):
        refuse("workers", "a mapping of name: {checkout, worktrees, never}", workers)
    for name, worker in workers.items():
        if not isinstance(worker, dict):
            refuse(f"workers.{name}", "a mapping", worker)
        required = {k: v for k, v in worker.items() if k not in WORKER_OPTIONAL}
        validate_section(WORKER_DEFAULTS | required, f"workers.{name}", WORKER_KEYS)
        validate_worker_optional(worker, f"workers.{name}", kinds)


def validate_worker_optional(worker: dict[str, Value], where: str, kinds: set[str]) -> None:
    for key in ("may_run", "accounts"):
        if key in worker:
            validate_list(worker[key], f"{where}.{key}")
    if "reach" in worker and not isinstance(worker["reach"], str):
        refuse(f"{where}.reach", "text (may be empty)", worker["reach"])
    if worker.get("may_run") == []:
        refuse(f"{where}.may_run", "at least one routing kind (leave the key out for all)", [])
    for kind in worker.get("may_run", []):
        if kind not in kinds:
            refuse(f"{where}.may_run", f"routing kinds {sorted(kinds)}", kind)


def bounds(dotted: str) -> tuple[float, float] | None:
    """Return a numeric key's outer range for the Settings page; None: not a number."""
    last = dotted.rpartition(".")[2]
    if dotted.startswith("roles.") and last in PROCESS:
        return PROCESS[last]
    if dotted.startswith("roles.") and ".context." in dotted:
        return CONTEXT
    return BOUNDS.get(dotted)


def validate_role(name: str, role: Value) -> list[str]:
    """Refuse a bad role; return its agent types. Only the reviewer has process limits."""
    where = f"roles.{name}"
    keys(role, ROLE_KEYS | ({*PROCESS, "tools"} if name == "reviewer" else set()), where)
    kinds = role["agent_types"]
    if not isinstance(kinds, list) or not all(isinstance(k, str) and k for k in kinds):
        refuse(f"{where}.agent_types", "a list of agent type names", kinds)
    model = role["model"]
    if model is not None and not (isinstance(model, str) and MODEL.fullmatch(model)):
        refuse(f"{where}.model", f"null or matching {MODEL.pattern}", model)
    if not isinstance(role["source"], str) or not role["source"]:
        refuse(f"{where}.source", "a sentence saying where its numbers come from", role["source"])
    for key, bounds in PROCESS.items():
        if key in role:
            number(role[key], f"{where}.{key}", bounds, whole=key != "usd")
    tools = role.get("tools", [])
    listed = isinstance(tools, list) and all(isinstance(t, str) for t in tools)
    if not listed or not set(tools) <= READ_ONLY:  # a name is checked before it is hashed
        refuse(f"{where}.tools", "a list within Read, Grep, Glob", tools)
    if not (role["context"] is None and name == "coordinator"):
        validate_context(where, role["context"])
    return list(kinds)


def validate_context(where: str, context: Value) -> None:
    keys(context, {"default", "min", "max"}, f"{where}.context")
    number(context["min"], f"{where}.context.min", CONTEXT)
    number(context["max"], f"{where}.context.max", (context["min"], CONTEXT[1]))
    number(context["default"], f"{where}.context.default", (context["min"], context["max"]))


def validate_routing(routing: Value, types: set[str]) -> None:
    keys(routing, {"kinds", "paths"}, "routing")
    kinds = routing["kinds"]
    if not isinstance(kinds, dict) or not kinds:
        refuse("routing.kinds", "a mapping of kind: agent type", kinds)
    for kind, agent in kinds.items():
        if not isinstance(kind, str) or not kind:  # YAML reads `1:` as an int key
            refuse("routing.kinds", "a mapping of kind: agent type", kinds)
        if not isinstance(agent, str) or agent not in types or agent == "*":
            refuse(f"routing.kinds.{kind}", "an agent type a role names", agent)
    if not isinstance(routing["paths"], list):
        refuse("routing.paths", "a list of {glob, kind}", routing["paths"])
    for index, rule in enumerate(routing["paths"]):
        keys(rule, {"glob", "kind"}, f"routing.paths.{index}")
        kind = rule["kind"]  # a list or mapping is unhashable: refused here, never a TypeError
        if not isinstance(rule["glob"], str) or not isinstance(kind, str) or kind not in kinds:
            refuse(f"routing.paths.{index}", f"a glob and a kind in {sorted(kinds)}", rule)
