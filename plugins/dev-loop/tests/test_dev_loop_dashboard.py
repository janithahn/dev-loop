"""Self-check for the dev-loop dashboard's scanner, reading.

Tokens, cost, context, the window, incremental reads and what counts as unreadable, on fixture
transcripts only (`test_dev_loop_fixtures`). Run: python tests/test_dev_loop_dashboard.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import json
import os

from dev_loop.dashboard import scan
from dev_loop.dashboard.scan import Call, Scanner
from test_dev_loop_fixtures import (
    AGENT,
    BRANCH,
    DAY,
    MILLION,
    MINUTE,
    NOW,
    SESSION,
    Record,
    Tokens,
    Tree,
    assistant,
    line,
    run,
    scanned,
)


def check_one_message_id_counts_once_the_last_record_wins(tree: Tree) -> None:
    records: list[Record | str] = [assistant("msg_1", tokens=(2, n, 0, 0, 0)) for n in (5, 9, 40)]
    found = tree.one([*records, assistant("msg_2", tokens=(1, 3, 0, 0, 0))])
    assert sorted(found.calls) == ["msg_1", "msg_2"]
    assert found.calls["msg_1"].output == 40
    assert sum(call.output for call in found.calls.values()) == 43


def check_context_is_the_latest_calls_prompt_and_a_zero_call_keeps_it(tree: Tree) -> None:
    found = tree.one(
        [
            assistant("m1", tokens=(1, 5, 100, 0, 900)),
            assistant("m2", tokens=(3, 5, 20, 7, 1000)),
            assistant("m3", model="<synthetic>", tokens=(0, 0, 0, 0, 0)),
        ]
    )
    assert found.context == 3 + 20 + 7 + 1000
    assert found.calls["m2"].context == 1030


def check_cost_per_model_at_list_price(_: Tree) -> None:
    def usd(model: str, tokens: Tokens) -> float:
        return round(Call(0, model, *tokens).usd, 6)

    assert usd("claude-opus-5-5", (MILLION, MILLION, 0, 0, MILLION)) == 24.2
    assert usd("claude-sonnet-5-5", (MILLION, MILLION, 0, 0, MILLION)) == 12.2
    assert usd("claude-fable-5-1", (MILLION, MILLION, 0, 0, MILLION)) == 60.25
    assert usd("claude-opus-5-5", (0, 0, MILLION, 0, 0)) == 5.0
    assert usd("claude-opus-5-5", (0, 0, 0, MILLION, 0)) == 8.0
    assert usd("claude-haiku-9", (MILLION, 0, 0, 0, 0)) == 10.0  # unlisted: the dearest


def check_a_write_without_the_split_is_a_five_minute_write(tree: Tree) -> None:
    record = assistant("m1")
    message = record["message"]
    assert isinstance(message, dict)
    message["usage"] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": MILLION,
    }
    assert round(tree.one([record]).calls["m1"].usd, 6) == 5.0
    hour = assistant("m2", tokens=(0, 0, 0, MILLION, 0))
    assert round(tree.one([hour], "s2").calls["m2"].usd, 6) == 8.0


def check_the_last_tool_and_the_subagent_meta(tree: Tree) -> None:
    path = tree.write([assistant("m", tool="Grep")], agent=AGENT)
    meta = {"agentType": "builder", "description": "Build the dashboard part a"}
    path.with_name(f"agent-{AGENT}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (found,) = tree.scan()
    assert (found.session_id, found.agent_id) == (SESSION, AGENT)
    assert (found.agent_type, found.description) == ("builder", meta["description"])
    assert (found.last_tool, found.calls["m"].model) == ("Grep", "claude-opus-5-5")


def check_the_window_the_order_and_the_project(tree: Tree) -> None:
    tree.write([assistant("m")], "old", age=8 * DAY)
    tree.write([assistant("m")], "new", age=MINUTE)
    tree.write([assistant("m")], "newest")
    tree.write([assistant("m")], "other", project="C--Projects-elsewhere")
    assert [t.session_id for t in tree.scan()] == ["newest", "new"]
    assert [t.session_id for t in tree.scan(days=10)] == ["newest", "new", "old"]


def check_a_file_that_leaves_the_window_leaves_the_scan(tree: Tree) -> None:
    path = tree.write([assistant("m")])
    scanner = Scanner("app", 7, tree.root)
    tree.scan(scanner=scanner)
    os.utime(path, (NOW - 8 * DAY, NOW - 8 * DAY))
    assert tree.scan(scanner=scanner) == []
    assert scanner.transcripts == {}


def check_only_new_bytes_are_read(tree: Tree) -> None:
    path = tree.write([assistant("m1", tokens=(1, 5, 0, 0, 0))])
    scanner = Scanner("app", 7, tree.root)
    (first,) = tree.scan(scanner=scanner)
    size = path.stat().st_size
    # The bytes before the offset are never read again: re-read, this line would count.
    path.write_bytes(b'{"usage":{' + b"x" * (size - 11) + b"\n")
    half = line(assistant("m2", tokens=(1, 7, 0, 0, 0)))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(half[:40])
    (second,) = tree.scan(scanner=scanner)
    assert (second.offset, sorted(second.calls)) == (size, ["m1"])  # the half line waits
    with path.open("a", encoding="utf-8") as handle:
        handle.write(half[40:] + "\n")
    (third,) = tree.scan(scanner=scanner)
    assert first is third
    assert sorted(third.calls) == ["m1", "m2"]
    assert (third.unreadable, third.offset) == (0, path.stat().st_size)


def check_a_shrunk_file_is_read_again_and_a_bad_line_is_counted(tree: Tree) -> None:
    path = tree.write([assistant("m1"), assistant("m2")])
    scanner = Scanner("app", 7, tree.root)
    tree.scan(scanner=scanner)
    path.write_text(line(assistant("m3")) + "\n{not json\n", encoding="utf-8")
    (found,) = tree.scan(scanner=scanner)
    assert (sorted(found.calls), found.unreadable) == (["m3"], 0)
    tree.write(['{"type":"assistant","usage":{"x":1}}'])
    (found,) = tree.scan(scanner=scanner)
    assert found.unreadable == 1


def check_a_bad_usage_is_unreadable_never_a_default(tree: Tree) -> None:
    good = line(assistant("b"))
    broken = [
        good.replace('"input_tokens":1,', ""),  # missing
        good.replace('"output_tokens":10', '"output_tokens":"10"'),
        good.replace('"input_tokens":1', '"input_tokens":-1'),
        good.replace('"cache_read_input_tokens":0', '"cache_read_input_tokens":true'),
        good.replace('"model":"claude-opus-5-5",', ""),
        good.replace('"model":"claude-opus-5-5"', '"model":5'),
    ]
    assert all(bad != good for bad in broken)
    found = tree.one(broken)
    assert (found.calls, found.unreadable) == ({}, len(broken))
    nothing_cached = good.replace('"cache_read_input_tokens":0', '"cache_read_input_tokens":null')
    assert tree.one([nothing_cached], "s2").calls["b"].read == 0  # the API's null: no cache


def check_a_malformed_record_is_counted_and_the_pass_completes(tree: Tree) -> None:
    good = line(assistant("b", tool="Bash"))
    broken = [
        good.replace('"name":"Bash",', ""),  # a tool_use block with no name
        good.replace('"id":"b"', '"id":{"not":"hashable"}'),
        '{"type":"user","usage":{"x":' + "[" * 100_000 + "]" * 100_000 + "}}",  # too deep
        '{"type":"assistant","usage":{},"message":null}',
        "[1, 2]",  # valid JSON, not a record: it holds no marker, so it is never parsed
    ]
    tree.write(broken)
    tree.write([assistant("ok")], "s2")
    scanner = scanned(tree)
    assert (scanner.scan_error, scanner.dropped) == (None, {})
    assert scanner.scanned_at >= NOW
    one, two = sorted(scanner.transcripts.values(), key=lambda t: t.session_id)
    assert (one.calls, one.unreadable, one.turn) == ({}, 4, "none")  # nothing half applied
    assert list(two.calls) == ["ok"]


def check_any_failure_in_a_record_is_counted_never_the_file(tree: Tree) -> None:
    def failing(record: dict[str, object]) -> Call:  # say, timestamp() on a year-1 naive time
        if "boom" in str(record):
            message = "a timestamp out of the platform's range"
            raise OSError(message)
        return validate(record)

    tree.write([assistant("ok"), assistant("boom"), assistant("after")])
    validate, scan.call_of = scan.call_of, failing
    try:
        scanner = scanned(tree)
    finally:
        scan.call_of = validate
    (found,) = scanner.transcripts.values()
    assert (scanner.scan_error, scanner.dropped) == (None, {})
    assert (sorted(found.calls), found.unreadable) == (["after", "ok"], 1)


def check_a_title_that_is_not_a_name_is_unreadable(tree: Tree) -> None:
    titles: list[Record | str] = [
        {"type": "ai-title", "aiTitle": n} for n in (None, {"a": 1}, 5, "")
    ]
    found = tree.one([*titles, {"type": "custom-title", "customTitle": None}, assistant("m")])
    assert (found.unreadable, found.titles, found.who) == (5, {}, BRANCH)


if __name__ == "__main__":
    run(
        "dev-loop dashboard", [obj for name, obj in list(globals().items()) if name[:6] == "check_"]
    )
