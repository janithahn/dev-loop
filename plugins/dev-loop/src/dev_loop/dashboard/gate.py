"""The budget gate's decision over the scan's totals and the owner's controls; no IO but git
and an agent's definition (read_only).

The PreToolUse hook asks it and fails open.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from itertools import takewhile
from typing import TYPE_CHECKING, Any

from .scan import CONTEXT_WINDOW

if TYPE_CHECKING:
    from pathlib import Path

    from .spend import Totals

STALE_SECONDS = 60  # spend older than this is not trusted: only pauses are enforced
STOP = "Stop: commit what passes its tests, record budget_exhausted with what is done and what is "
STOP += "left, and report to the coordinator. Do not retry the tool."
WRAP_CALLS = 10  # the calls a crossing grants: tests, git add and commit, dl record
HAND_OFF = (
    "dev-loop gate: agent {agent} ({role}) is at its quality cap: {reason}. Hand off within "
    "{calls} Bash calls: {steps}"
)
COMMIT = "commit your work in progress on the row's branch, "  # only on a change branch
STEPS = (
    "run dl record <change> handover role={role} agent={agent} context={context} "
    'commit={commit} "done=..." "next=...", then stop and report handover. A fresh agent '
    "continues from the record."
)
# A worker main session: the runner, not a parent agent, starts the fresh run; it reads the
# note file and prepends it, so the hand-off writes done/next there, never to a parent.
WORKER_STEPS = (
    "commit your work in progress on the branch, then write one line (done=...; next=...) to "
    "{file} and stop. The runner starts a fresh run that continues from the branch and your note."
)
# An agent that cannot run the record command (read_only) hands off in its final reply.
REPLY_STEPS = (
    "you cannot run the record command, so stop now and put the hand-off in your final reply: "
    "what was done, what is left and what to read next. A fresh agent continues from your reply."
)
READ_ONLY_TYPES = frozenset({"Explore", "Plan", "claude-code-guide"})  # Claude Code's built-ins


@dataclass(frozen=True)
class Controls:
    """The owner's settings, keyed by session or agent id: pauses and context caps."""

    paused: frozenset[str] = frozenset()  # a paused session pauses its agents too
    context_caps: dict[str, int] = field(default_factory=dict)  # tokens, over the role's default

    def __post_init__(self) -> None:
        caps = self.context_caps.values()  # NaN, inf, a bool: refused
        if not all(type(cap) in (int, float) and 0 < cap <= CONTEXT_WINDOW for cap in caps):
            msg = f"a context cap is a number above zero and at most {CONTEXT_WINDOW:,}"
            raise ValueError(msg)


@dataclass(frozen=True)
class Role:
    """One role of the config: the agent types it governs, its model, its cap.

    `context` is (default, min, max) in tokens; None: never capped (the coordinator).
    The context window is the only cap enforced; cost, turns and time are shown.
    """

    name: str
    agent_types: tuple[str, ...]
    model: str | None  # None: any model
    context: tuple[int, int, int] | None


def roles(config: dict[str, Any]) -> list[Role]:
    """Return a Role per `roles:` entry of a config schema.py has validated, in its order."""
    found = []
    for name, role in config["roles"].items():
        cap = role["context"]
        context = None if cap is None else (cap["default"], cap["min"], cap["max"])
        found.append(Role(name, tuple(role["agent_types"]), role["model"], context))
    return found


def role_for(table: list[Role], agent_type: str | None, stated: str | None = None) -> Role | None:
    """Return the role naming the agent type, else the stated one, else the `*` role.

    A bare type (a project's own copy, `planner`) is named by a namespaced entry of that name
    (`dev-loop:planner`). A stated role counts only for an agent no role names, and never
    an uncapped role: no subagent runs uncapped.
    """
    named = next((role for role in table if agent_type in role.agent_types), None)
    if named is None and agent_type and ":" not in agent_type:
        suffix = f":{agent_type}"
        named = next((r for r in table if any(t.endswith(suffix) for t in r.agent_types)), None)
    chosen = named or next((r for r in table if r.name == stated and r.context is not None), None)
    return chosen or next((role for role in table if "*" in role.agent_types), None)


