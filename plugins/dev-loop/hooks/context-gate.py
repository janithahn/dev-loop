"""dev-loop context gate: the local PreToolUse command hook.

Per call, nothing in memory: the event on stdin, the agent's transcript tail, the merged config and
~/.claude/dev-loop/control.json go to gate.verdict. The answer is one JSON object on stdout, exit 0:
a deny, a window's countdown (`additionalContext`) or a bad project file's `systemMessage`; silence
lets the call proceed. It never answers "allow", so the user's own rules still apply. A main
session is never capped, only paused, and reads nothing but control.json (no YAML). Neither does a
subagent's call under the floor: see below_floor. Run as `python -S`, those import json,
os, sys and the tail reader alone (os.path: pathlib is an import); the slow path loads PyYAML.
The project is CLAUDE_PROJECT_DIR (else the working directory), read per call; the code is this
hook's own plugin, wherever installed: its `src` first, then PyYAML by dev_loop.hookenv.add_venv.
A crash fails open and loud: exit 1, one stderr line, one line in gate-errors.jsonl.
A gate that cannot judge at all (an import fails, the config loader exits for want of PyYAML)
says so in a `systemMessage` and exits 0: the same open door, where the user sees it.
No prompt, tool or document text leaves the hook: only ids, counts and error classes.

T-b0 (Claude Code 2.1.311, 2026-10-29): a subagent's event carries `agent_id` and `agent_type`;
its `transcript_path` is the session's file, so the agent's own is
`<transcript_path without .jsonl>/subagents/agent-<agent_id>.jsonl`.
"""

# ruff: noqa: PTH103, PTH105, PTH107, PTH110, PTH111
# ruff: noqa: PTH118, PTH119, PTH120, PTH122, PTH123, PTH202
from __future__ import annotations

import json
import os
import sys
import time  # loaded before any script runs: free

TYPE_CHECKING = False  # typing is not imported at run time either
if TYPE_CHECKING:
    from typing import Any

PLUGIN = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(PLUGIN, "src"))  # a path only: nothing is imported here
HOME = os.path.join(os.path.expanduser("~"), ".claude", "dev-loop")
CONTROL, WINDOWS = os.path.join(HOME, "control.json"), os.path.join(HOME, "windows")
HEARTBEAT = os.path.join(HOME, "gate-heartbeat")  # the gate's "I ran" mark: an mtime, no content
ERRORS = os.path.join(HOME, "gate-errors.jsonl")  # each checkout's floor: dashboard.floor
AGENTS = os.path.join(HOME, "agents.jsonl")  # subagent telemetry, read by the fleet exporter
LOG_MAX = 1 << 20  # past it the log moves to gate-errors.1.jsonl
AGENTS_MAX = 5 << 20  # past it agents.jsonl moves to agents.1.jsonl
ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
TYPE_CHARS = ID_CHARS | frozenset(":")  # a subagent type may be namespaced, e.g. `dev-loop:builder`
PAUSED = "dev-loop gate: this session is paused by the owner. Stop and report to the coordinator."
NO_YAML = "dev-loop config: PyYAML is missing: pip install pyyaml"  # config.py's own exit line


def plain(value: object) -> str | None:
    """Return value when it is a plain id (1 to 64 of A-Za-z0-9_-), else None: never a path."""
    if isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= ID_CHARS:  # noqa: PLR2004
        return value
    return None


def kind(value: object) -> str | None:
    """Return a subagent type: a plain id, plus `:` for a namespaced one; else None (never text)."""
    if isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= TYPE_CHARS:  # noqa: PLR2004
        return value
    return None


