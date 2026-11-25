#!/usr/bin/env bash
# The pipeline's cheap steps, run before a push (`dl githook pre-push`). Diffs the merge base with
# the base against HEAD: PREPUSH_BASE, else the parent `dl pr --base` remembered for the branch
# (git config branch.<branch>.dl-base) while the remote has it, else <remote>/<integration>.
#   always: `dl ci <step> --base <base>` for size-cap, records-current, rules-current, unparsed;
#   then each `verify.prepush` name: the command `verify.steps.<name>`, run through `verify.runner`
#   (none for a `verify.host_steps` name).
#   A name that is also a `gates.sides` side runs only when gate mode lists that side for the
#   changed paths; an unreadable gate runs every step. A step's `{affected}` placeholder is left
#   empty, so the step runs in full: only `dl verify` narrows a step.
# One PASS/FAIL/SKIP line a step (a failure prints its last lines); exits 1 when any step failed. A
# step that cannot run (no command, no tool) fails: not run is never clear.
# Enable once per clone: `dl init` (git config core.hooksPath .githooks). Test: tests/test_githook.py
set -o pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1
here=$(cd "$(dirname "$0")" && pwd)
dl="${DL:-$here/../../../bin/dl}"
get() { bash "$dl" config get "$1"; }
minutes=$(get verify.step_timeout_minutes) || { echo "FAIL cannot read step timeout"; exit 1; }
gate() { PYTHONPATH="$here/../.." "${DL_PYTHON:-python3}" -m dev_loop.ci.gate_mode; }
log=$(mktemp)
trap 'rm -f "$log"' EXIT

integration=$(get branches.integration) || { echo "FAIL cannot read branches.integration"; exit 1; }
remote=$(get branches.remote) || { echo "FAIL cannot read branches.remote"; exit 1; }
if parent=$(git config "branch.$(git branch --show-current).dl-base" 2>/dev/null) &&
  git rev-parse -q --verify "$remote/$parent" >/dev/null; then
  ls_remote_timeout=30  # = doctor.LS_REMOTE_TIMEOUT (test_size_cap_fork pins it)
  GIT_TERMINAL_PROMPT=0 timeout "$ls_remote_timeout" git ls-remote --exit-code "$remote" \
    "refs/heads/$parent" >/dev/null 2>&1
  asked=$?
  if [ "$asked" = 2 ]; then  # merged and deleted: the tracking ref is stale
    echo "$remote has no branch $parent now; judged from $remote/$integration"
  else
    [ "$asked" = 0 ] ||
      echo "could not reach $remote within ${ls_remote_timeout}s; judged from $remote/$parent"
    stacked=$remote/$parent
  fi
fi
base=${PREPUSH_BASE:-${stacked:-$remote/$integration}}
merge_base=$(git merge-base "$base" HEAD) || { echo "FAIL no merge base with $base"; exit 1; }
changed=$(git diff --name-only --no-renames "$merge_base" HEAD)
failed=0
step() {
  local name=$1
  shift
  local start=$SECONDS
  timeout -k 5 "${minutes}m" "$@" >"$log" 2>&1
  local code=$?
  if [ "$code" = 0 ]; then
    echo "PASS $name ($((SECONDS - start)) s)"
  else
    echo "FAIL $name ($((SECONDS - start)) s)"
    [ "$code" = 124 ] && echo "FAIL $name (timed out after $minutes minute(s))"
    tail -n 25 "$log"
    failed=1
  fi
}

for name in size-cap records-current rules-current unparsed; do
  step "$name" bash "$dl" ci "$name" --base "$base"
done

sides=$(gate </dev/null) && run=$(gate <<<"$changed") || { echo "FAIL gate mode"; failed=1; sides=; }
runner=$(get verify.runner) || { echo "FAIL cannot read verify.runner"; exit 1; }
host=$(get verify.host_steps) || { echo "FAIL cannot read verify.host_steps"; exit 1; }
names=$(get verify.prepush) || { echo "FAIL cannot read verify.prepush"; exit 1; }
while IFS= read -r name; do
  [ -n "$name" ] || continue
  if grep -qxF "$name" <<<"$sides" && ! grep -qxF "$name" <<<"$run"; then
    echo "SKIP $name (its side's files did not change)"
  elif command=$(get "verify.steps.$name" 2>"$log"); then
    grep -qxF "$name" <<<"$host" && prefix= || prefix=${runner:+$runner }
    step "$name" bash -c "$prefix${command//'{affected}'/}"
  else
    echo "FAIL $name (no verify.steps.$name)"
    failed=1
  fi
done <<<"$names"

[ "$failed" = 0 ] && echo "pre-push: every step passed" || echo "pre-push: a step failed (above)"
exit "$failed"