def cap_of(role: Role, stated: int | None, owner_cap: int | None) -> int:
    """Return one agent's context cap.

    The owner's cap, else the stated budget clamped into the role's min..max, else its default.
    """
    if owner_cap is not None:
        return owner_cap
    if role.context is None:
        msg = f"the {role.name} role is never capped"
        raise ValueError(msg)
    default, low, high = role.context
    return default if stated is None else min(max(stated, low), high)


@dataclass(frozen=True)
class Caller:
    """One subagent tool call, as the server sees it."""

    session_id: str
    agent_id: str
    tool: str
    role: Role | None = None
    window: tuple[str, int] | None = None  # (kind, calls judged) since a cap was crossed
    cap: int | None = None  # cap_of's (stated, clamped or default); None: the role's default
    threshold: float = 1.0  # the share of the cap that trips: the config's handoff.threshold
    caps_enabled: bool = True  # caps.enabled: false skips the cap; pauses still apply
    on_change_branch: bool = True  # the cwd is on a branches.change_prefix branch
    handover_file: str | None = None  # a worker run's hand-over note path; None: a subagent
    read_only: bool = False  # it cannot run the record command: it hands off in its reply


@dataclass(frozen=True)
class Answer:
    """The hook's answer: a deny reason, or a note beside the tool result; never an allow."""

    deny: str | None = None
    context: str | None = None  # additionalContext
    kind: str | None = None  # "context": the window this call counts in; None: none


def self_check() -> bool:
    """Prove the gate is live (G6): a capped agent at its cap is denied the crossing call.

    Read by `dl doctor`: the decision engine the hook asks still denies a crossing, not just
    that the hook file is present. No IO, no git (the call is off a change branch).
    """
    from .spend import Totals  # noqa: PLC0415 - only dl doctor pays for it

    role = Role("builder", ("*",), None, (200_000, 100_000, 400_000))
    caller = Caller("s", "a", "Read", role, cap=200_000, threshold=0.9, on_change_branch=False)
    totals = Totals(0.0, 0.0, 0.0, {}, {"a": 200_000})
    return verdict(caller, Controls(), totals, 0.0).deny is not None


def pauses(ids: list[tuple[str, str]], controls: Controls) -> list[str]:
    return [f"this {who} is paused by the owner" for who, i in ids if i in controls.paused]


def verdict(caller: Caller, controls: Controls, totals: Totals, now: float) -> Answer:
    """Answer an agent's call: the context at its cap opens a window.

    The window is WRAP_CALLS calls long. A pause is a hard deny, and it never opens a window: the
    crossing instruction is given on the first unpaused call. A stale scan opens no window and
    closes none; compaction under the trip closes it.
    """
    role, agent = caller.role, caller.agent_id
    default = role.context[0] if role and role.context else None
    default = default if caller.cap is None else caller.cap
    cap = controls.context_caps.get(agent, default) if caller.caps_enabled else None
    context = totals.context.get(agent, 0)
    kind, count = caller.window or (None, 0)
    if now - totals.at <= STALE_SECONDS:
        kind = "context" if cap is not None and context >= cap * caller.threshold else None
    answer = Answer()
    if kind and cap is not None:
        answer = in_window(caller.tool, kind, count, hand_off(caller, context, cap))
    if paused := pauses([("session", caller.session_id), ("agent", agent)], controls):
        carried = answer.kind if caller.window else None  # only an open window carries on
        return Answer(deny=f"dev-loop gate: {'; '.join(paused)}. {STOP}", kind=carried)
    return answer


