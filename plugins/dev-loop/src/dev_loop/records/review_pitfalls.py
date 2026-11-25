"""`review_records.py pitfalls <paths>`: the lessons most often logged against a change's packages.

A lesson with `Files:` belongs to the packages of those files. A lesson without it (history)
belongs to the packages the files of its change's merged PR touched: the PR ids come from the
change's `status.md` metrics rows, the files from one `git log --first-parent --name-only` over
`origin/<branches.integration>` (else `HEAD`). Packages are `knowledge.packages`.
Without git the counts are class-level, with a note. Reads only.
"""

from __future__ import annotations

import functools
import math
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..config import load
from .recall_near import GARBLED, near_enough, read_lessons
from .record import MARKDOWN, MIGRATED, change_folders, changes_dir, text_of, trusted, write
from .review_classes import ClassRow, class_rows
from .review_guards import BACKTICKED
from .review_lessons import NEVER, Lesson, class_guard, parse_lessons

MERGED = re.compile(r"^Merged? (?:PR|pull request) (\d+)")
METRICS_PR = re.compile(r"^\| (\d+) \| .*? \| \d+ \| ", re.MULTILINE)
# The frozen per-PR table (older PRs had a row there, not in their change's status.md).
FROZEN_PR = re.compile(
    r"^\| (\d+) \| (?:(?:fix|docs|ci)\()?([a-z0-9][a-z0-9-]*)(?=[\s|,)])", re.MULTILINE
)  # a name first, or inside fix( ), docs( ) or ci( )
RESEARCH = "docs/research"  # the folder of studies recall lists first
MAX_LINES = 5
UNKNOWN_NUMBER = 99
MAX_DEPTH = 2


@functools.lru_cache(maxsize=None)
def _packages(root: Path) -> tuple[str, ...]:
    """Return `knowledge.packages`, read once per root (recall asks per file of every PR)."""
    return tuple(load(root).get("knowledge.packages"))


def package_of(path: str, root: Path) -> str | None:
    """Return the package a path belongs to, by `knowledge.packages`, else None.

    An entry is `<root>[:<depth>][=<mirror>]`: a file under `<root>` belongs to the package of
    its first `<depth>` (default 2) folders, at least one (depth 0: `<root>` itself); with
    `=<mirror>` that package is named under `<mirror>` (tests mirroring the code they test).
    """
    path = path.replace("\\", "/").rstrip("/")
    if "." not in path.rsplit("/", 1)[-1]:  # a folder (no extension): read as a file inside it
        path += "/_"
    parts = path.split("/")
    for entry in _packages(root):
        spec, _, mirror = entry.partition("=")
        base, _, depth = spec.partition(":")
        head = base.strip("/").split("/")
        dirs = parts[len(head) : -1]
        if parts[: len(head)] != head or len(parts) == len(head):
            continue
        if int(depth or MAX_DEPTH) == 0:
            return base.strip("/")
        if dirs:
            named = mirror.strip("/").split("/") if mirror else head
            return "/".join([*named, *dirs[: int(depth or MAX_DEPTH)]])
    return None


def place_of(path: str, root: Path) -> str | None:
    """Return a path's package, else its folder (`scripts`, `hooks`); a top file: None."""
    path = path.replace("\\", "/").rstrip("/")
    return package_of(path, root) or path.rpartition("/")[0] or None


