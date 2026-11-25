"""`dl ci <step>`: the CI steps; each exits with the code of the step it wraps.

    dl ci size-cap [--base REF]              dl ci records-current [--base REF]   # dl records current
    dl ci rules-current [--base REF]     # dl records rules
    dl ci unparsed [--base REF]          # dl records unparsed
    dl ci verdict-selftest               # the verdict contract accepts a good reply, refuses a bad
    dl ci hooks-selftest                 # every registered hook runs on a sample event, no crash
    dl ci pin-check [--tag vX.Y.Z]       # every pin (settings, marketplace ref, CI tag) = plugin
    dl ci fix-proves [--base REF]        # fix_proves.sh REF: a changed test fails on REF's source
    dl ci secret-scan                    # token shapes in tracked files, by path:line, not shown
    dl ci licences                       # each direct dependency's licence vs licences.allow
    dl ci done-rule [CHANGE...] [--base REF]  # the done rule; no CHANGE: those REF..HEAD touches
    dl ci release-gate                   # each open debt item; red on release_gate.block_on
    dl ci flakes --junit FILE... [--build ID] [--no-fail-on-flake]  # log flakes, red by default
    dl ci additive-tests [--base REF]    # no test, assert or check of REF's test files is gone
`flakes` is not a gate step the CI template runs: it reads test reports, which a diff-only gate has
not produced. Nor is `additive-tests`: a project names it in `verify.steps` and `verify.prepush`. REF defaults to PREPUSH_BASE (set by `dl pr` for its push), else
the parent `dl pr --base` remembered for the branch,
else <branches.remote>/<branches.integration>. The plugin's files
(bin/dl, hooks, plugin.json) are found at CLAUDE_PLUGIN_ROOT, else this source tree. A step that
cannot run exits 1, never 0.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dev_loop import config, doctor
from dev_loop.ci import licences, secret_scan
from dev_loop.ci.size import plugin_root, script_env, size_cap, stack_base

RECORDS = {"records-current": "current", "rules-current": "rules", "unparsed": "unparsed"}
STEPS = ("size-cap", *RECORDS, "verdict-selftest", "hooks-selftest", "pin-check",
         "fix-proves", "secret-scan", "licences", "done-rule", "release-gate")
CHOICES = (*STEPS, "flakes", "additive-tests")  # subcommands, not gates the CI template runs
SPEC = "## Done when\n- the first line\n- the second\n  continued\n## Next\n- not a line\n"
GOOD = {"verdict": "APPROVE", "summary": "", "findings": [], "spec": [
    {"id": "S1", "status": "met", "evidence": "a test"},
    {"id": "S2", "status": "not-applicable", "evidence": "no code"},
]}
BAD = {**GOOD, "spec": [{**GOOD["spec"][0], "status": "failed"}, GOOD["spec"][1]]}


PR_BRANCHES = {  # what fix_proves.sh reads: where a CI host names the PR's branch
    "FIX_PROVES_BRANCH": ("GITHUB_HEAD_REF", "SYSTEM_PULLREQUEST_SOURCEBRANCH"),
    "FIX_PROVES_TARGET": ("GITHUB_BASE_REF", "SYSTEM_PULLREQUEST_TARGETBRANCH"),
}


def fix_proves(root: Path, base: str) -> int:
    script = Path(__file__).with_name("fix_proves.sh")
    env = script_env()
    for key, hosts in PR_BRANCHES.items():
        if value := next((env[name] for name in (key, *hosts) if env.get(name)), ""):
            env[key] = value
    return subprocess.run(["bash", str(script), base], cwd=root,  # noqa: S607
                          env=env, check=False).returncode


def verdict_selftest() -> int:
    from dev_loop.review import review_verdict  # noqa: PLC0415 - only this step needs it

    faults = []
    if review_verdict.spec_lines(SPEC) != ["the first line", "the second continued"]:
        faults.append("lines: the done-when section was misread")
    if review_verdict.errors(GOOD, 2):
        faults.append("check: a good reply was refused")
    if not review_verdict.errors(BAD, 2):
        faults.append("check: a failed tick with no major filed was accepted")
    print("\n".join(faults) or "verdict contract: green")
    return 1 if faults else 0


def hooks_selftest(root: Path) -> int:
    plugin = plugin_root()
    try:
        events = json.loads((plugin / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    except (OSError, ValueError, KeyError) as error:
        print(f"hooks: not run, hooks.json unreadable under {plugin}: {error}", file=sys.stderr)
        return 1
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(plugin), "CLAUDE_PROJECT_DIR": str(root)}
    failed = 0
    for event, groups in events.items():
        for group in groups:
            tool = (group.get("matcher") or "Read").split("|")[0]
            payload = json.dumps({
                "session_id": "selftest", "transcript_path": "", "cwd": str(root),
                "hook_event_name": event, "tool_name": tool,
                "tool_input": {"file_path": str(root / "README.md"), "command": "true"},
            })
            for hook in group["hooks"]:
                if "/hooks/" not in hook["command"]:
                    continue  # the venv bootstrap is not a hook under test
                code = subprocess.run(hook["command"], shell=True, input=payload, cwd=root,  # noqa: S602
                                      env=env, capture_output=True, text=True,
                                      check=False).returncode
                failed += code not in (0, 2)  # 2 is the hook's answer; anything else a crash
                print(f"{event} {tool}: exit {code}  {hook['command']}")
    return 1 if failed else 0


def pin_check(root: Path, tag: str | None) -> int:
    try:
        have = doctor._plugin_version()  # noqa: SLF001 - one reader of plugin.json
    except (OSError, ValueError):
        print("pin check: not run, plugin.json is unreadable", file=sys.stderr)
        return 1
    try:
        body = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        body = {}
    market = body.get("extraKnownMarketplaces", {}).get("dev-loop", {}).get("source", {})
    pins = {"settings pluginPins": body.get("pluginPins", {}).get("dev-loop"),
            "marketplace ref": market.get("ref"), "CI tag": tag}
    found = {where: pin.removeprefix("v") for where, pin in pins.items() if pin}
    if not found:
        print(f"pin check: not run, no pin found for plugin {have}", file=sys.stderr)
        return 1
    wrong = {where: pin for where, pin in found.items() if pin != have}
    for where, pin in found.items():
        print(f"{where}: {pin} {'!=' if where in wrong else '=='} plugin {have}")
    return 1 if wrong else 0


def touched(root: Path, base: str) -> list[str] | None:
    """Return the change folders base..HEAD touches; None when git cannot say."""
    changes = str(config.load(root).get("paths.changes"))
    diff = subprocess.run(["git", "diff", "--name-only", base, "HEAD", "--", changes],  # noqa: S607
                          cwd=root, capture_output=True, text=True, check=False)
    if diff.returncode:
        print(f"done rule: not run, git diff {base} failed: {diff.stderr.strip()}", file=sys.stderr)
        return None
    from dev_loop.records import record  # noqa: PLC0415 - only this step needs it

    names = {Path(line).relative_to(changes).parts[0] for line in diff.stdout.splitlines()}
    return sorted(names & {folder.name for folder in record.change_folders(root)})


def done_rule(root: Path, names: list[str]) -> int:
    """The done guard's rule (`record.missing`) on each change whose task boxes are all ticked."""
    from dev_loop.records import record  # noqa: PLC0415 - only this step needs it

    folders = {folder.name: folder for folder in record.change_folders(root)}
    failed = 0
    if not names:
        print("done rule: not run: no change judged")
        return 1
    for name in names:
        if name not in folders:
            print(f"{name}: not run, no such change")
            failed = 1
        elif not (folders[name] / "tasks.md").exists():
            print(f"{name}: not run: no tasks.md")
            failed = 1
        elif not record.tasks_done(folders[name]):
            print(f"{name}: tasks open; the done rule holds once every box is ticked")
        elif gaps := record.missing(folders[name]):
            print(f"{name}: missing {'; '.join(gaps)}")
            failed = 1
        else:
            print(f"{name}: done")
    return failed


