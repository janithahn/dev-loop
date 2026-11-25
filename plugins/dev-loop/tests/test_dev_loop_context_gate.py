"""Self-check for the context gate: the reading, the cap, the deny and its steps.

`scan.last_call` reads the context of an agent's last model call from its transcript's tail;
`scan.stated_budget`, `gate.role_for` and `gate.cap_of` give one agent's role and context cap;
`hooks/context-gate.py`, run as Claude Code runs it, answers one PreToolUse event.
Run: python -m pytest test_dev_loop_context_gate.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from dev_loop.dashboard import scan
from dev_loop.dashboard.gate import cap_of, role_for, roles
from test_dev_loop_config import defaults
from test_dev_loop_fixtures import (
    AGENT,
    PROJECT,
    SECRET,
    SESSION,
    Tree,
    assistant,
    line,
    tool_result,
    user,
)

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "context-gate.py"

# A user record whose tool result carries a `usage` object (an Agent call's): not a model call.
AGENT_RESULT = user() | {"toolUseResult": {"usage": {"input_tokens": 999_999}}}


def test_the_context_is_the_last_calls(tree: Tree) -> None:
    """Input + cache write + cache read of the last assistant call; read in blocks."""
    first = assistant("m1", tokens=(1, 10, 0, 0, 50_000))
    last = assistant("m2", tokens=(2, 10, 3, 4, 90_000))
    records = [user(), first, tool_result(), last, AGENT_RESULT, tool_result()]
    path = tree.write(records, agent=AGENT)
    assert scan.last_call(path) == 2 + 3 + 4 + 90_000  # input, 5m and 1h writes, read
    block = scan.TAIL_BLOCK
    try:
        scan.TAIL_BLOCK = 64  # lines span many blocks: each is joined whole before it is read
        assert scan.last_call(path) == 2 + 3 + 4 + 90_000
    finally:
        scan.TAIL_BLOCK = block


def test_a_half_written_last_line_is_skipped(tree: Tree) -> None:
    """What follows the last newline is still being written; the line before it counts."""
    path = tree.write([user(), assistant("m1", tokens=(1, 10, 0, 0, 170_000))], agent=AGENT)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line(assistant("m2", tokens=(1, 10, 0, 0, 900_000)))[:-5])
    assert scan.last_call(path) == 1 + 170_000
    only = tree.root / "half.jsonl"
    only.write_text(line(assistant("m1", tokens=(1, 10, 0, 0, 900_000)))[:-5], encoding="utf-8")
    assert scan.last_call(only) is None


def test_no_usage_yet_is_allowed(tree: Tree) -> None:
    """No assistant call yet, or no file yet: None (the call is allowed)."""
    assert scan.last_call(tree.write([user(), AGENT_RESULT], agent=AGENT)) is None
    assert scan.last_call(tree.root / "agent-never-written.jsonl") is None
    (tree.root / "empty.jsonl").write_bytes(b"")
    assert scan.last_call(tree.root / "empty.jsonl") is None


def test_the_tail_read_is_bounded(tree: Tree) -> None:
    """At most 64 MiB from the end; a line cut by the bound is never parsed."""
    assert (scan.TAIL_BLOCK, scan.TAIL_LIMIT) == (1 << 20, 64 << 20)
    filler = [tool_result() for _ in range(40)]
    path = tree.write([user(), assistant("m1", tokens=(1, 10, 0, 0, 80_000)), *filler], agent=AGENT)
    after = sum(len(line(record)) + 1 for record in filler)
    assert scan.last_call(path) == 1 + 80_000
    assert scan.last_call(path, tail_bytes=after) is None  # the call is past the bound
    assert scan.last_call(path, tail_bytes=after + 40) is None  # cut mid-line: not parsed


def test_a_stated_budget_is_clamped_to_the_range(tree: Tree) -> None:
    """`dev-loop budget: context=<n>[k]` on line one, clamped into the role's min..max."""
    backend = role_for(roles(defaults()), "dev-loop:builder-backend")  # 200k, 100k..400k
    assert backend is not None
    stated = {
        "dev-loop budget: context=50000\nbuild it": 100_000,
        "dev-loop budget: context=300k": 300_000,
        "dev-loop budget: context=900000\r\nbuild it": 400_000,
        "dev-loop budget:": 200_000,
        "build it\ndev-loop budget: context=300000": 200_000,  # only the first line
        "dev-loop budget: context=300000 please": 200_000,  # the whole line, or nothing
        "dev-loop budget: context=3000000000": 200_000,  # past nine digits: not a budget
    }
    for prompt, cap in stated.items():
        assert cap_of(backend, scan.stated_budget(prompt)[1], None) == cap, prompt
    assert scan.stated_budget("dev-loop budget: role=planner context=250k") == ("planner", 250_000)
    assert scan.stated_budget("build it") == (None, None)


