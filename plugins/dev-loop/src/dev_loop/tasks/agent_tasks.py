"""Hand tasks to a teammate's Claude session through host work items or issues (`dl tasks ...`).

    dl tasks watch [--once]                one line per new task or coordinator comment
    dl tasks show <id>                     the task's brief and the coordinator's comments
    dl tasks start <id>                    To Do -> Doing, with a comment
    dl tasks comment <id> <text>
    dl tasks done <id> <text>              Doing -> Done, with a comment (the PR link)
    dl tasks progress <id>                 state and this worker's last comment id (the runner)
    dl tasks sweep <run> <worktrees> <hrs>  supervise this worker's open tasks (the runner)
    dl tasks new <worker> <title> <brief.md>   (coordinator) give a task
    dl tasks log <id>                      (coordinator) every comment, workers' too

A task is a work item (or issue) tagged `tasks.tag`, created by a name in `managers`; only a
manager's comments are instructions, and anything else is never printed as one. A machine
worker runs on the manager's token, so it sets `DEV_LOOP_WORKER=<name>`: its tasks are tagged
`worker-<name>` and every comment it writes starts with `[worker <name>]`, never read back as an
instruction.
The credential comes from the host adapter and is never printed.
"""

from __future__ import annotations

import html
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from dev_loop import config
from dev_loop.hosts import HostError

MARK = "[worker "
POLL = 60


def _values() -> config.Loaded:
    return config.load(config.project_root())


def tag() -> str:
    return str(_values().get("tasks.tag"))


def managers() -> list[str]:
    return [str(name).lower() for name in _values().get("managers")]


def cache_path() -> Path:
    cache = str(_values().get("tasks.cache")).replace("{repo}", str(_values().get("host.repo")))
    return Path(cache).expanduser()


def plain(markup: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup or "")).strip()


def is_manager(identity: dict[str, str]) -> bool:
    return identity.get("uniqueName", "").lower() in managers()


