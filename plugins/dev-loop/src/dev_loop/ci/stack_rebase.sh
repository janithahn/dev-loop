#!/usr/bin/env bash
# stack_rebase.sh [<old base>]: rebase the current branch onto the integration branch (the part stack).
# A stacked part names its old base, the parent part's commit it was built on, and is moved with
# `git rebase --onto $STACK_ONTO <old base>`, so a squash-merged parent's commits are not replayed.
# With no old base it is a plain `git rebase $STACK_ONTO`. STACK_ONTO defaults to
# origin/<branches.integration>, read through `dl config get` as `size.generated` is.
# Conflicts:
#   an area CLAUDE.md conflicting only inside its generated do-not-repeat block: the integration
#     branch's side (records/guide_resolve.py); run `dl records rules --write` after;
#   a file matching a `size.generated` glob: the integration branch's side; regenerate it after;
#   anything else: the rebase is aborted, the branch is left as it was, exit 1 naming the files.
# Test: tests/test-stack-rebase.sh.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
dl="${DL:-$here/../../../bin/dl}"
get() { bash "$dl" config get "$1" || { echo "stack_rebase: cannot read $1 through $dl" >&2; exit 1; }; }
onto=${STACK_ONTO:-$(get branches.remote)/$(get branches.integration)} || exit 1
generated=$(get size.generated) || exit 1
is_generated() {
  local glob
  while IFS= read -r glob; do
    # shellcheck disable=SC2053 # the glob is a pattern on purpose
    [ -n "$glob" ] && [[ $1 == $glob ]] && return 0
  done <<<"$generated"
  return 1
}
is_guide() { [[ $1 == CLAUDE.md || $1 == */CLAUDE.md ]]; }
if [ $# -gt 0 ]; then args=(--onto "$onto" "$1"); else args=("$onto"); fi
abort() { echo "stack_rebase: $1" >&2; git rebase --abort 2>/dev/null; exit 1; }

out=$(git rebase -q "${args[@]}" 2>&1) && exit 0
while true; do
  conflicted=$(git diff --name-only --diff-filter=U) || abort "cannot list the conflicts"
  [ -n "$conflicted" ] || abort "rebase stopped without a conflict: $out"
  bad=$(while read -r file; do is_guide "$file" || is_generated "$file" || echo "$file"; done \
    <<<"$conflicted")
  [ -z "$bad" ] || abort "conflict outside the guide blocks and the generated code: $bad"
  while read -r file; do
    if is_guide "$file"; then
      "${DL_PYTHON:-python3}" "$here/../records/guide_resolve.py" "$file" || abort "guide prose conflict in $file"
    else
      git checkout --ours -- "$file" || abort "cannot take the integration branch's side of $file"
    fi
    git add -- "$file" || abort "cannot stage $file"
  done <<<"$conflicted"
  out=$(GIT_EDITOR=true git rebase --continue 2>&1) && break
done
echo "stack_rebase: guide or generated conflicts took the integration branch's side;" \
  "run dl records rules --write, regenerate the generated code"