def test_the_owners_cap_beats_the_stated(tree: Tree) -> None:
    """The owner's per-agent cap wins, inside the range or not."""
    table = roles(defaults())
    backend = role_for(table, "dev-loop:builder-backend")
    assert backend is not None
    owner = {"under the stated": (300_000, 120_000), "past the range": (None, 450_000)}
    for why, (stated, cap) in owner.items():
        assert cap_of(backend, stated, cap) == cap, why
    coordinator = next(role for role in table if role.name == "coordinator")
    cap = owner["under the stated"][1]
    assert cap_of(coordinator, None, cap) == cap
    refused = ""
    try:
        cap_of(coordinator, 300_000, None)  # an uncapped role has no range to clamp into
    except ValueError as error:
        refused = str(error)
    assert "coordinator" in refused


def test_a_stated_role_applies_only_to_an_untyped_agent(tree: Tree) -> None:
    """A typed agent keeps its type's role; a stated role never uncaps."""
    table = roles(defaults())
    role, _context = scan.stated_budget("dev-loop budget: role=planner")
    for untyped in (None, "general-purpose"):
        assert getattr(role_for(table, untyped, role), "name", None) == "planner", untyped
    assert getattr(role_for(table, "dev-loop:builder-backend", role), "name", None) == "builder-backend"
    for named in ("coordinator", "no-such-role", None):  # the coordinator is never capped
        assert getattr(role_for(table, "general-purpose", named), "name", None) == "general"


