#!/usr/bin/env bash
# fix_proves.sh <base>: prove a fix's test. The test files the branch added or changed since
# <base> (matching a `fix_proves.tests` glob) run against the base's source (the files matching a
# `fix_proves.src` glob, taken from <base>), so a test that would still pass with the bug is caught.
# Run from the project root; `dl ci fix-proves` runs it with <base> origin/<branches.integration>.
# The config is read there; every path (the globs, <paths.changes>) is then read from git's top
# level, where the test command also runs, so a project below the top names its paths from the top.
# The copy is the head's tree with the base's source and the head's changed tests, under the
# project root (a runner such as a container that mounts the root sees it). In it runs
#   <verify.runner> <fix_proves.command> <changed test files...>
# Only a fix PR is proved: a `fix/` branch (`FIX_PROVES_BRANCH`, else the current one), or a
# changed <paths.changes>/*/status.md holding `fixes-defect`; and only one aimed at the integration
# branch (`FIX_PROVES_TARGET`, the PR's target when a CI sets it). Else: "not a fix PR", exit 0.
# `dl ci fix-proves` fills both from the host's PR variables (GITHUB_HEAD_REF and GITHUB_BASE_REF,
# SYSTEM_PULLREQUEST_SOURCEBRANCH and _TARGETBRANCH) when unset; with no branch: "not run", exit 1.
# The command must write a junit report: at `{report}` where the command names that placeholder,
# else `--junitxml=.fix-proves-junit.xml` is added (pytest). The report with the command's exit code
# is the verdict, never a bare exit code:
#   exit 5: skipped (exit 0), the changed files hold no selected test;
#   no test in the report: exit 3, could not run (126 and 127 too: could not start);
#   a test failed on an assertion, or a changed test cannot import on the base (a collection
#   error: it needs code the base lacks): proved (exit 0);
#   any other error (a fixture, the environment): exit 3, nothing proved;
#   every test passes: FAILED (exit 1, naming the tests).
# An empty `fix_proves.src` or `fix_proves.tests` or a null command: "not run", exit 1. Every key
# is read through `dl config get`; an unreadable config stops the gate (exit 1).
# Tests: tests/test_fix_proves.py.
set -euo pipefail
dl="${DL:-$(cd "$(dirname "$0")/../../.." && pwd)/bin/dl}"
base=${1:?usage: fix_proves.sh <base>}
get() {  # one config value (a list: one item a line); exit 1 naming the key when unreadable
  bash "$dl" config get "$1" && return
  echo "fix-proves: cannot read $1 through $dl" >&2
  exit 1
}
src=$(get fix_proves.src) || exit 1
tests=$(get fix_proves.tests) || exit 1
command=$(get fix_proves.command) || exit 1
runner=$(get verify.runner) || exit 1
integration=$(get branches.integration) || exit 1
changes=$(get paths.changes) || exit 1
if [[ -z $src || -z $tests || -z $command ]]; then
  echo "fix-proves: not run: fix_proves not configured"
  exit 1
