"""Self-check for the dev-loop dashboard's scanner, state.

Status, the session's name, failed passes and files, one pass at a time, torn reads and the
not-text rule, on fixture transcripts only (`test_dev_loop_fixtures`). Run: python tests/test_dev_loop_status.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import contextlib
import io
import threading
from typing import TYPE_CHECKING

from dev_loop.dashboard import scan
from dev_loop.dashboard.scan import Scanner, Transcript
from test_dev_loop_fixtures import (
    AGENT,
    BRANCH,
    MINUTE,
    NOW,
    PROJECT,
    SECRET,
    SESSION,
    Record,
    Tree,
    assistant,
    bare_user,
    run,
    scanned,
    tool_result,
    user,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def check_running(tree: Tree) -> None:
    assert tree.status([user(), assistant("m", tool="Bash")]) == "running"
    assert tree.status([user(), assistant("m", tool="Read"), tool_result()], "s2") == "running"
    assert tree.status([user()], "s3") == "running"
    assert tree.status([user(), assistant("m", stop=None)], "s4") == "running"  # streaming
    # A user turn that carries a usage block (an agent's result, say) is parsed: still a turn.
    result = {**tool_result(), "toolUseResult": {"usage": {"input_tokens": 5}}}
    assert tree.status([user(), assistant("m", stop="end_turn"), result], "s5") == "running"


def check_a_prompt_with_only_its_prompt_id_is_a_turn(tree: Tree) -> None:
    first = bare_user("Build the dashboard part a")  # a subagent's first prompt, or a headless one
    assert tree.status([first], age=MINUTE, agent=AGENT) == "running"
    answered: list[Record | str] = [user(), assistant("m", stop="end_turn")]
    assert tree.status([*answered, first], "s2", age=MINUTE) == "running"


def check_a_long_bash_call_is_running_until_an_hour_without_a_write(tree: Tree) -> None:
    bash: list[Record | str] = [user(), assistant("m", tool="Bash")]
    assert tree.status(bash, age=50 * MINUTE) == "running"
    assert tree.status(bash, "s2", age=61 * MINUTE) == "stalled"
    assert tree.status([*bash, tool_result()], "s3", age=61 * MINUTE) == "stalled"


def check_a_finished_turn(tree: Tree) -> None:
    answered: list[Record | str] = [user(), assistant("m", stop="end_turn")]
    assert tree.status(answered, age=5 * MINUTE) == "idle"
    assert tree.status(answered, "s2", age=20 * MINUTE) == "stopped"
    assert tree.status(answered, age=5 * MINUTE, agent=AGENT) == "done"
    interrupted = [user(), assistant("m", tool="Bash"), bare_user("[Request interrupted by user]")]
    assert tree.status(interrupted, "s3", age=61 * MINUTE) == "stopped"


def check_records_no_model_answers_leave_a_finished_turn_finished(tree: Tree) -> None:
    answered: list[Record | str] = [user(), assistant("m", stop="end_turn")]
    nested: Record = {"type": "progress", "data": {"message": {"type": "user", "content": "x"}}}
    meta = {**user(), "isMeta": True}
    summary = {**user(), "isCompactSummary": True, "isVisibleInTranscriptOnly": True}
    command = bare_user("<local-command-stdout>model set</local-command-stdout>")
    records: list[Record] = [nested, meta, summary, command]
    for n, record in enumerate(records):
        assert tree.status([*answered, record], f"s{n}", age=MINUTE) == "idle", record


def check_the_session_name(tree: Tree) -> None:
    def title(kind: str, name: str) -> Record:
        return {"type": kind, "customTitle" if kind == "custom-title" else "aiTitle": name}

    assert tree.one([assistant("m")]).who == BRANCH
    named = [title("ai-title", "Old"), title("ai-title", "Screening fixes"), assistant("m")]
    assert tree.one(named, "s2").who == "Screening fixes"  # the latest AI title
    custom = [title("custom-title", "Main checkout"), title("ai-title", "Later"), assistant("m")]
    assert tree.one(custom, "s3").who == "Main checkout"  # the owner's name wins


def check_a_failed_pass_is_recorded_and_the_next_pass_runs(tree: Tree) -> None:
    class Flaky(Scanner):
        failures = 1

        def scan(self) -> Iterator[Transcript]:
            if self.failures:
                self.failures -= 1
                message = "state.json is held by another reader"
                raise PermissionError(message)
            return super().scan()

    tree.write([assistant("m")])
    scanner = Flaky("app", 7, tree.root)
    scanner.refresh()  # never raises
    assert (scanner.scanned_at, scanner.transcripts) == (0.0, {})
    assert scanner.scan_error == "PermissionError: state.json is held by another reader"
    scanner.refresh()
    assert scanner.scan_error is None
    assert scanner.scanned_at >= NOW
    assert next(iter(scanner.transcripts.values())).calls


def check_a_file_gone_or_unreadable_drops_out(tree: Tree) -> None:
    tree.write([assistant("m")])
    folder = tree.root / PROJECT / "folder.jsonl"
    folder.mkdir()  # listed, but cannot be read
    metas = {"half": "{", "null": "null", "list": "[]", "deep": "[" * 100_000 + "]" * 100_000}
    agents = {name: tree.write([assistant("a")], name, agent=AGENT) for name in metas}
    for name, meta in metas.items():  # half-written or not an object: read on a later pass
        agents[name].with_suffix(".meta.json").write_text(meta, encoding="utf-8")
    gone = tree.root / PROJECT / "gone.jsonl"
    with contextlib.suppress(OSError):  # no symlinks without privilege on Windows: pr-gates has
        gone.symlink_to(tree.root / "missing.jsonl")
    scanner = scanned(tree)
    assert (scanner.scan_error, [t.session_id for t in scanner.transcripts.values()]) == (
        None,
        [SESSION],
    )
    expected = {folder, *agents.values()} | ({gone} if gone.is_symlink() else set())
    assert set(scanner.dropped) == expected  # each surfaced, with its error's type
    assert all(kind.endswith("Error") for kind in scanner.dropped.values())


def check_passes_run_one_at_a_time_and_a_rerun_changes_nothing(tree: Tree) -> None:
    records: list[Record | str] = [assistant(f"m{n}", tokens=(1, 1, 0, 0, 0)) for n in range(200)]
    for n in range(20):
        tree.write(records, f"s{n}")
    scanner = Scanner("app", 7, tree.root)
    with scanner.passing:  # a pass already running: the next one waits for it
        waiting = threading.Thread(target=scanner.refresh)
        waiting.start()
        waiting.join(timeout=0.5)
        assert waiting.is_alive()
        assert (scanner.scanned_at, scanner.transcripts) == (0.0, {})
    waiting.join()
    first = {p: (dict(t.calls), t.offset) for p, t in scanner.transcripts.items()}
    scanner.refresh()
    assert first == {p: (dict(t.calls), t.offset) for p, t in scanner.transcripts.items()}
    assert all(t.offset == t.path.stat().st_size for t in scanner.transcripts.values())
    assert sum(len(t.calls) for t in scanner.transcripts.values()) == 20 * 200


def check_readers_never_see_a_torn_chunk(tree: Tree) -> None:
    records: list[Record | str] = [assistant(f"m{n}", tokens=(1, 1, 0, 0, 0)) for n in range(300)]
    data = {(path := tree.write(records, f"s{n}")): path.read_bytes() for n in range(20)}
    scanner, done = Scanner("app", 7, tree.root), threading.Event()
    errors: list[str] = []

    def read() -> None:  # what part b's page and hook do while the first index runs
        while not done.is_set():
            try:
                with scanner.lock:
                    for path, transcript in scanner.transcripts.items():  # one call a line
                        complete = data[path][: transcript.offset].count(b"\n")
                        assert len(transcript.calls) == complete
                        assert sum(call.usd for call in transcript.calls.values()) >= 0
            except (AssertionError, RuntimeError) as error:
                errors.append(repr(error))
                return

    reader, chunk, scan.CHUNK = threading.Thread(target=read), scan.CHUNK, 4096
    try:
        reader.start()
        scanner.refresh()
    finally:
        done.set()
        reader.join()
        scan.CHUNK = chunk
    assert errors == []
    assert sum(len(t.calls) for t in scanner.transcripts.values()) == 20 * 300


def check_no_text_in_any_state_output_or_stream(tree: Tree) -> None:
    document = user([{"type": "document", "source": {"type": "text", "data": f"inv {SECRET}"}}])
    thinking = assistant("m3", stop="end_turn")
    broken = f'{{"broken": "{SECRET}", "usage":{{'
    tree.write([user(), assistant("m1", tool="Bash"), tool_result(), document, thinking, broken])
    tree.write([user(), assistant("m2", tool="Read"), tool_result()], agent=AGENT)
    before = sorted(tree.root.rglob("*"))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        scanner = scanned(tree)
        found = list(scanner.transcripts.values())
        states = [(t.status(NOW), t.who) for t in found]
    kept = repr([[vars(t), t.calls] for t in found]) + repr((vars(scanner), states))
    assert sorted(tree.root.rglob("*")) == before  # the scanner writes no file
    assert all(t.calls for t in found)
    assert found[0].unreadable == 1
    assert SECRET not in kept + out.getvalue() + err.getvalue()
    assert "EXAMPLE" not in kept


if __name__ == "__main__":
    run("dev-loop status", [obj for name, obj in list(globals().items()) if name[:6] == "check_"])