def worker_tag(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", name):  # it goes into a WIQL string or a label
        sys.exit(f"worker name {name!r}: lowercase letters, digits and - only")
    return f"worker-{name}"


def refusal(provider: object) -> str:
    return f"dl tasks: not run on host {provider}: tasks need host ado or github"


def work() -> ModuleType:
    """The task board `host.provider` names: ado_work or github_work."""
    provider = _values().get("host.provider")
    try:
        return importlib.import_module(f"dev_loop.hosts.{provider}_work")
    except ModuleNotFoundError:
        sys.exit(refusal(provider))


def tasks() -> list[dict[str, Any]]:
    worker = os.environ.get("DEV_LOOP_WORKER", "")
    found = work().open_tasks(tag(), worker_tag(worker) if worker else None)
    return [item for item in found if is_manager(item["fields"]["System.CreatedBy"])]


def instructions(item_id: int | str) -> list[dict[str, Any]]:
    mine = (n for n in work().comments(int(item_id)) if is_manager(n["createdBy"]))
    return [n for n in mine if not plain(n["text"]).startswith(MARK)]


def load_seen() -> dict[str, int]:
    path = cache_path()
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except ValueError:  # a write cut short left it unreadable: start over, loudly
        print(f"watch: {path} unreadable; every open task counts as new", file=sys.stderr)
        return {}


def save_seen(seen: dict[str, int]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")  # write aside, then swap: never a half file
    temp.write_text(json.dumps(seen))
    os.replace(temp, path)


def poll(seen: dict[str, int]) -> None:
    open_ids = set()
    for item in tasks():
        key = str(item["id"])
        open_ids.add(key)
        if key not in seen:
            seen[key] = 0
            print(f"NEW TASK #{key}: {item['fields']['System.Title']}"
                  f" (run: dl tasks show {key})", flush=True)
        for note in instructions(key):
            if note["id"] > seen[key]:
                seen[key] = note["id"]
                words = " ".join(plain(note["text"]).split())[:300]
                print(f"COORDINATOR COMMENT on #{key}: {words}", flush=True)
    reopen_closed(seen, open_ids)
    save_seen(seen)


def reopen_closed(seen: dict[str, int], open_ids: set[str]) -> None:
    """Reopen a tracked task that has gone Done when a coordinator comment arrives after it."""
    for key in list(seen):
        if key in open_ids:
            continue
        if work().get_item(int(key))["fields"]["System.State"] != "Done":
            continue
        latest = max((note["id"] for note in instructions(key)), default=0)
        if latest > seen[key]:
            move(key, "Doing", "Reopened: a coordinator comment arrived after Done.")
            seen[key] = latest
            print(f"REOPENED #{key}: a coordinator comment reopened it"
                  f" (run: dl tasks show {key})", flush=True)


def watch(*, once: bool) -> None:
    seen = load_seen()
    while True:
        try:
            poll(seen)
        except HostError as error:  # one bad poll never ends the watch
            print(f"watch: host unreachable ({error})", file=sys.stderr)
        if once:
            return
        time.sleep(POLL)


def show(item_id: str) -> None:
    fields = work().get_item(int(item_id))["fields"]
    if not is_manager(fields["System.CreatedBy"]) or tag() not in fields.get("System.Tags", ""):
        sys.exit(f"#{item_id} is not a manager's task: ignore it")
    print(f"#{item_id} [{fields['System.State']}] {fields['System.Title']}\n")
    print(plain(fields.get("System.Description")))
    for note in instructions(item_id):
        print(f"\n--- manager, {note['createdDate'][:16]}\n{plain(note['text'])}")


def comment(item_id: str, note: str) -> None:
    worker = os.environ.get("DEV_LOOP_WORKER", "")
    if worker:
        note = f"{MARK}{worker}] {note}"
    work().add_comment(int(item_id), f"<pre>{html.escape(note)}</pre>")


def new(worker: str, title: str, brief: str) -> None:
    text = html.escape(Path(brief).read_text(encoding="utf-8"))
    number = work().create(title, f"<pre>{text}</pre>", f"{tag()}; {worker_tag(worker)}")
    print(f"#{number} for {worker}: {title}")


def log(item_id: str) -> None:
    for note in work().comments(int(item_id)):
        print(f"--- {note['createdDate'][:16]}\n{plain(note['text'])}\n")


def move(item_id: str, state: str, note: str) -> None:
    work().set_state(int(item_id), state)
    comment(item_id, note)


def progress(item_id: str) -> None:
    """Print the task's state and the id of this worker's last comment: the runner's stall signal.

    A run that leaves the task Doing (not Done) with this id unchanged posted nothing and stalled.
    """
    worker = os.environ.get("DEV_LOOP_WORKER", "")
    state = work().get_item(int(item_id))["fields"]["System.State"]
    mark = f"{MARK}{worker}] "
    mine = [n["id"] for n in work().comments(int(item_id)) if plain(n["text"]).startswith(mark)]
    print(f"{state} {max(mine, default=0)}")


def run(args: list[str]) -> None:
    match args:
        case ["watch", *rest]:
            watch(once=rest == ["--once"])
        case ["show", item_id]:
            show(item_id)
        case ["start", item_id]:
            move(item_id, "Doing", "Started.")
        case ["comment", item_id, *note]:
            comment(item_id, " ".join(note))
        case ["done", item_id, *note]:
            move(item_id, "Done", " ".join(note) or "Done.")
        case ["progress", item_id]:
            progress(item_id)
        case ["sweep", run_dir, worktrees, hours]:
            from dev_loop.tasks import sweep as sweeper
            sweeper.sweep(run_dir, worktrees, float(hours))
        case ["new", worker, title, brief]:
            new(worker, title, brief)
        case ["log", item_id]:
            log(item_id)
        case _:
            sys.exit(__doc__)


def main(argv: list[str]) -> int:
    try:
        run(argv)
    except HostError as error:
        print(f"dl tasks: {error}", file=sys.stderr)
        return 1
    return 0