def log_error(event: object, error: BaseException) -> None:
    """Append one line: the time, the ids, the error's class and the hook's innermost own frame."""
    import traceback  # noqa: PLC0415 - only a crash pays for these
    from pathlib import Path  # noqa: PLC0415

    fields = event if isinstance(event, dict) else {}
    frames = traceback.extract_tb(error.__traceback__)
    ours = [frame for frame in frames if Path(frame.filename).resolve().is_relative_to(PLUGIN)]
    row = {
        "at": time.time(),
        "session_id": plain(fields.get("session_id")),
        "agent_id": plain(fields.get("agent_id")),
        "error": type(error).__name__,
        "frame": f"{Path(ours[-1].filename).stem}:{ours[-1].name}" if ours else None,
    }
    os.makedirs(HOME, exist_ok=True)
    if os.path.exists(ERRORS) and os.path.getsize(ERRORS) > LOG_MAX:
        os.replace(ERRORS, os.path.join(HOME, "gate-errors.1.jsonl"))
    with open(ERRORS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def beat() -> None:
    """Touch the heartbeat after a judged call: an mtime, no content. Never before: it is proof."""
    try:
        os.utime(HEARTBEAT, None)
    except FileNotFoundError:
        os.makedirs(HOME, exist_ok=True)
        with open(HEARTBEAT, "wb"):  # one empty mark; a later call only moves its mtime
            pass


def controls(event: dict[str, Any]) -> dict[str, Any]:
    """Return control.json's pauses and context caps; an unreadable file: no pauses, logged.

    No file: no controls. Any other key (an old file's dollar or default cap) is ignored.
    """
    try:
        data = json.loads(read(CONTROL)) if os.path.exists(CONTROL) else {}
        if not isinstance(data.get("paused", []), list):  # a string would pause by substring
            msg = "control.json: paused is not a list"
            raise TypeError(msg)  # noqa: TRY301 - one handler for every unreadable file
        kept = {key: data[key] for key in ("context_caps",) if key in data}
        return {**kept, "paused": frozenset(data.get("paused", []))}
    except (OSError, ValueError, TypeError, AttributeError) as error:
        log_error(event, error)
        return {"paused": frozenset()}


def say(deny: str | None, note: str | None = None, warning: str | None = None) -> None:
    """Print the hook's one answer: a deny, else a countdown; a config warning beside either."""
    answer: dict[str, Any] = {"systemMessage": warning} if warning else {}
    decision = {"hookEventName": "PreToolUse"}
    if deny:
        decision |= {"permissionDecision": "deny", "permissionDecisionReason": deny}
    elif note:
        decision |= {"additionalContext": note}
    if deny or note:
        answer["hookSpecificOutput"] = decision
    if answer:
        print(json.dumps(answer))  # noqa: T201 - stdout is the hook's answer


def count(agent: str, *, judged: bool) -> None:
    """Add one byte to the agent's window file for a judged call; delete it for any other.

    An append needs no lock: parallel calls each count; a lost race over-counts, never under.
    # ponytail: window files are never swept; a sweep of files older than 7 days if the folder grows
    """
    window = os.path.join(WINDOWS, agent)
    if not judged:
        try:  # noqa: SIM105 - contextlib would cost the fast path its import
            os.remove(window)
        except FileNotFoundError:
            pass
        return
    os.makedirs(WINDOWS, exist_ok=True)
    with open(window, "ab") as handle:
        handle.write(b".")


def project() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def own_transcript(named: str, agent: str, *, main: bool) -> str:
    """Return the agent's own transcript; a subagent names the session's (T-b0), a main its own."""
    if main:  # a main session judged under a role: the named path is its own file
        return named
    own, folder = f"agent-{agent}.jsonl", os.path.join(os.path.splitext(named)[0], "subagents")
    return named if os.path.basename(named) == own else os.path.join(folder, own)


def cached_floor(root: str) -> int | None:
    """Return the floor saved for these config files; none, or one saved for others: None."""
    from dev_loop.dashboard.floor import config_key, floor_file  # noqa: PLC0415 - os, zlib

    try:
        saved = json.loads(read(floor_file(HOME, root)))
    except (OSError, ValueError):
        return None
    floor = saved.get("floor") if isinstance(saved, dict) else None
    return floor if type(floor) is int and saved.get("key") == config_key(PLUGIN, root) else None


def save_floor(path: str, key: list[list[int] | None], config: Any) -> None:  # noqa: ANN401
    """Save the lowest context any cap trips at: the smallest role min times the threshold.

    A stated budget is clamped into its role's min..max; an owner's cap or pause is read per call.
    The key was taken before the config was read, so an edit in between only makes a miss.
    """
    import contextlib  # noqa: PLC0415 - the slow path's alone

    mins = [role["context"]["min"] for role in config.values["roles"].values() if role["context"]]
    floor = int(min(mins) * config.get("handoff.threshold"))
    tmp = f"{path}.{os.getpid()}.tmp"
    with contextlib.suppress(OSError):  # a cache: a write that fails only costs the next call
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, "floor": floor}))
        os.replace(tmp, path)