def pr_packages(root: Path, ref: str | None = None) -> dict[int, set[str]] | None:
    """Return merged PR id -> places of its files (`place_of`), or None when git cannot answer."""
    try:
        if ref is None:
            loaded = load(root)
            upstream = f"{loaded.get('branches.remote')}/{loaded.get('branches.integration')}"
            has = subprocess.run(  # noqa: S603, S607 - fixed git arguments
                ["git", "rev-parse", "--verify", "-q", upstream], cwd=root,
                capture_output=True, check=False,
            )
            ref = "HEAD" if has.returncode else upstream
        log = subprocess.run(  # noqa: S603, S607 - fixed git arguments
            ["git", "log", "--first-parent", "--name-only", "--format=%x1e%s", ref], cwd=root,
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=120,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    found: dict[int, set[str]] = {}
    for record in log.split("\x1e")[1:]:
        subject, _, files = record.partition("\n")
        merged = MERGED.match(subject)
        if merged:
            packages = {place_of(f, root) for f in files.split()}
            found[int(merged[1])] = {p for p in packages if p}
    return found


def prs_by_change(root: Path) -> dict[str, list[int]]:
    """Return each change's PR ids: its `status.md` metrics rows, then the frozen per-PR table."""
    found: dict[str, list[int]] = defaultdict(list)
    base = changes_dir(root)
    names = {d.name for d in base.rglob("*") if d.is_dir()}
    for path in sorted(base.rglob("status.md")):
        text = path.read_text(encoding="utf-8")
        found[path.parent.name] += [int(m[1]) for m in METRICS_PR.finditer(text)]
    for table in frozen_tables(root):
        for pr, change in FROZEN_PR.findall(table.read_text(encoding="utf-8")):
            if change in names and int(pr) not in found[change]:
                found[change].append(int(pr))
    return dict(found)


def frozen_tables(root: Path) -> list[Path]:
    """Return the metrics tables kept as they were: files of `paths.frozen` named *metrics*.md."""
    found = {p for pattern in load(root).get("paths.frozen") for p in root.glob(pattern)}
    found |= {m for p in found if p.is_dir() for m in p.glob("*metrics*.md")}
    return sorted(p for p in found if p.is_file() and "metrics" in p.name)


def change_prs(root: Path, change: str) -> list[int]:
    return prs_by_change(root).get(change, [])


History = tuple[dict[int, set[str]], dict[str, list[int]]]  # merged PR -> places; change -> PRs


def packages_of(root: Path, change: str, files: Iterable[str], history: History) -> set[str]:
    """Return the places of a lesson's files, else of its change's merged PRs' files."""
    if files := list(files):
        return {p for p in (place_of(f, root) for f in files) if p}
    merged, prs = history
    return set().union(*(merged.get(pr, set()) for pr in prs.get(change, [])))


def line_of(name: str, rows: list[ClassRow], lessons: list[Lesson]) -> str:
    number = next((f"class {r.number} " for r in rows if r.text == name), "")
    newest = max(lessons, key=lambda x: x.when or NEVER)
    changes = len({x.change for x in lessons})
    guard = newest.guard or class_guard(name, rows, lessons)
    return (
        f"{number}{name} · {len(lessons)} lessons in {changes} changes ({newest.change}) · "
        f"avoid: {newest.avoid or '—'} · guard: {guard}"
    )


def is_shallow(root: Path) -> bool:
    """True when the checkout is a shallow clone: older merged PRs are missing from its log."""
    try:
        done = subprocess.run(  # noqa: S603, S607 - fixed git arguments
            ["git", "rev-parse", "--is-shallow-repository"], cwd=root,
            capture_output=True, text=True, check=False,
        )
    except OSError:  # no git here (the dev container): nothing to say
        return False
    return done.stdout.strip() == "true"


def unplaced(root: Path, paths: list[str]) -> list[str]:
    """Return a line per path with no package (five at most), then a count of the rest."""
    loose = [p for p in paths if not package_of(p, root)]
    lines = [f"no package for {p}" for p in loose[:MAX_LINES]]
    if len(loose) > MAX_LINES:
        lines.append(f"{len(loose) - MAX_LINES} more paths without a package")
    return lines


def pitfalls(root: Path, paths: list[str], ref: str | None = None) -> list[str]:
    """Return "class · avoid rule · guard" lines for the paths' packages: five ranked at most.

    After them: a line per package with no lessons, the paths with no package, then the tail
    (changes whose lessons cannot be placed, a shallow history). Never an empty answer.
    """
    if not paths:
        return ["pitfalls unavailable: no paths given"]
    wanted = {p for p in (package_of(f, root) for f in paths) if p}
    no_package = unplaced(root, paths)
    if not wanted:
        return no_package
    rows = class_rows(root)
    merged, prs = pr_packages(root, ref), prs_by_change(root)
    history = (merged or {}, prs)
    by_class: dict[str, list[Lesson]] = defaultdict(list)
    lessons = parse_lessons(root, rows)
    covered: set[str] = set()
    for lesson in lessons:
        placed = wanted
        if merged is not None:
            placed = packages_of(root, lesson.change, lesson.files, history)
        here = placed & wanted
        if here:
            by_class[lesson.class_].append(lesson)
            covered |= here
    unmerged = {
        x.change for x in lessons
        if not x.files and not any(pr in (merged or {}) for pr in prs.get(x.change, []))
    }
    lines = [line_of(n, rows, by_class[n]) for n in ranked_classes(by_class, rows)]
    if merged is None:
        note = "note: git or the PR ids are unavailable; class-level counts, all packages"
        lines = [note, *lines[: MAX_LINES - 1]]
    empty = [f"no lessons yet for {package}" for package in sorted(wanted - covered)]
    tail = []
    if unmerged and merged is not None:
        tail.append(f"{len(unmerged)} changes without a merged PR")
    if is_shallow(root):
        tail.append("history: shallow (older PRs not attributed)")
    return [*lines[:MAX_LINES], *empty, *no_package, *tail]


MAX_RULES = 10


def do_not_repeat(root: Path, paths: list[str], ref: str | None = None) -> list[str]:
    """Return the newest concrete "Avoid:" rules logged for the paths' packages, ten at most.

    Newest first, one per distinct rule text, each tagged with its class and `change:Ln`,
    with its Self-check. A heading first; a "none logged" line when nothing matches.
    """
    heading = "Do not repeat"
    wanted = {p for p in (package_of(f, root) for f in paths) if p}
    none = [f"{heading}: none logged for these packages"]
    if not wanted:
        return none
    rows = class_rows(root)
    merged, prs = pr_packages(root, ref), prs_by_change(root)
    history = (merged or {}, prs)
    number = {r.text: r.number for r in rows}
    note = [] if merged is not None else [
        "note: git or the PR ids are unavailable; class-level, all packages"
    ]
    lessons = parse_lessons(root, rows)
    if merged is not None:
        lessons = [x for x in lessons if packages_of(root, x.change, x.files, history) & wanted]
    lines = newest_rules(lessons, number)
    return [heading, *note, *lines[:MAX_RULES]] if lines else none


def ranked_classes(by_class: dict[str, list[Lesson]], rows: list[ClassRow]) -> list[str]:
    """Return the classes by lesson count, then class number, then name."""
    number = {r.text: r.number for r in rows}
    return sorted(by_class, key=lambda n: (-len(by_class[n]), number.get(n, UNKNOWN_NUMBER), n))


def newest_rules(lessons: list[Lesson], number: dict[str, int]) -> list[str]:
    """Return a line per distinct "Avoid:" rule, newest first, tagged with class and `change:Ln`."""
    seen: set[str] = set()
    lines = []
    for lesson in sorted(lessons, key=lambda x: x.when or NEVER, reverse=True):
        key = lesson.avoid.lower()
        if not lesson.avoid or key.startswith("see ") or key in seen:
            continue
        seen.add(key)
        tag = f"class {number[lesson.class_]}" if lesson.class_ in number else "class ?"
        check = f" Self-check: {lesson.check}" if lesson.check else ""
        lines.append(f"- {tag} · {lesson.change}:{lesson.id} · {lesson.avoid}.{check}")
    return lines


RECALL = 3
WORD = re.compile(r"[a-z][a-z0-9_]{3,}")


def recall(root: Path, change: str, paths: list[str], ref: str | None = None) -> list[str]:
    """Return the research folder's listing, then up to three past lessons near `change` (k = 3).

    The ranking is `ranked`'s; only lessons over its relevance floor are shown, so
    fewer than three is fine (v0.4.19). The skills approved for the paths' places come first.
    A bad sidecar line, or a run-together lesson with nothing before its first label, is counted.
    """
    research = root / RESEARCH
    listing = ", ".join(sorted(p.name for p in research.iterdir())) if research.is_dir() else "none"
    lines, near = ranked(root, change, paths, ref)
    top = [line_for(name, lesson) for _, shown, name, lesson in near if shown][:RECALL]
    none = ["recall: no past lesson near this change"] if near and not top else []
    return [f"research (ls {RESEARCH}/): {listing}", *lines, *top, *none]


def nearest(
    root: Path, change: str, paths: list[str], ref: str | None = None
) -> tuple[list[str], list[tuple[float, str, dict[str, Any]]]]:
    """Return `ranked` as (share, change, lesson), without the run-together lessons (distil)."""
    lines, near = ranked(root, change, paths, ref)
    return lines, [(share, name, lesson) for share, _, name, lesson in near if "cut" not in lesson]


def ranked(
    root: Path, change: str, paths: list[str], ref: str | None = None
) -> tuple[list[str], list[tuple[float, bool, str, dict[str, Any]]]]:
    """Return recall's notes and every past lesson as (share, near, change, lesson), nearest first.

    Ranked by the share of a lesson's places that the paths touch (the files to touch, else the
    proposal's backticked paths), then by the rarity-weighted words shared with the proposal, then
    newest first; `near` is `near_enough`'s floor. Lessons are read through record.trusted (a bad
    line is counted); a run-together one is cut at its first embedded label, on a copy.
    """
    change = Path(change).name  # `me/` is the change `me`
    folder = changes_dir(root) / change
    if not change.strip("./\\") or not folder.is_dir():
        return [f"recall unavailable: no change {change}"], []
    proposal = text_of(folder / "proposal.md")
    paths = paths or [p for p in BACKTICKED.findall(proposal) if "/" in p]  # given paths win
    wanted = {p for p in (place_of(f, root) for f in paths) if p}
    found, bad = past_lessons(root, change, ("lesson", "skill"))  # the sidecars are read once
    from .review_distil import approved  # noqa: PLC0415 - review_distil imports this module
    lines = approved(found, wanted)
    lessons, skipped = read_lessons(root, found, bad)
    if not lessons:
        return [*lines, *skipped, "recall: no past lessons"], []
    merged, prs = pr_packages(root, ref), prs_by_change(root)
    history = (merged or {}, prs)
    texts = [f"{r.get('summary') or ''} {r.get('avoid') or ''}" for _, r in lessons]
    words = [set(WORD.findall(text.lower())) for text in texts]
    seen_in = Counter(w for got in words for w in got)
    query = set(WORD.findall(f"{proposal} {' '.join(paths)}".lower()))

    def rank(i: int) -> tuple[float, float, str]:
        # ponytail: plain idf, no BM25 length norm; summaries are a sentence or two.
        name, lesson = lessons[i]
        files = (lesson.get("files") or "").split()
        places = packages_of(root, name, files, history)
        shared = len(places & wanted) / len(places) if places else 0  # a wide PR is not a match
        score = sum(math.log(len(lessons) / seen_in[w]) for w in words[i] & query)
        return shared, score, lesson.get("when") or ""

    ranks = {i: rank(i) for i in range(len(lessons))}
    top = sorted(ranks, key=ranks.__getitem__, reverse=True)
    note = [] if merged is not None else ["note: no git; only lessons with files placed"]
    near = [near_enough(*ranks[i][:2], len(lessons), bool(wanted)) for i in range(len(lessons))]
    return [*lines, *note, *skipped], [(ranks[i][0], near[i], *lessons[i]) for i in top]


def garbled(lesson: dict[str, Any]) -> bool:
    """Return whether a lesson's summary or avoid holds another field's label (never shown)."""
    return any(GARBLED.search(lesson.get(field) or "") for field in ("summary", "avoid"))


SHOWN = re.compile(r"^- ([\w.-]+:L\d+) · ")


def record_shown(root: Path, change: str, lines: list[str]) -> None:
    """Record the lessons `recall` showed the builder: `impact` counts its hits."""
    ids = [m[1] for line in lines if (m := SHOWN.match(line))]
    if ids:
        write(root, change, {"kind": "recall", "change": change, "shown": " ".join(ids)})


def past_lessons(
    root: Path, change: str, kinds: tuple[str, ...] = ("lesson",)
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    """Return (change, record) for each other change's record of `kinds`, and the bad lines skipped.

    Read through record.trusted, the checked reader: a record off the grammar is never recalled.
    """
    old, bad, lessons = text_of(changes_dir(root) / MIGRATED).split(), 0, []
    for folder in change_folders(root):
        try:
            checked = trusted(folder, MARKDOWN, folder.name in old)
        except UnicodeDecodeError:  # not UTF-8: counted as bad, never a stop
            bad += 1
            continue
        bad += sum(bool(faults) for _, _, faults in checked)
        lessons += [
            (folder.name, r) for _, r, faults in checked
            if not faults and r["kind"] in kinds and folder.name != change
        ]
    return lessons, bad


def line_for(change: str, lesson: dict[str, Any]) -> str:
    summary, avoid = lesson.get("summary") or "—", lesson.get("avoid") or "—"  # no None
    return f"- {change}:{lesson['id']} · {summary} · avoid: {avoid}"
