"""`dl record` guards on the local host: a round or lesson needs a clean `review-run` behind it,
a round's fix keeps the integration branch's assertions and tests what it changes, and the
lessons-logged count is the number of lesson records."""

from __future__ import annotations

import functools
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..config import PROJECT, load, project_root

SHA = re.compile(r"[0-9a-f]{7,40}")
CUT = re.compile(r"assert|expect\(")
Records = list[dict[str, Any]]


def _refuse(why: str) -> None:
    print(f"dl record: refused: {why}", file=sys.stderr)
    raise SystemExit(2)


def _lessons_counted(root: Any, loaded: Any, change: str, asked: dict[str, Any]) -> None:  # noqa: ANN401
    """Exit 2 unless `count` is the number of the change's lesson records."""
    from . import record  # noqa: PLC0415 - record imports this module
    folder = root / str(loaded.get("paths.changes")) / change
    have = sum(1 for r in record.read(folder) if r["kind"] == "lesson")
    if str(asked.get("count")) != str(have):
        _refuse(f"count={asked.get('count')} but {have} lesson records")


def _pause_cited(root: Any, loaded: Any, folder: Path, asked: dict[str, Any]) -> None:  # noqa: ANN401
    """Exit 2 unless a pause copies 20+ characters of the proposal's or a governance file's words."""
    paths = [folder / "proposal.md"]
    paths += [p for g in loaded.get("review.governance") or [] for p in sorted(root.glob(g))]
    held = " ".join(" ".join(p.read_text(encoding="utf-8").split()) for p in paths if p.is_file())
    cites = " ".join(str(asked.get("cites") or "").split())
    if len(cites) < 20 or cites not in held:
        _refuse("pause: no such words in proposal.md or a governance file. A case the approved "
                "proposal does not answer is the loop's to decide: return it under `fix needed` "
                "with the answer to build")


def _active_paused(folder: Path, change: str, asked: dict[str, Any]) -> None:
    """Exit 2 when a round's `active` is above the PR's distinct paused threads."""
    from . import record  # noqa: PLC0415 - record imports this module
    paused = {r["thread"] for r in record.read(folder)
              if r["kind"] == "pause" and r["pr"] == asked.get("pr")}
    if isinstance(asked.get("active"), int) and asked["active"] > len(paused):
        _refuse(f"active={asked['active']} but {len(paused)} paused: a thread stays active only "
                f"behind `dl record {change} pause`")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603, S607 - git, with a checked hex anchor
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False)


def _diff(root: Path, spec: list[str], *revs: str) -> list[tuple[str, str]] | None:
    """Each (sign, stripped line) the diff of `revs` adds or drops; None: git cannot."""
    done = _git(root, "diff", "--no-renames", "-U0", *revs, "--", *spec)
    if done.returncode != 0:
        return None
    return [(x[0], x[1:].strip()) for x in done.stdout.splitlines()
            if x[:1] in ("+", "-") and x[:3] not in ("+++", "---")]


def _origin(root: Path, loaded: Any, first: str | None, anchor: str) -> str | None:  # noqa: ANN401
    """What the PR started from: its first reviewed head, else its merge-base with integration."""
    if first:
        return first if SHA.fullmatch(first) else None
    ref = f"{loaded.get('branches.remote')}/{loaded.get('branches.integration')}"
    base = _git(root, "merge-base", ref, anchor)
    return base.stdout.strip() if base.returncode == 0 else None


def _own(root: Path, origin: str | None, anchor: str, spec: list[str]) -> set[str]:
    """The lines the fix rounds added before `anchor`; none when `origin` is unknown (strict)."""
    since = _diff(root, spec, origin, anchor) if origin else None
    return {text for sign, text in since or [] if sign == "+"}


def _fix_tested(
    root: Path, loaded: Any, asked: dict[str, Any], heads: tuple[str | None, str]  # noqa: ANN401
) -> None:
    """Exit 2 when the fix since `heads[1]` cut an assertion, or changed source, no test added.

    `heads` is the PR's first reviewed head (None: no review-run record) and the round's anchor.
    No review-run record: round 1's anchor is the review's base; only the source-fix check runs.
    """
    first, anchor = heads
    # ponytail: word checks on the diff. They catch a cut assertion and a source fix with nothing
    # test-like added, never a weak test; `dl ci fix-proves` proves a test.
    globs = list(loaded.get("size.production") or [])
    spec = [".", f":(exclude){loaded.get('paths.changes')}", ":(exclude)*.md",
            *(f":(exclude,glob){g}" for g in globs)]
    rest = _diff(root, spec, anchor)
    src = _diff(root, [f":(glob){g}" for g in globs], anchor) if globs else []
    if rest is None or src is None:
        print(f"dl record: note: cannot read the fix since {anchor[:12]}; its tests were not "
              "checked", file=sys.stderr)
        return
    added = {text for sign, text in rest if sign == "+"}
    gone = [t for sign, t in rest if sign == "-" and CUT.search(t) and t not in added]
    gone = [] if first is None and asked.get("round") == 1 else gone
    origin = _origin(root, loaded, first, anchor) if gone else None
    if gone and (gone := [t for t in gone if t not in _own(root, origin, anchor, spec)]):
        _refuse(f"the fix cut an assertion ({gone[0][:60]}): an assertion the PR had when first "
                "reviewed, or on the integration branch, is only added to; restore it, or leave "
                "the thread active and report the loop paused")
    tested = added or any("test" in t.lower() for sign, t in src if sign == "+")
    if asked.get("fixed") and src and not tested:
        _refuse(f"fixed={asked['fixed']}: source changed since {anchor[:12]} and no test was "
                "added: each fixed blocker or major has a test that pins it")


