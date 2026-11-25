#!/usr/bin/env bash
# size_cap.sh: hold a PR to the size cap. Reads `git diff --numstat --no-renames <base> HEAD` on
# stdin; run from the project root. Production lines are added + deleted lines of the files that
# match a `size.production` glob and no `size.exclude` or `size.generated` glob (binaries skipped).
# A glob's `*` crosses folders; a leading `**/` also matches at the top level.
#   over size.cap: exit 1, unless the proposal.md of a change folder (under paths.changes) the diff
#     touches has a line `Size waiver: <who> <yyyy-mm-dd> — <reason>` (bold allowed); then exit 0
#     naming it and printing the line, for the reviewer to confirm a person wrote it;
#   over size.advise: exit 0 with the advice to split. SIZE_CAP / SIZE_ADVISE override the config.
# Every key is read through `dl config get`; an unreadable config stops the gate (exit 1).
# Under `dl ci size-cap` the size section is the base ref's, so a PR cannot raise its own cap;
# every other key is the working tree's, and SIZE_CAP / SIZE_ADVISE are ignored.
# Tests: tests/tools/test_size_cap.py, tests/test-size-cap.sh.
set -euo pipefail
dl="${DL:-$(cd "$(dirname "$0")/../../.." && pwd)/bin/dl}"
get() {  # one config value (a list: one item a line); exit 1 naming the key when unreadable
  bash "$dl" config get "$1" && return
  echo "size cap: cannot read $1 through $dl" >&2
  exit 1
}
cap=${SIZE_CAP:-$(get size.cap)} || exit 1
advise=${SIZE_ADVISE:-$(get size.advise)} || exit 1
production=$(get size.production) || exit 1
skip=$(get size.exclude && get size.generated) || exit 1
changes=$(get paths.changes) || exit 1
matches() {  # matches <path> <globs, one a line>
  local glob
  while IFS= read -r glob; do
    [ -n "$glob" ] || continue
    # shellcheck disable=SC2053 # the glob is a pattern on purpose
    if [[ $1 == $glob ]] || { [[ $glob == '**/'* ]] && [[ $1 == ${glob#'**/'} ]]; }; then return 0; fi
  done <<<"$2"
  return 1
}
lines=0 touched=()
while IFS=$'\t' read -r added deleted path; do
  [ -n "${path:-}" ] || continue
  rest=${path#"$changes"/}
  [[ $path == "$changes"/*/* ]] && touched+=("${rest%%/*}")
  [ "$added" != - ] || continue
  if matches "$path" "$production" && ! matches "$path" "$skip"; then
    lines=$((lines + added + deleted))
  fi
done
# who, a date, an em dash, a reason: `Size waiver: lead 2026-01-01 — built under the old rule`
waiver='^\**Size waiver:\**[[:space:]]+[^[:space:]].*[[:space:]][0-9]{4}-[0-9]{2}-[0-9]{2}[[:space:]]+—[[:space:]]+[^[:space:]]'
if [ "$lines" -gt "$cap" ]; then
  for change in $(printf '%s\n' "${touched[@]}" | sort -u); do
    proposal="$changes/$change/proposal.md"
    if line=$(grep -m1 -E "$waiver" "$proposal" 2>/dev/null); then
      echo "size cap: $lines production lines, over the $cap-line cap with the waiver in $proposal"
      # no check here can tell who wrote the line (any file or git author can be forged from a
      # shell), so the gate's log shows it to the person who reviews the PR
      echo "size cap: a person confirms this waiver in review: $line"
      exit 0
    fi
  done
  echo "size cap: $lines production lines is over the $cap-line cap. Split the row into parts" \
    "(dl stack-rebase) or record 'Size waiver: <who> <date> — <reason>' in its proposal.md."
  exit 1
fi
if [ "$lines" -gt "$advise" ]; then
  echo "size cap: $lines production lines; over $advise, consider a split."
else
  echo "size cap: $lines production lines (cap $cap)"
fi
