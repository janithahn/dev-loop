"""`dl records distil` and `approve`: a skill drafted from a clean PR.

The draft is assembled from the PR's records, its diff's file list and the lessons recalled for
the place it changed most: never proposal, spec or document text. Records are read through
`record.trusted` (via `past_lessons`) and written by `record.write`. A refusal raises ValueError.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import record
from .review_classes import changes_dir
from .review_pitfalls import RECALL, garbled, line_for, nearest, past_lessons, place_of
from .review_rules import git

SKILLS = Path(".claude") / "skills"
KINDS = ("metrics", "round", "lesson", "skill", "skills")
MAX_ROUNDS = 2
DECISION = re.compile(r"^(?:- \*\*(D\d+\b[^*\n]*)|### (.+))", re.MULTILINE)  # `- **D1 T.**`, ###
TEST = re.compile(r"(?:^|/)test_[^/]*$|[._]test\.[^/]*$")  # test_x.py, x.test.tsx, x_test.go
Found = list[tuple[str, dict[str, Any]]]


def staging(root: Path) -> Path:
    """Return the folder of drafts, beside the changes folder."""
    return changes_dir(root).parent / "skills-proposed"


def source(found: Found, pr: int) -> tuple[str, int]:
    """Return the change holding PR `pr`'s records and its rounds: the metrics row, else rounds."""
    mine = [(c, r) for c, r in found if r["kind"] in ("metrics", "round") and r["pr"] == pr]
    if not mine:
        raise ValueError(f"no records of PR {pr}")
    change = mine[-1][0]
    stated = [r["rounds"] for _, r in mine if r["kind"] == "metrics" and r["rounds"] is not None]
    rounds = stated[-1] if stated else sum(r["kind"] == "round" for _, r in mine)
    if not rounds:
        raise ValueError(f"PR {pr} ({change}): no rounds on record; unknown is never clean")
    if rounds > MAX_ROUNDS:
        raise ValueError(f"PR {pr} ({change}): {rounds} rounds; distil takes 1 or 2")
    if last_round(mine) not in ((0, 0), ("0", "0")):
        raise ValueError(f"PR {pr} ({change}): its last round is not on record as clean")
    return change, rounds


def last_round(mine: Found) -> tuple[object, ...]:
    """Return (blocker, major) of the PR's highest round line, else of its metrics row's last."""
    rows = [r for _, r in mine if r["kind"] == "round"]  # file order is not round order
    lines = [(r["blocker"], r["major"]) for r in sorted(rows, key=lambda r: r["round"] or 0)]
    per = [r["per_round"] for _, r in mine if r["kind"] == "metrics" and r["per_round"]]
    if lines:
        return lines[-1]
    return tuple(per[-1].split(",")[-1].strip().split("/")[:2]) if per else ()  # "0/1/6, 0/0/1"


def diff(root: Path, pr: int) -> tuple[str, str, list[tuple[str | None, int, str]]]:
    """Return PR `pr`'s merge commit, its place (most lines changed) and (place, lines, path)s."""
    grep = f"--grep=^Merged? (PR|pull request) {pr}( |:)"
    try:
        sha = git(root, "log", "--all", "--first-parent", "-1", "--format=%H", "-E", grep).strip()
        stat = git(root, "diff", "--numstat", "--no-renames", f"{sha}^1", sha) if sha else ""
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"git cannot answer: {type(error).__name__}") from error
    if not sha:
        raise ValueError(f"PR {pr} is not merged here")
    lines: Counter[str] = Counter()
    files = []
    skipped = (f"{changes_dir(root).as_posix()}/", f"{staging(root).as_posix()}/", "docs/")
    for added, deleted, path in (row.split("\t", 2) for row in stat.splitlines()):
        if path.startswith(skipped):  # records, drafts and docs: not what a skill is for
            continue
        count = sum(int(n) for n in (added, deleted) if n.isdigit())  # a binary file: -, 0 lines
        at = place_of(path, root)
        files.append((at, count, path))
        if at:
            lines[at] += count
    place = max(sorted(lines), key=lines.__getitem__, default=None)
    if place is None:
        raise ValueError(f"PR {pr} changed no folder a skill could be for")
    return sha, place, files