fi
matches() {  # matches <path> <globs, one a line>; a glob's `*` crosses folders
  local glob
  while IFS= read -r glob; do
    [ -n "$glob" ] || continue
    # shellcheck disable=SC2053 # the glob is a pattern on purpose
    if [[ $1 == $glob ]] || { [[ $glob == '**/'* ]] && [[ $1 == ${glob#'**/'} ]]; }; then return 0; fi
  done <<<"$2"
  return 1
}
pick() {  # pick <globs>: the paths on stdin that match one
  local path
  while IFS= read -r path; do matches "$path" "$1" && echo "$path"; done
  return 0
}

root=$(git rev-parse --show-toplevel)
cd "$root"  # git names every changed path from here
target=${FIX_PROVES_TARGET:-$integration}
target=${target#refs/heads/}
if [[ $target != "$integration" ]]; then
  echo "fix-proves: skipped, the PR targets $target, not $integration"
  exit 0
fi
branch=${FIX_PROVES_BRANCH:-$(git branch --show-current)}
branch=${branch#refs/heads/}
if [[ -z $branch ]]; then  # a detached HEAD and no branch from the host: a fix PR cannot be told
  echo "fix-proves: not run, cannot tell the branch (a detached HEAD; set FIX_PROVES_BRANCH)"
  exit 1
fi
if ! diffed=$(git diff --name-only --diff-filter=AM "$base...HEAD"); then
  echo "fix-proves: not run, git diff failed ($base...HEAD)"
  exit 1
fi
marked=""
while IFS= read -r status; do
  grep -q 'fixes-defect' "$status" 2>/dev/null && marked=$status
done < <(pick "$changes/*/status.md" <<<"$diffed")
if [[ $branch != fix/* && -z $marked ]]; then
  echo "fix-proves: skipped, not a fix PR (branch $branch, no fixes-defect marker)"
  exit 0
fi

mapfile -t changed < <(pick "$tests" <<<"$diffed")
if [[ ${#changed[@]} -eq 0 ]]; then
  echo "fix-proves: skipped, no changed file matches fix_proves.tests since $base"
  exit 0
fi
py=${DL_PYTHON:-python3}  # reads the junit report
if ! command -v "$py" >/dev/null; then
  echo "fix-proves: not run, $py not found (it reads the junit report)"
  exit 1
fi
copy="$root/.fix-proves-base-$$"  # a fresh name each run, removed on exit
rm -rf "$copy" && mkdir "$copy"
trap 'rm -rf "$copy"' EXIT
git archive HEAD | tar -x -C "$copy"
git ls-tree -r --name-only HEAD | pick "$src" | while IFS= read -r path; do
  rm -f "$copy/$path"
done
mapfile -t base_src < <(git ls-tree -r --name-only "$base" | pick "$src")
if [[ ${#base_src[@]} -gt 0 ]]; then git archive "$base" -- "${base_src[@]}" | tar -x -C "$copy"; fi
git archive HEAD -- "${changed[@]}" | tar -x -C "$copy"  # the head's tests, over any src glob

echo "fix-proves: ${#changed[@]} changed test file(s) against $base's source"
set +e
report=.fix-proves-junit.xml
if [[ $command == *'{report}'* ]]; then
  command=${command//'{report}'/$report}
else
  command="$command --junitxml=$report"
fi
rm -f "$copy/$report"  # one the tree holds is an earlier run's, never this one's
(cd "$copy" && bash -c "$runner $command \"\$@\"" fix-proves "${changed[@]}")
code=$?
set -e
if [[ $code -eq 126 || $code -eq 127 ]]; then
  echo "fix-proves: could not run the test command (exit $code)"
  exit 3
fi
# the report's counts: "<tests> <assertion failures> <errors> <collection errors>"
counts=$("$py" - "$copy/$report" <<'PY' 2>/dev/null || echo "0 0 0 0"
import sys
import xml.etree.ElementTree as ET
cases = list(ET.parse(sys.argv[1]).getroot().iter("testcase"))
errors = [c.find("error") for c in cases if c.find("error") is not None]
print(len(cases), sum(c.find("failure") is not None for c in cases), len(errors),
      sum(e.get("message") == "collection failure" for e in errors))
PY
)
read -r ran failed errored collected <<<"$counts"
if [[ $code -eq 5 ]]; then
  echo "fix-proves: skipped, the changed files hold no selected test"
  exit 0
fi
if [[ $ran -eq 0 ]]; then
  echo "fix-proves: could not run the test command (exit $code, no test ran);" \
    "the command must write a junit report (pytest: --junitxml; any other: at {report})"
  exit 3
fi
if [[ $failed -gt 0 ]]; then
  echo "fix-proves: passed, a changed test fails on the base ($failed of $ran failed)"
  exit 0
fi
if [[ $errored -gt 0 ]]; then
  if [[ $collected -gt 0 ]]; then
    echo "fix-proves: passed, a changed test cannot import on the base (it needs code it lacks)"
    exit 0
  fi
  echo "fix-proves: not proved: $errored of $ran test(s) errored before their body ran"
  exit 3
fi
echo "fix-proves: FAILED, every changed test passes on the base: none proves the fix:"
printf '  %s\n' "${changed[@]}"
exit 1
