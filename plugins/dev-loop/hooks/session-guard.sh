#!/usr/bin/env sh
# SessionStart hook: warn on a protected branch, then print the session checklist.
# The project is CLAUDE_PROJECT_DIR, else git's top level (never this file's place). The checklist
# is the file `knowledge.session_checklist` names (relative to the project), else a neutral
# three-line default. Protected: `branches.integration`, `branches.release`, `branches.protected`.
# Always exits 0: SessionStart informs, it never blocks.

plugin="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
get() { (cd "$root" && bash "$plugin/bin/dl" config get "$1" 2>/dev/null); }

branch="$(git -C "$root" branch --show-current 2>/dev/null)"
if [ -n "$branch" ] && { get branches.integration; get branches.release; get branches.protected; } \
  | grep -qxF "$branch"; then
  echo "⚠ On protected branch '$branch' — create a feature branch before editing."
  echo ""
fi

dups="$(cd "$root" && bash "$plugin/bin/dl" doctor --duplicates 2>/dev/null)"
if [ -n "$dups" ]; then
  echo "$dups"
  echo ""
fi

if [ "$(get caps.enabled)" = "false" ]; then
  echo "dev-loop context caps are disabled (caps.enabled: false)."
  echo ""
fi

checklist="$(get knowledge.session_checklist)"
if [ -n "$checklist" ] && [ "$checklist" != "null" ] && [ -f "$root/$checklist" ]; then
  cat "$root/$checklist"
else
  echo "- [ ] Read the project's CLAUDE.md or AGENTS.md before editing."
  echo "- [ ] Work on a feature branch, never on the integration branch."
  echo "- [ ] Run the project's checks before calling a change done."
fi
exit 0