def release_gate(root: Path) -> int:
    """List every open debt item across the changes with its severity; red on a blocking one."""
    from dev_loop.records import record  # noqa: PLC0415 - only this step needs it

    loaded = config.load(root)
    if loaded.error:
        print(f"release gate: not run, {loaded.error}", file=sys.stderr)
        return 1
    block_on = loaded.get("release_gate.block_on")
    folders = record.change_folders(root)
    bad = 0
    for folder in folders:
        for number, line in enumerate(record.text_of(folder / "records.jsonl").splitlines(), 1):
            try:
                json.loads(line)
            except ValueError:
                print(f"{folder.name}/records.jsonl:{number}: unreadable")
                bad += 1
    debt = [(folder.name, item) for folder in folders
            for item in record.read(folder) if item["kind"] == "debt" and not item.get("closed")]
    if not debt:
        print("no open debt")
        return 1 if bad else 0
    for name, item in debt:
        print(f"{name}: {record.severity(item)} PR {item['pr']} thread {item['thread']} "
              f"{item['file']}: {item['finding']}")
    blocking = sum(record.severity(item) in block_on for _, item in debt)
    print(f"{blocking} of {len(debt)} open debt items block the release "
          f"(block_on: {', '.join(block_on)})")
    return 1 if blocking or bad else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dl ci", description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=CHOICES)
    parser.add_argument("change", nargs="*", help="done-rule: the changes to judge")
    parser.add_argument("--base", help="default the remembered parent, else the integration")
    parser.add_argument("--tag", help="pin-check: the tag the CI installed")
    parser.add_argument("--junit", nargs="+", help="flakes: the JUnit XML reports to read")
    parser.add_argument("--build", help="flakes: the build id a flake is recorded against")
    parser.add_argument("--no-fail-on-flake", action="store_true", help="flakes: stay green")
    args = parser.parse_args(argv)
    if args.change and args.step != "done-rule":
        parser.error(f"{args.step} takes no change")
    root = config.project_root()
    if args.step == "flakes":
        from dev_loop.records import flakes  # noqa: PLC0415 - only this step needs it

        if not args.junit:
            parser.error("flakes needs --junit FILE...")
        return flakes.run(root, args.junit, args.build, fail_on_flake=not args.no_fail_on_flake)
    if args.step == "secret-scan":
        return secret_scan.scan(root)
    if args.step == "licences":
        return licences.check(root)
    if args.step == "release-gate":
        return release_gate(root)
    branch = config.load(root).values["branches"]
    own = f"{branch['remote']}/{branch['integration']}"
    base = args.base or os.environ.get("PREPUSH_BASE") or stack_base(root) or own
    if args.step in RECORDS:
        from dev_loop.records import review_records  # noqa: PLC0415 - only these steps need it

        return int(review_records.main([RECORDS[args.step], "--base", base]))
    if args.step == "done-rule":
        names = args.change or touched(root, base)
        return 1 if names is None else done_rule(root, names)
    if args.step == "additive-tests":
        from dev_loop.ci import additive  # noqa: PLC0415 - only this step needs it

        return additive.check(root, base)
    if args.step == "size-cap":
        return size_cap(root, base)
    if args.step == "fix-proves":
        return fix_proves(root, base)
    if args.step == "verdict-selftest":
        return verdict_selftest()
    if args.step == "hooks-selftest":
        return hooks_selftest(root)
    return pin_check(root, args.tag)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