def _reviewed(root: Path, loaded: Any, change: str, asked: dict[str, Any]) -> list[dict[str, Any]]:  # noqa: ANN401
    """The local host's review-runs behind a round (its PR's) or a lesson (the change's).

    Exit 2 when there is none or the last is not clean. Another host, or the subagent
    fallback, has no such record: [].
    """
    if loaded.get("host.provider") != "local" or loaded.get("review.fallback") == "subagent":
        return []
    from ..hosts import local  # noqa: PLC0415 - only the local host has review-run records
    folder = root / str(loaded.get("paths.changes")) / change
    runs = [r for r in local.records(folder)
            if r.get("kind") == "review-run" and asked.get("pr") in (None, r.get("pr"))]
    if not runs or runs[-1].get("rc") != 0:
        _refuse(f"no clean review-run for {asked.get('pr')}" if asked["kind"] == "round" else
                f"no clean review-run on {change}: a lesson is one fixed review finding; a build "
                "note goes in the change's design.md")
    return runs


def checked(change: str, asked: dict[str, Any]) -> dict[str, Any]:
    """Return `asked`, or exit 2: no clean review-run behind a round or lesson, an untested fix."""
    root = project_root()
    loaded = load(root)
    if asked.get("kind") == "lessons-logged":
        _lessons_counted(root, loaded, change, asked)
    folder = root / str(loaded.get("paths.changes")) / change
    if asked.get("kind") == "pause":
        _pause_cited(root, loaded, folder, asked)
    if asked.get("kind") == "round" and loaded.get("host.provider") == "local":
        _active_paused(folder, change, asked)
    if asked.get("kind") not in ("round", "lesson"):
        return asked
    runs = _reviewed(root, loaded, change, asked)
    anchor = str(runs[-1]["head"] if runs else asked.get("base"))
    if asked["kind"] == "round" and SHA.fullmatch(anchor):
        _fix_tested(root, loaded, asked, (str(runs[0]["head"]) if runs else None, anchor))
    return asked


def fast_track(root: Path) -> bool:
    """`review.fast_track` from the integration branch's config (a PR cannot relax its own done
    rule), else the working tree's, as the size cap; read once until the config file or the
    integration branch's head changes."""
    path = root / PROJECT
    stamp = path.stat().st_mtime_ns if path.exists() else 0
    head = _git(root, "rev-parse", "--verify", "-q", _ref(root, stamp)).stdout.strip()
    return _fast_track(root, stamp, head)


@functools.lru_cache(maxsize=8)
def _ref(root: Path, _stamp: int) -> str:
    loaded = load(root)
    return f"{loaded.get('branches.remote')}/{loaded.get('branches.integration')}"


@functools.lru_cache(maxsize=8)
def _fast_track(root: Path, _stamp: int, _head: str) -> bool:
    loaded = load(root)
    ref = f"{loaded.get('branches.remote')}/{loaded.get('branches.integration')}"
    held = _git(root, "show", f"{ref}:./{PROJECT}")
    if held.returncode == 0:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / PROJECT).write_text(held.stdout, encoding="utf-8")
            theirs = load(Path(tmp))
        loaded = loaded if theirs.error else theirs
    return loaded.get("review.fast_track") is True


def review_gaps(rounds: Records, tasks: Records, debt: Records | None = None) -> list[str]:
    """The done rule's review gaps: a clean last round per part's PR, a reviewed PR per part.
    `debt` (fast track, no recheck): each blocker and major fixed or debt of its severity."""
    parts = len({t["part"] for t in tasks if "part" in t and t["status"] != "todo"}) or 1
    # ponytail: PRs are counted, not matched to parts; a part's PR raised twice counts twice
    prs = list(dict.fromkeys(r["pr"] for r in reversed(rounds)))[:parts]
    dirty = False
    for pr in prs:
        own = [r for r in rounds if r["pr"] == pr]
        carried = own[-2]["active"] if len(own) > 1 else 0  # this round rechecked those fixes
        last = own[-1]
        held = list({r["thread"]: r.get("severity") for r in debt or [] if r["pr"] == pr}.values())
        covered = last["fixed"] + sum(min(last[s], held.count(s)) for s in ("blocker", "major"))
        owed = last["blocker"] + last["major"] - (covered if debt is not None else 0)
        dirty |= bool(owed > 0 or last["active"] or debt is None and last["fixed"] > carried)
    gaps = ["a clean last review round"] if dirty else []
    short = f"a reviewed PR for each started part ({parts} started, {len(prs)} reviewed)"
    return gaps + ([short] if len(prs) < parts else [])