def draft(root: Path, pr: int) -> Path:
    """Write the draft SKILL.md for PR `pr` into the staging folder; never over an existing one."""
    found, _ = past_lessons(root, "", KINDS)
    change, rounds = source(found, pr)
    sha, place, files = diff(root, pr)
    target = root / staging(root) / change / "SKILL.md"
    if target.exists() or (root / SKILLS / change).exists():
        raise ValueError(f"{change}: a draft or a skill of that name exists")
    design = record.text_of(root / changes_dir(root) / change / "design.md")
    caught = [
        f"- {r['id']}: {r['avoid']}"
        for c, r in found if c == change and r.get("avoid") and not garbled(r)
    ]
    tests = [f"- `{p}`" for _, _, p in files if TEST.search(p)]  # wherever the PR put them
    changed = [f"- `{p}` ({n} lines)" for at, n, p in files if at == place and not TEST.search(p)]
    decisions = [f"- {''.join(d).strip()}" for d in DECISION.findall(design)]
    near = nearest(root, change, [f"{place}/_.py"])[1]  # a file in the place (a folder: _/_)
    past = [line_for(c, r) for share, c, r in near if share][:RECALL]  # at the place only
    today = datetime.now(timezone.utc).date()  # noqa: UP017 - 3.10 hosts
    text = [
        "---", f"name: {change}",
        f"description: How PR {pr} ({change}) changed {place}/ and closed clean in {rounds} "
        f"review round(s). Use when changing files under {place}/.", "---", "",
        f"# {change}: a clean change to {place}/", "",
        f"Drafted {today} by `dl records distil {pr}` from the PR's records, its diff's "
        f"file list and the lessons logged for {place}/. A draft until the owner approves it.", "",
        f"## What it changed (merge {sha[:12]})",
        *changed, "", "## Tests it shipped", *(tests or ["- none"]), "",
        "## Decisions it held to (design.md)", *(decisions or ["- none recorded"]), "",
        "## Past lessons for this place (recall)", *(past or ["- none logged"]), "",
        "## What its review caught", *(caught or ["- nothing: no lesson logged"]),
    ]
    target.parent.mkdir(parents=True)
    target.write_text("\n".join(text) + "\n", encoding="utf-8", newline="\n")
    return target


def approve(root: Path, pr: int, by: str) -> str:
    """The owner's step: move PR `pr`'s draft into .claude/skills, then record who approved it.

    All or nothing: a bad record is refused before the move, a
    refused write moves the draft back, a run stopped between the two is finished by a rerun.
    """
    found = past_lessons(root, "", KINDS)[0]
    change, _ = source(found, pr)
    staged, target = root / staging(root) / change, root / SKILLS / change
    skill = {"kind": "skill", "skill": change, "place": diff(root, pr)[1], "pr": pr, "by": by}
    if bad := record.faults(skill, writing=True):
        raise ValueError("; ".join(bad))
    done = any(r["kind"] == "skill" and r["skill"] == change for _, r in found)
    half = not staged.exists() and (target / "SKILL.md").exists() and not done  # moved, unrecorded
    if not half and (not (staged / "SKILL.md").exists() or target.exists()):
        raise ValueError(f"no draft for PR {pr} to approve, or {target.name} is a skill already")
    try:
        if not half:
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.rename(target)
    except OSError as error:
        raise ValueError(f"cannot move the draft: {type(error).__name__}") from error
    try:
        return str(record.write(root, change, skill)["line"])
    except Exception:
        if not half:
            target.rename(staged)  # nothing recorded: the draft goes back
        raise


def approved(found: Found, places: set[str]) -> list[str]:
    """Return a line per owner-approved skill in `found` whose place is among `places`."""
    return [
        f"- skill {r['skill']} (.claude/skills/{r['skill']}/SKILL.md, from PR {r['pr']})"
        for _, r in found if r["kind"] == "skill" and r["place"] in places
    ]


def tally(root: Path) -> list[str]:
    """Return the measure (skills distilled / used), then each approved skill and its users."""
    found, bad = past_lessons(root, "", KINDS)
    drafts = sorted(p.parent.name for p in (root / staging(root)).glob("*/SKILL.md"))
    lines: list[str] = []
    used_count = 0
    for change, skill in found:
        if skill["kind"] != "skill" or not skill["skill"]:  # a fieldless legacy row
            continue
        word = re.compile(rf"(?<![\w-]){re.escape(skill['skill'])}(?![\w-])")
        users = {c for c, r in found if r["kind"] == "skills" and word.search(r["skills"] or "")}
        users.discard(change)
        used_count += bool(users)
        lines.append(f"- {skill['skill']} for {skill['place']} (PR {skill['pr']}), approved by "
                     f"{skill['by']}; used by: {', '.join(sorted(users)) or 'none yet'}")
    head = f"skills: {len(drafts) + len(lines)} distilled, {len(lines)} approved, {used_count} used"
    skipped = [f"{bad} bad sidecar lines skipped (dl records unparsed)"] if bad else []
    return [f"{head}; awaiting approval: {', '.join(drafts) or 'none'}", *lines, *skipped]


def run(root: Path, command: str, args: list[str], by: str | None) -> list[str]:
    """The CLI: `distil [pr]`, `approve <pr> --by <name>`; a PR id not a number is refused."""
    if command == "distil" and not args:
        return tally(root)
    if len(args) != 1 or not args[0].isdigit():
        raise ValueError(f"give one PR id: {command} <pr>")
    if command == "approve":
        return [approve(root, int(args[0]), by or "")]
    return [draft(root, int(args[0])).relative_to(root).as_posix()]
