#!/usr/bin/env sh
# PostToolUse(Edit|Write) hook: the project's guard on the one edited file.
# Runs `gates.on_edit` (a shell command; the edited file is its last argument) in the checkout
# that holds the file, so a worktree is judged by its own guards. A failing guard's output goes to
# stderr with exit 2: Claude Code shows it to the agent; the edit itself stands. No `on_edit`, no
# file in the payload, or a passing guard: exit 0, silent. An unreadable config is a tool failure:
# one line to stderr, exit 0. The project is CLAUDE_PROJECT_DIR, else git's top level.
# A configured guard script that is missing is reported once as a configuration problem (exit 2
# with the message, then silent), not as a failing guard.
# Test: tests/test_edit_hooks.py.

PLUGIN="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"

file="$("${DL_PYTHON:-python3}" -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null | tr '\134' '/')"
[ -n "$file" ] || exit 0
guard="$(cd "${ROOT:-.}" && bash "$PLUGIN/bin/dl" config get gates.on_edit)" \
  || { echo "guard-on-edit: cannot read gates.on_edit; skipped" >&2; exit 0; }
[ -n "$guard" ] && [ "$guard" != null ] || exit 0
checkout="$(git -C "$(dirname "$file")" rev-parse --show-toplevel 2>/dev/null)"
# a configured guard that is not there is a configuration problem, said once, never a failing guard
set -f; set -- $guard; set +f
case "$1" in
  sh|bash|python|python3|node) case "$2" in -*|"") script="" ;; *) script="$2" ;; esac ;;
  *=*) script="" ;;
  *) script="$1" ;;
esac
if [ -n "$script" ] && ! { cd "${checkout:-${ROOT:-.}}" && { [ -e "$script" ] || command -v "$script" >/dev/null 2>&1; }; }; then
  mark="${TMPDIR:-/tmp}/dl-guard-missing-$(printf '%s' "${checkout:-$ROOT}$guard" | cksum | cut -d' ' -f1)"
  [ -e "$mark" ] && exit 0
  : > "$mark"
  echo "guard-on-edit: guard script $script not found; check gates.on_edit" >&2
  exit 2
fi
out="$(cd "${checkout:-${ROOT:-.}}" && sh -c "$guard \"\$1\"" on-edit "$file" 2>&1)" && exit 0
printf '%s\n' "$out" >&2
exit 2
