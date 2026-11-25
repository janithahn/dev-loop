#!/usr/bin/env sh
# Self-check for hooks/done-guard.sh, which reads each change's records.jsonl through
# `dl record done` (tests/test_record.py holds the full checks). A missing verify, a dirty last
# round, the clean case and the scope rules stay pinned. The project sets a non-default
# `paths.changes` and `branches.integration`, so both keys are read, never assumed.
# Run: sh tests/test-done-guard.sh (tests/test_edit_hooks.py runs it).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$HERE/../hooks/done-guard.sh"
DL="$HERE/../bin/dl"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export CLAUDE_PROJECT_DIR="$T"
unset DONE_GUARD_BASE
printf 'version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\nbranches:\n  integration: trunk\n  remote: hub\n' \
  > "$T/dev-loop.yaml"
C="$T/work/log/x"
mkdir -p "$C"
printf -- '- [x] T1 done\n' > "$C/tasks.md"
run() { (cd "$T" && sh "$HOOK" </dev/null 2>&1); }  # the report is on stderr, exit 2
put() { (cd "$T" && bash "$DL" record x "$@" >/dev/null); }
put round pr=1 round=1 base=full blocker=0 major=0 minor=1 fixed=0 deferred=1 active=0 turns=4 seconds=200 tokens_in=1000 tokens_out=100 unmetered=0
put metrics pr=1 change=x rounds=1 per_round=0/0/1 total=0/0/1 lessons=0
put lessons-logged count=0
put simplifier edits=none
put skills skills=tdd
run | grep -q "a green verify" || { echo "FAIL: missing verify not named"; exit 1; }
# A verify head git cannot resolve (or no git at all) is stale, so pin a real commit
command -v git >/dev/null || { echo "FAIL: the verify cases need git"; exit 1; }
git -C "$T" init -q -b trunk
git -C "$T" -c user.name=t -c user.email=t@t commit -q --allow-empty -m start
put verify result=green head="$(git -C "$T" rev-parse HEAD)"
run | grep -q "verify-report.md" || { echo "FAIL: missing verify-report.md not named"; exit 1; }
touch "$C/verify-report.md"
[ -z "$(run)" ] || { echo "FAIL: a finished change flagged: $(run)"; exit 1; }
put round pr=1 round=2 base=abc blocker=0 major=1 minor=0 fixed=0 deferred=0 active=1 turns=4 seconds=200 tokens_in=1000 tokens_out=100 unmetered=0
run | grep -q "a clean last review round" || { echo "FAIL: dirty last round not named"; exit 1; }
# In a checkout, only the changes it touches since origin/<branches.integration> are named.
command -v git >/dev/null || { echo "FAIL: the scope cases need git"; exit 1; }
g() { git -C "$T" -c user.name=t -c user.email=t@t "$@"; }
g init -q -b trunk && g add -A && g commit -qm base && g update-ref refs/remotes/hub/trunk HEAD
[ -z "$(run)" ] || { echo "FAIL: an untouched old change named"; exit 1; }
put skills skills=verify
run | grep -q '\] x:' || { echo "FAIL: a change edited in this checkout not named"; exit 1; }
g checkout -q -- . && mkdir "$T/work/log/y"
printf -- '- [x] T1 done\n' > "$T/work/log/y/tasks.md"
out="$(run)" || true  # exit 2 when it names something
printf '%s' "$out" | grep -q '\] y:' || { echo "FAIL: a new change not named"; exit 1; }
! printf '%s' "$out" | grep -q '\] x:' || { echo "FAIL: the old change named beside it"; exit 1; }
# An edit committed on the branch with a clean working tree is still named (base..HEAD).
g checkout -q -b feat
put skills skills=verify
g add -A && g commit -qm "edit x, add y"
[ -z "$(git -C "$T" status --porcelain)" ] || { echo "FAIL: tree not clean for the committed case"; exit 1; }
run | grep -q '\] x:' || { echo "FAIL: a change committed on the branch (clean tree) not named"; exit 1; }
# With no reachable base, fall back to the uncommitted and untracked folders, not every change.
export DONE_GUARD_BASE=no-such-ref
put skills skills=simplify
out="$(run)" || true
printf '%s' "$out" | grep -q '\] x:' || { echo "FAIL: no-base fallback did not name the dirty change"; exit 1; }
! printf '%s' "$out" | grep -q '\] y:' || { echo "FAIL: no-base fallback named a clean change"; exit 1; }
unset DONE_GUARD_BASE
# A git command that errors is a tool failure: exit 0 with a line on stderr, nothing named.
mkdir "$T/bin"
printf '#!/bin/sh\ncase " $* " in *" diff "*) exit 1;; esac\nexec "%s" "$@"\n' "$(command -v git)" > "$T/bin/git"
chmod +x "$T/bin/git"
out="$(PATH="$T/bin:$PATH" run)" && rc=0 || rc=$?
[ "$rc" = 0 ] || { echo "FAIL: a git failure held the Stop (exit $rc)"; exit 1; }
printf '%s' "$out" | grep -q 'skipped' || { echo "FAIL: no stderr line on a git failure"; exit 1; }
! printf '%s' "$out" | grep -q '\] x:' || { echo "FAIL: a git failure still named a change"; exit 1; }
# Scope names with no change folder (the archive folder, a change's old name after `git mv`, a
# root file) are dropped, so they never turn the check into a silent skip. A fresh project.
B="$T"; trap 'rm -rf "$B"' EXIT; T="$B/p2"; mkdir -p "$T/work/log/x" "$T/work/log/y"
export CLAUDE_PROJECT_DIR="$T"
cp "$B/dev-loop.yaml" "$T/"
printf -- '- [x] T1 done\n' > "$T/work/log/x/tasks.md"
printf -- '- [ ] T1 open\n' > "$T/work/log/y/tasks.md"
g init -q -b trunk && g add -A && g commit -qm base && g update-ref refs/remotes/hub/trunk HEAD
g checkout -q -b feat && mkdir -p "$T/work/log/archive" && g mv work/log/x work/log/archive/x
printf -- '- [x] T1 done\n' > "$T/work/log/y/tasks.md"
g add -A && g commit -qm "archive x, finish y"
out="$(run)" && rc=0 || rc=$?
[ "$rc" = 2 ] || { echo "FAIL: archiving x let finished y pass (exit $rc): $out"; exit 1; }
printf '%s' "$out" | grep -q '\] y:.*a green verify' || { echo "FAIL: y not named: $out"; exit 1; }
g update-ref refs/remotes/hub/trunk HEAD && touch "$T/work/log/migrated-changes.txt"
out="$(run)" && rc=0 || rc=$?
[ "$rc" = 0 ] && [ -z "$out" ] || { echo "FAIL: a root file alone (exit $rc): $out"; exit 1; }
# v422h: record.py's change_folders alone judges what a change folder is, so no spelling of
# paths.changes or paths.archive lets finished y (no verify) pass. A fresh project per case.
fresh() {  # <label> <paths.changes> <paths.archive>: x and y, base committed on hub/trunk
  T="$B/$1"; mkdir -p "$T/work/log/x" "$T/work/log/y"; export CLAUDE_PROJECT_DIR="$T"
  printf 'version: 1\npaths:\n  changes: %s\n  archive: %s\n' "$2" "$3" > "$T/dev-loop.yaml"
  printf 'branches:\n  integration: trunk\n  remote: hub\n' >> "$T/dev-loop.yaml"
  printf -- '- [x] T1 done\n' > "$T/work/log/x/tasks.md"
  printf -- '- [ ] T1 open\n' > "$T/work/log/y/tasks.md"
  g init -q -b trunk && g add -A && g commit -qm base && g update-ref refs/remotes/hub/trunk HEAD
}
spelt() {  # <label> <paths.changes> <paths.archive>: archive x with git mv, finish y
  fresh "$@"
  g checkout -q -b feat && mkdir -p "$T/work/log/archive" && g mv work/log/x work/log/archive/x
  printf -- '- [x] T1 done\n' > "$T/work/log/y/tasks.md"
  g add -A && g commit -qm "archive x, finish y"
  out="$(run)" && rc=0 || rc=$?
  [ "$rc" = 2 ] || { echo "FAIL: $1: finished y passed (exit $rc): $out"; exit 1; }
  printf '%s' "$out" | grep -q '\] y:.*a green verify' \
    || { echo "FAIL: $1: y not named: $out"; exit 1; }
}
spelt dot-archive work/log ./work/log/archive
spelt abs-archive work/log "$B/abs-archive/work/log/archive"
spelt slash-changes work/log/ work/log/archive
spelt dot-changes ./work/log work/log/archive
# A hand-made untracked folder whose name is a glob (`fi[x]`, beside a root file `fix` it would
# match) is named, never expanded away or dropped.
fresh glob work/log work/log/archive
touch "$T/fix" && mkdir "$T/work/log/fi[x]"
printf -- '- [x] T1 done\n' > "$T/work/log/fi[x]/tasks.md"
out="$(run)" && rc=0 || rc=$?
[ "$rc" = 2 ] || { echo "FAIL: untracked fi[x] passed (exit $rc): $out"; exit 1; }
printf '%s' "$out" | grep -qF '] fi[x]:' || { echo "FAIL: fi[x] not named: $out"; exit 1; }
# v422i: the shell maps no path to a name, so no platform's `realpath`, no quoted path and no
# folder spelt as an argument lets finished y (no verify, committed on the branch) pass.
finish_y() {  # <label>: a fresh project, y finished on branch feat
  fresh "$1" work/log work/log/archive
  g checkout -q -b feat && printf -- '- [x] T1 done\n' > "$T/work/log/y/tasks.md"
  g commit -qam "finish y"
}
held() {  # <label> <name>: the hook exits 2 and names <name>
  out="$(run)" && rc=0 || rc=$?
  [ "$rc" = 2 ] || { echo "FAIL: $1: passed (exit $rc): $out"; exit 1; }
  printf '%s' "$out" | grep -qF "] $2:" || { echo "FAIL: $1: $2 not named: $out"; exit 1; }
}
shim() {  # <label> <body>: a `realpath` first on PATH that runs <body>
  mkdir -p "$B/$1-bin" && printf '#!/bin/sh\n%s\n' "$2" > "$B/$1-bin/realpath"
  chmod +x "$B/$1-bin/realpath"
}
finish_y abs-realpath  # Git Bash: --relative-to=C:/... returns an absolute /c/... path
shim abs-realpath 'echo /c/elsewhere/work/log'
(PATH="$B/abs-realpath-bin:$PATH"; held abs-realpath y)
finish_y old-realpath  # macOS and BusyBox: no -m, no --relative-to
shim old-realpath 'echo "realpath: illegal option -- m" >&2; exit 1'
(PATH="$B/old-realpath-bin:$PATH"; held old-realpath y)
fresh backslash work/log work/log/archive  # git quotes it even with core.quotePath=false
mkdir "$T/work/log/a\\b" && printf -- '- [x] T1 done\n' > "$T/work/log/a\\b/tasks.md"
held backslash 'a\b'
finish_y part-folder  # a folder spelt as an argument is never parsed as one
mkdir "$T/work/log/part=z" && touch "$T/work/log/part=z/notes.md"
held part-folder y
# v422j: the project in a subfolder of its repo with `diff.relative` set: git's paths stay
# top-relative, so finished y (no verify, committed on the branch) is still named.
T="$B/repo/proj"; mkdir -p "$T/work/log/y"; export CLAUDE_PROJECT_DIR="$T"
printf 'version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\n' > "$T/dev-loop.yaml"
printf 'branches:\n  integration: trunk\n  remote: hub\n' >> "$T/dev-loop.yaml"
printf -- '- [ ] T1 open\n' > "$T/work/log/y/tasks.md"
git -C "$B/repo" init -q -b trunk
g add -A && g commit -qm base && g update-ref refs/remotes/hub/trunk HEAD
g config diff.relative true
g checkout -q -b feat && printf -- '- [x] T1 done\n' > "$T/work/log/y/tasks.md"
g commit -qam "finish y"
held subfolder-relative y
echo "done-guard: ok"