def hand_off(caller: Caller, context: int, cap: int) -> str:
    """Return the crossing call's instruction: the role, the context, the cap, the steps."""
    role = caller.role.name if caller.role else "agent"
    share = "" if caller.threshold >= 1 else f"{caller.threshold:.0%} of "
    fields = {"role": role, "agent": caller.agent_id, "context": context}
    reason = f"this agent's context {context // 1000}k is at {share}its {cap // 1000}k cap"
    if caller.handover_file:  # a worker main session: the runner re-runs, not a parent agent
        steps = WORKER_STEPS.format(file=caller.handover_file)
    elif caller.read_only:
        steps = REPLY_STEPS
    else:
        wrap = STEPS.format(**fields, commit="<sha>" if caller.on_change_branch else "none")
        steps = COMMIT + wrap if caller.on_change_branch else wrap
    return HAND_OFF.format(**fields, calls=WRAP_CALLS, reason=reason, steps=steps)


def in_window(tool: str, kind: str, count: int, text: str) -> Answer:
    """Return the answer to the call judged `count`-th since the crossing."""
    if count == 0:
        return Answer(deny=text, kind=kind)
    if count > WRAP_CALLS:
        stop = "dev-loop gate: the wrap-up calls are spent: stop now and report to the coordinator."
        return Answer(deny=stop, kind=kind)
    if tool == "Bash":
        note = f"dev-loop gate: {WRAP_CALLS - count} calls left; then stop and report handover."
        return Answer(context=note, kind=kind)
    only = "dev-loop gate: only Bash calls in the window: tests, git add/commit, record.py. "
    return Answer(deny=only + text, kind=kind)


def _named(front: list[str], key: str) -> list[str] | None:
    """Return the names `key:` lists in an agent's frontmatter, None when it has no such key.

    One comma line, a flow list or a block list, quoted or not. A value that cannot be read
    raises ValueError.
    """
    import yaml  # noqa: PLC0415 - the config's own loader, there wherever a call is judged

    at = next((i for i, line in enumerate(front) if line.startswith(f"{key}:")), None)
    if at is None:
        return None
    below = takewhile(lambda line: not line.strip() or line[0] in " \t-", front[at + 1 :])
    try:
        value = yaml.safe_load("\n".join([front[at], *below]))[key]
    except (yaml.YAMLError, TypeError, KeyError) as error:
        raise ValueError(key) from error
    if isinstance(value, str):  # `Read, Bash(git:*, npm:*)`: a scope's commas are not the list's
        value = re.sub(r"\([^)]*\)", "()", value).split(",")
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ValueError(key)
    return [name.strip() for name in value]


def read_only(agent_type: str | None, plugin: Path, project: Path) -> bool:
    """Return whether the agent cannot run the record command: it has no Bash.

    A read-only built-in, or a definition whose frontmatter grants no Bash (`Bash(git:*)` is
    Bash) or takes it away again (an unscoped `disallowedTools` name): the plugin's own
    agents/<name>.md for `dev-loop:<name>`, the project's .claude/agents/<name>.md for a bare
    type. Write and Edit are not asked for: a reviewer with Bash alone records. No definition
    found, no `tools:` line (every tool) or a line that cannot be read: it can. agent_type is a
    checked type (the hook's `kind`), never a path.
    """
    if agent_type in READ_ONLY_TYPES or not agent_type:
        return agent_type in READ_ONLY_TYPES
    namespace, _, name = agent_type.rpartition(":")
    folders = {"": project / ".claude" / "agents", "dev-loop": plugin / "agents"}
    if namespace not in folders:
        return False
    try:
        lines = (folders[namespace] / f"{name}.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    fenced = bool(lines) and lines[0].strip() == "---"
    front = list(takewhile(lambda line: line.strip() != "---", lines[1:])) if fenced else []
    try:
        granted, denied = _named(front, "tools"), _named(front, "disallowedTools") or []
    except ValueError:
        return False
    bash = granted is None or any(tool.split("(")[0].strip() == "Bash" for tool in granted)
    return not bash or "Bash" in denied


def on_branch(cwd: Path, prefix: str) -> bool:
    """Return whether the checkout at cwd is on a `<prefix>...` branch; any failure: no."""
    if not cwd.is_dir():
        return False
    argv = ["git", "-C", str(cwd), "rev-parse", "--abbrev-ref", "HEAD"]
    try:
        done = subprocess.run(  # noqa: S603 - fixed git arguments, the cwd as one argument
            argv, capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.startswith(prefix)
