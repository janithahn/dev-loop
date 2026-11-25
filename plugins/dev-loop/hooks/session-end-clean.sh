#!/usr/bin/env sh
# SessionEnd hook: run `dl clean --apply` at most once per 24 hours (a stamp file), silent unless
# it removed something. Off when `cleanup.auto` is false. The project is CLAUDE_PROJECT_DIR, else
# git's top level from the working directory. Always exits 0: a session-end hook never blocks.
# DEV_LOOP_CLEAN_STAMP overrides the stamp file (the tests set it).

plugin="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
dl="$plugin/bin/dl"

[ "$(cd "$root" && bash "$dl" config get cleanup.auto 2>/dev/null)" = "false" ] && exit 0

stamp="${DEV_LOOP_CLEAN_STAMP:-$HOME/.dev-loop/clean.stamp}"
now="$(date +%s)"
if [ -f "$stamp" ] && [ "$((now - $(cat "$stamp" 2>/dev/null || echo 0)))" -lt 86400 ]; then
  exit 0  # cleaned within the last 24 hours
fi
mkdir -p "$(dirname "$stamp")" && echo "$now" >"$stamp"

out="$(cd "$root" && bash "$dl" clean --apply 2>/dev/null)"
printf '%s\n' "$out" | grep -q "removed nothing" && exit 0
printf '%s\n' "$out" >&2  # it removed something: say what, on stderr
exit 0
