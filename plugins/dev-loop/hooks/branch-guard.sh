#!/usr/bin/env sh
# PreToolUse(Edit|Write) hook: the branch guard.
# Refuses an edit on a protected branch: branches.integration, branches.release and
# branches.protected from the project's dev-loop.yaml. Exit 2 blocks the tool call and shows
# the message on stderr to Claude.
# The branch is read from the nearest existing folder above the edited file, so a worktree on its
# own feature branch is judged by that branch, never by the root checkout. The project is
# CLAUDE_PROJECT_DIR, else git's top level from the working directory.

PLUGIN="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"

# The config cannot be read (no PyYAML): the shipped integration branch stays protected.
branches="$(cd "${ROOT:-.}" && bash "$PLUGIN/bin/dl" config get branches 2>/dev/null)" \
  || branches='{"integration": "main"}'

# Line 1: the edited file; then one protected branch a line.
out="$(BRANCHES="$branches" "${DL_PYTHON:-python3}" -c '
import json, os, sys
try:
    payload = json.load(sys.stdin)
except ValueError:
    payload = {}
print(payload.get("tool_input", {}).get("file_path", ""))
b = json.loads(os.environ["BRANCHES"])
print(*[n for n in [b.get("integration"), b.get("release"), *(b.get("protected") or [])] if n],
      sep="\n")
')"
file="$(printf '%s\n' "$out" | sed -n 1p)"
protected="$(printf '%s\n' "$out" | sed 1d)"

dir="$ROOT"
# A Windows path (C:\...) becomes a POSIX one where cygpath exists (Git Bash only).
[ -n "$file" ] && command -v cygpath >/dev/null 2>&1 && file="$(cygpath -u "$file")"
[ -n "$file" ] && dir="$(dirname "$file")"
while [ -n "$dir" ] && [ ! -d "$dir" ]; do dir="$(dirname "$dir")"; done
branch="$(git -C "${dir:-.}" branch --show-current 2>/dev/null)"

if [ -n "$branch" ] && printf '%s\n' "$protected" | grep -qxF -- "$branch"; then
  echo "BLOCKED: refusing to edit on protected branch '$branch'. Create a feature branch first: git checkout -b <feature-name>" >&2
  exit 2
fi

exit 0