def configured(root: str) -> Any:  # noqa: ANN401 - a Loaded; dev_loop is imported late
    """Return the merged config; save its floor under the files' stat taken before the read."""
    from pathlib import Path  # noqa: PLC0415

    from dev_loop.config import load  # noqa: PLC0415
    from dev_loop.dashboard.floor import config_key, floor_file  # noqa: PLC0415

    key = config_key(PLUGIN, root)
    config = load(Path(root))
    save_floor(floor_file(HOME, root), key, config)
    return config


def below_floor(event: dict[str, Any], agent: str, *, main: bool) -> bool:
    """Return True when judge would answer nothing, decided without YAML: the fast path.

    No model call yet, or a context under the saved floor, and no pause or owner's cap on the
    call: the slow path's answer is silence, and a window open under the trip closes as there.
    No floor saved for these config files, or anything else: judge decides (and saves one).
    """
    from dev_loop.dashboard import tail  # noqa: PLC0415 - json, os and datetime only

    floor = cached_floor(project())
    if floor is None:
        return False
    path = own_transcript(os.fspath(event["transcript_path"]), agent, main=main)
    record = tail.last_assistant(path)
    context = None if record is None else tail.context_of(record)
    if context is not None and context >= floor:
        return False
    owner = controls(event)
    if {event["session_id"], agent} & owner["paused"] or agent in owner.get("context_caps", {}):
        return False
    if context is not None:  # as judge counts a call under the trip
        count(agent, judged=False)
    return True


