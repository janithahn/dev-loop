#!/usr/bin/env sh
# Stop hook: the done guard. What is missing goes to stderr with exit 2, which keeps Claude working
# with it as the reason (exit-0 output goes to the debug log, unseen). A Stop already held by this
# hook (`stop_hook_active` true) exits 0, so it never holds the turn it caused. A headless
# `claude -p` passes --settings '{"disableAllHooks": true}', or is held too.
# For every change (a folder under `paths.changes`) whose task boxes are all ticked, `dl record
# done` names each line of the done rule its records.jsonl still lacks.
# Scope: only the changes this checkout works on. With a reachable base (DONE_GUARD_BASE, default
# <branches.remote>/<branches.integration>) the paths changed since base plus untracked ones; with
# no reachable base, the uncommitted and untracked ones, never the whole tree. The paths go to
# `dl record done --touched-paths -` as git prints them (`-z`, verbatim): which change folder holds
# each is judged there on resolved paths, never by `realpath` or text here. A git or dl command
# that errors is a tool failure: one line to stderr, exit 0, so only a real missing record holds the
# Stop. With no git at all the scope is unknown, so every change is named (never fewer).
# The project is CLAUDE_PROJECT_DIR, else git's top level. Test: tests/test-done-guard.sh.

PLUGIN="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
grep -qE '"stop_hook_active"[[:space:]]*:[[:space:]]*true' 2>/dev/null && exit 0
cd "${ROOT:-.}" || exit 0
dl() { bash "$PLUGIN/bin/dl" "$@"; }
skip() { echo "done-guard: $1; skipped" >&2; exit 0; }
if git rev-parse --git-dir >/dev/null 2>&1; then
  integration="$(dl config get branches.integration)" || skip "cannot read branches.integration"
  remote="$(dl config get branches.remote)" || skip "cannot read branches.remote"
  base="$(git merge-base "${DONE_GUARD_BASE:-$remote/$integration}" HEAD 2>/dev/null)"
  # A file, not a pipe: a pipe would hide a git failure behind dl's exit status.
  paths="$(mktemp)" || skip "cannot make a temporary file"
  trap 'rm -f "$paths"' EXIT
  # diff.relative off: the paths stay top-relative with the project in a subfolder (v422j).
  git -c diff.relative=false diff -z --name-only "${base:-HEAD}" -- >"$paths" \
    && git ls-files -z --others --exclude-standard --full-name -- :/ >>"$paths" \
    || skip "listing the touched paths failed"
  report="$(dl record done --touched-paths - <"$paths")" \
    || skip "the done check failed (dl record done)"
else
  report="$(dl record done)" || skip "the done check failed (dl record done)"
fi
[ -z "$report" ] && exit 0
printf '%s\n' "$report" >&2
exit 2