def hook(
    tree: Tree, stdin: str, path: Path = HOOK, extra: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    """Start the hook as Claude Code does (`python -S`, as registered), HOME in the tree."""
    home = tree.root / "home"
    env = os.environ | {"HOME": str(home), "USERPROFILE": str(home)}  # USERPROFILE: Windows
    for ambient in ("DEV_LOOP_ROLE", "DEV_LOOP_HANDOVER"):  # the test sets the role, not the
        env.pop(ambient, None)  # worker that launched the suite; a role case passes it via `extra`
    env |= {"CLAUDE_PROJECT_DIR": str(tree.root / "repo")} | (extra or {})
    pipe = subprocess.PIPE
    started = subprocess.Popen(  # noqa: S603 - this interpreter and the hook's path
        [sys.executable, "-S", str(path)], stdin=pipe, stdout=pipe, stderr=pipe, text=True, env=env
    )
    assert started.stdin is not None
    started.stdin.write(stdin)
    started.stdin.close()
    return started


def finish(started: subprocess.Popen[str]) -> tuple[int, str, str]:
    """Return a started hook's exit code, stdout and stderr."""
    started.wait(timeout=30)  # communicate() would flush the closed stdin (Python 3.12.14)
    assert started.stdout is not None and started.stderr is not None
    return started.returncode, started.stdout.read(), started.stderr.read()


def gate(
    tree: Tree, event: dict[str, Any], extra: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Run the hook as Claude Code does; return its JSON answer, or None: silence, proceed."""
    code, stdout, stderr = finish(hook(tree, json.dumps(event), extra=extra))
    assert code == 0, stderr
    assert SECRET not in stdout + stderr  # no prompt or tool text comes out
    return json.loads(stdout) if stdout.strip() else None


def event(
    tree: Tree, agent: str | None = AGENT, tool: str = "Bash", cwd: str = ""
) -> dict[str, Any]:
    """Return a PreToolUse event; a subagent's names the session's transcript (T-b0)."""
    found: dict[str, Any] = {
        "session_id": SESSION,
        "transcript_path": str(tree.root / PROJECT / f"{SESSION}.jsonl"),
        "cwd": cwd or str(tree.root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": f"cat {SECRET}.pdf"},
    }
    return found | ({"agent_id": agent, "agent_type": "dev-loop:builder-backend"} if agent else {})


def at_context(tree: Tree, context: int, agent: str = AGENT, prompt: str | None = None) -> None:
    """Append a call of this context to the agent's transcript; its prompt opens a new one."""
    path = tree.root / PROJECT / SESSION / "subagents" / f"agent-{agent}.jsonl"
    start = [] if path.exists() else [user(prompt)]
    tree.write([*start, assistant(f"m{context}", tokens=(context, 10, 0, 0, 0))], agent=agent)


def owner(tree: Tree, **controls: Any) -> None:  # noqa: ANN401 - control.json's keys
    folder = tree.root / "home" / ".claude" / "dev-loop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "control.json").write_text(json.dumps({"paused": []} | controls), encoding="utf-8")


def denied(answer: dict[str, Any] | None) -> str:
    """Return a deny's reason; anything else fails."""
    assert answer is not None, "the call was let through"
    decision = answer["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse", answer
    assert decision["permissionDecision"] == "deny", answer
    return str(decision["permissionDecisionReason"])


def test_a_main_session_is_never_capped(tree: Tree) -> None:
    """A session (no agent_id, no DEV_LOOP_ROLE) at any context or default cap proceeds."""
    tree.write([user(), assistant("m1", tokens=(900_000, 10, 0, 0, 0))])
    for controls in ({}, {"session_context": 100_000, "agent_context": 100_000}):
        owner(tree, **controls)
        for tool in ("Bash", "Edit"):
            assert gate(tree, event(tree, None, tool)) is None, (controls, tool)


def test_a_main_session_is_capped_only_with_a_role(tree: Tree) -> None:
    """A main session is capped only when the runner sets DEV_LOOP_ROLE; then it is judged as
    that role and its hand-off writes the note to DEV_LOOP_HANDOVER, so the runner (not a parent
    agent) continues the run. Without the role it stays uncapped, exactly as before."""
    note = str(tree.root / "handover")
    tree.write([user(), assistant("m1", tokens=(185_000, 10, 0, 0, 0))])
    assert gate(tree, event(tree, None)) is None  # no role: uncapped even at 185k
    role = {"DEV_LOOP_ROLE": "builder-backend", "DEV_LOOP_HANDOVER": note}
    reason = denied(gate(tree, event(tree, None), role))
    for part in ("(builder-backend)", "185k is at 90% of its 200k cap", "within 10 Bash calls"):
        assert part in reason, (part, reason)
    assert f"write one line (done=...; next=...) to {note}" in reason, reason
    assert "scripts/record.py" not in reason, reason  # the runner continues, not a parent agent


def test_a_paused_session_is_denied(tree: Tree) -> None:
    """A paused session is denied, its agents too; a paused agent alone is its own."""
    at_context(tree, 1_000)
    owner(tree, paused=[SESSION])
    assert "this session is paused" in denied(gate(tree, event(tree, None)))
    assert "this session is paused" in denied(gate(tree, event(tree)))
    owner(tree, paused=[AGENT])
    assert gate(tree, event(tree, None)) is None
    assert "this agent is paused" in denied(gate(tree, event(tree)))


def test_the_crossing_call_is_denied_with_the_hand_off(tree: Tree) -> None:
    """At handoff.threshold (0.9) of the cap the call is denied with the steps.

    The cap is the role's default, or the stated budget clamped.
    """
    at_context(tree, 179_999)
    assert gate(tree, event(tree)) is None
    at_context(tree, 180_000)
    reason = denied(gate(tree, event(tree, tool="Read")))
    for part in ("(builder-backend)", "180k is at 90% of its 200k cap", "within 10 Bash calls"):
        assert part in reason, (part, reason)
    assert "handover role=builder-backend agent=a0123456789abcdef context=180000" in reason
    stated = "b0123456789abcdef"
    at_context(tree, 269_999, stated, "dev-loop budget: context=300k")
    assert gate(tree, event(tree, stated)) is None
    at_context(tree, 270_000, stated)
    assert "270k is at 90% of its 300k cap" in denied(gate(tree, event(tree, stated)))


def test_caps_off_skips_the_cap_not_the_pause(tree: Tree) -> None:
    """The project's `caps.enabled: false` lets any context through; a pause still denies."""
    (tree.root / "repo").mkdir()
    (tree.root / "repo" / "dev-loop.yaml").write_text("caps:\n  enabled: false\n", "utf-8")
    at_context(tree, 900_000)
    assert gate(tree, event(tree)) is None
    owner(tree, paused=[AGENT])
    assert "this agent is paused" in denied(gate(tree, event(tree)))


def test_the_hook_never_answers_allow(tree: Tree) -> None:
    """Every answer is a deny or silence; an allow would skip the user's own rules."""
    tree.write([user(), assistant("m1", tokens=(900_000, 10, 0, 0, 0))])
    answers = []
    for context, paused in ((1_000, []), (190_000, []), (190_000, [AGENT]), (1_000, [SESSION])):
        at_context(tree, context)
        owner(tree, paused=paused)
        for agent in (AGENT, None):
            answers += [gate(tree, event(tree, agent, tool)) for tool in ("Bash", "Edit")]
    assert None in answers
    for answer in answers:
        assert answer is None or denied(answer), answer
        assert "allow" not in json.dumps(answer), answer


def git(cwd: Path, *argv: str) -> None:
    command = ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=noreply@example.com"]
    subprocess.run([*command, *argv], capture_output=True, timeout=30, check=True)  # noqa: S603


def test_the_steps_follow_the_branch(tree: Tree) -> None:
    """On a `branches.change_prefix` branch the steps commit first; elsewhere they don't."""
    checkout = tree.root / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q", "-b", "dev-loop/a-row")
    git(checkout, "commit", "-q", "--allow-empty", "-m", "start")
    at_context(tree, 190_000)
    on = denied(gate(tree, event(tree, cwd=str(checkout))))
    assert "Bash calls: commit your work in progress on the row's branch, run dl record" in on, on
    assert "commit=<sha>" in on, on
    git(checkout, "switch", "-q", "-c", "main")
    for agent, cwd in (("c1", checkout), ("c2", tree.root / "no-such-folder")):
        at_context(tree, 190_000, agent)  # each its own crossing: a window counts down
        off = denied(gate(tree, event(tree, agent, cwd=str(cwd))))
        assert "commit your work" not in off, off
        assert "Bash calls: run dl record <change> handover role=" in off, off
        assert "commit=none" in off, off


def plugin_copy(tree: Tree) -> Path:
    """Copy the plugin's hooks and source outside any project, as an install places them."""
    copy = tree.root / "installed" / "dev-loop"
    for part in ("hooks", "src"):
        shutil.copytree(HOOK.parents[1] / part, copy / part)
    return copy


def test_a_copy_outside_the_project_judges_the_project(tree: Tree) -> None:
    """The project is CLAUDE_PROJECT_DIR, the code is the hook's own plugin: a copy still denies."""
    (tree.root / "repo").mkdir()
    (tree.root / "repo" / "dev-loop.yaml").write_text("caps: {enabled: false}\n", "utf-8")
    copy = plugin_copy(tree) / "hooks" / "context-gate.py"
    at_context(tree, 190_000)
    code, out, err = finish(hook(tree, json.dumps(event(tree)), copy))
    assert (code, out, err) == (0, "", ""), err  # the project's caps-off file was read
    (tree.root / "repo" / "dev-loop.yaml").unlink()
    at_context(tree, 190_000, "c0123456789abcdef")
    started = hook(tree, json.dumps(event(tree, "c0123456789abcdef")), copy)
    assert "190k is at 90% of its 200k cap" in denied(json.loads(finish(started)[1]))


def test_the_slow_path_takes_pyyaml_from_the_plugin_venv(tree: Tree) -> None:
    """With ${CLAUDE_PLUGIN_DATA}/venv present, its site-packages give PyYAML, not site.main()."""
    data = tree.root / "data"
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    packages = data / "venv" / "lib" / version / "site-packages"
    shutil.copytree(Path(yaml.__file__).parent, packages / "yaml")
    at_context(tree, 190_000)
    started = hook(
        tree,
        json.dumps(event(tree)),
        plugin_copy(tree) / "hooks" / "context-gate.py",
        {"CLAUDE_PLUGIN_DATA": str(data), "PYTHONNOUSERSITE": "1"},
    )
    assert "of its 200k cap" in denied(json.loads(finish(started)[1]))
    shutil.rmtree(packages / "yaml")  # an empty venv: no fallback to site.main(); open and loud
    at_context(tree, 190_000, "c0123456789abcdef")
    started = hook(
        tree,
        json.dumps(event(tree, "c0123456789abcdef")),
        tree.root / "installed" / "dev-loop" / "hooks" / "context-gate.py",
        {"CLAUDE_PLUGIN_DATA": str(data)},
    )
    code, out, err = finish(started)
    # 0.4.18: still no fallback to the system's packages, and the call still goes through, but
    # where the user sees it: exit 0 with a systemMessage, not exit 1 and a stderr line alone.
    assert code == 0 and "PyYAML is missing" in err, err
    said = json.loads(out)["systemMessage"]
    assert said.startswith("dev-loop gate is not enforcing: ") and "PyYAML is missing" in said


def agents_log(tree: Tree) -> Path:
    return tree.root / "home" / ".claude" / "dev-loop" / "agents.jsonl"


def test_a_subagent_call_is_recorded_and_a_main_session_is_not(tree: Tree) -> None:
    """A subagent's slow-path call writes one agents.jsonl line of ids, a type and a count;
    a main session writes none, and no prompt or document text ever appears in the file."""
    at_context(tree, 150_000)  # above the floor, below the 180k trip: silence, but recorded
    assert gate(tree, event(tree)) is None
    rows = [json.loads(one) for one in agents_log(tree).read_text("utf-8").splitlines()]
    assert len(rows) == 1, rows
    assert rows[0] == {
        "session_id": SESSION,
        "agent_id": AGENT,
        "agent_type": "dev-loop:builder-backend",
        "context_tokens": 150_000,
        "at": rows[0]["at"],  # a float, checked below
    }
    assert isinstance(rows[0]["at"], float), rows[0]
    assert SECRET not in agents_log(tree).read_text("utf-8")
    gate(tree, event(tree, None))  # a main session (no agent_id): no line
    assert len(agents_log(tree).read_text("utf-8").splitlines()) == 1