def record_agent(event: dict[str, Any], agent: str, context: int) -> None:
    """Append one subagent telemetry line, rotated past 5 MB; best-effort, never the verdict.

    Ids, counts and the agent's type only: no prompt, tool or document text. A write that
    fails costs only the next sample, like the floor cache; it must not turn a deny into an allow.
    """
    import contextlib  # noqa: PLC0415 - the slow path's alone

    row = {
        "session_id": plain(event.get("session_id")),
        "agent_id": agent,
        "agent_type": kind(event.get("agent_type")),
        "context_tokens": context,
        "at": time.time(),
    }
    with contextlib.suppress(OSError):
        os.makedirs(HOME, exist_ok=True)
        if os.path.exists(AGENTS) and os.path.getsize(AGENTS) > AGENTS_MAX:
            os.replace(AGENTS, os.path.join(HOME, "agents.1.jsonl"))
        with open(AGENTS, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


def judge(event: dict[str, Any], agent: str, main_role: str | None) -> tuple[Any, str | None]:
    """Return the verdict on one call and the project file's warning; count the window.

    A main session judged under a role: main_role names it and the runner, not a parent
    agent, continues the hand-off, so the note goes to DEV_LOOP_HANDOVER.
    """
    # Imported here: a main session's call loads no YAML and no scanner.
    from dataclasses import replace  # noqa: PLC0415

    from dev_loop.dashboard import scan  # noqa: PLC0415
    from dev_loop.dashboard.gate import Caller, Controls, cap_of, role_for, roles  # noqa: PLC0415
    from dev_loop.dashboard.gate import verdict as answer_of  # noqa: PLC0415
    from dev_loop.dashboard.spend import Totals  # noqa: PLC0415

    path = own_transcript(os.fspath(event["transcript_path"]), agent, main=main_role is not None)
    context = scan.last_call(path)
    if context is not None:  # a real reading: one telemetry line for the fleet dashboard
        record_agent(event, agent, context)
    owner = Controls(**controls(event))
    config = configured(project())
    stated_role, stated = scan.stated_budget(scan.first_line(path)) if context else (None, None)
    role = role_for(roles(config.values), event.get("agent_type"), main_role or stated_role)
    cap = cap_of(role, stated, owner.context_caps.get(agent)) if role else None
    window = os.path.join(WINDOWS, agent)
    calls = os.path.getsize(window) if os.path.exists(window) else 0  # the count before this call
    caller = Caller(
        event["session_id"],
        agent,
        event["tool_name"],
        role,
        window=("context", calls) if calls else None,
        cap=cap,
        threshold=config.get("handoff.threshold"),
        caps_enabled=config.get("caps.enabled"),
        handover_file=os.environ.get("DEV_LOOP_HANDOVER") if main_role else None,
    )
    now = time.time()
    totals = Totals(now, 0.0, 0.0, {}, {agent: context or 0})
    answer = answer_of(caller, owner, totals, now)
    if answer.kind:  # the steps follow the branch: git runs only when the text is shown
        branch = on_branch(event.get("cwd"), config.get("branches.change_prefix"))
        reply = replying(event.get("agent_type"))  # keywords, not a dict: each is type-checked
        stepped = replace(caller, on_change_branch=branch, read_only=reply)
        answer = answer_of(stepped, owner, totals, now)
    if context is not None:  # no model call yet: no window change
        count(agent, judged=bool(answer.kind))
    return answer, config.error and f"{config.error}; running on the defaults"


def replying(agent_type: object) -> bool:
    """Return whether the agent hands off in its final reply: it cannot run the record command."""
    from pathlib import Path  # noqa: PLC0415

    from dev_loop.dashboard.gate import read_only  # noqa: PLC0415

    return read_only(kind(agent_type), Path(PLUGIN), Path(project()))


def on_branch(cwd: object, prefix: str) -> bool:
    from pathlib import Path  # noqa: PLC0415

    from dev_loop.dashboard.gate import on_branch as checked_out  # noqa: PLC0415

    return isinstance(cwd, str) and checked_out(Path(cwd), prefix)


def answer(event: dict[str, Any]) -> None:
    """Answer one PreToolUse event on stdout; an agent id that is not plain raises."""
    agent = event.get("agent_id")
    main_role = None
    if agent is None:  # a main session: uncapped unless the runner names its role
        main_role = os.environ.get("DEV_LOOP_ROLE") or None
        if main_role is None:  # no role: never capped, only paused, exactly as before
            if event["session_id"] in controls(event)["paused"]:
                say(PAUSED)
            return
        agent = event["session_id"]  # judged as the role, keyed and transcribed by the session
    if plain(agent) is None:  # never a path built from it
        msg = "agent_id is not a plain id"
        raise ValueError(msg)
    if below_floor(event, agent, main=main_role is not None):
        return
    from dev_loop.hookenv import add_venv  # noqa: PLC0415 - the slow path's alone

    # ponytail: PyYAML's pure-Python part runs on any 3.x; its C part loads only on a match
    add_venv()  # the plugin venv's PyYAML, whichever Python 3 built it; no venv: site.main()
    verdict, warning = judge(event, agent, main_role)
    beat()  # judged, not merely started: "the gate is live" means it can still deny (G6)
    say(verdict.deny, verdict.context, warning)


def unable(error: BaseException) -> str:
    """Return why the gate cannot judge: the PyYAML exit line, else the class and module.

    No other exit text is echoed: it may quote a config value.
    """
    if isinstance(error, SystemExit) and error.code == NO_YAML:
        return NO_YAML
    module = getattr(error, "name", None)
    return type(error).__name__ + (f" {module}" if module else "")


def main() -> int:
    """Fail open, loud: any crash lets the call through, said once and logged."""
    event: Any = None
    code = 1
    try:
        event = json.loads(sys.stdin.buffer.read())
        answer(event)  # a non-object event fails inside: logged like any crash
    except BaseException as error:  # noqa: BLE001 - the hook's one boundary, SystemExit too
        if isinstance(error, (ImportError, SystemExit)):  # not one bad call: no call is judged
            said = f"dev-loop gate is not enforcing: {unable(error)}; run dl doctor"
            print(json.dumps({"systemMessage": said}))  # noqa: T201 - said first: it needs nothing
            code = 0  # exit 0, or the message is never read
        import contextlib  # noqa: PLC0415 - only a crash pays for these

        from dev_loop.hookenv import crashed  # noqa: PLC0415

        crashed("context-gate", error, "call allowed")  # the line, and hook-errors.jsonl
        with contextlib.suppress(OSError):  # an unwritable folder: the stderr line still shows
            log_error(event, error)
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
