#!/usr/bin/env sh
# PostToolUse(Edit|Write) hook: a light, scoped, advisory lint on the edited file only.
# Reads the edited path(s) from $CLAUDE_FILE_PATHS, else the stdin payload's file_path. Warnings go
# to stderr with exit 2, the only PostToolUse channel the model reads (exit-0 output goes to the
# debug log). The edit stands. No warning: exit 0, silent. A project's own rules run through
# `gates.on_edit` (guard-on-edit.sh); the hard gate is the verify step and CI.
# Test: tests/test_edit_hooks.py.

paths="$CLAUDE_FILE_PATHS"
if [ -z "$paths" ]; then
  payload="$(cat 2>/dev/null)"
  paths="$(printf '%s' "$payload" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]+"' | sed -E 's/.*"file_path"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
fi
[ -z "$paths" ] && exit 0

warnings="$(for f in $paths; do
  [ -f "$f" ] || continue
  lines="$(wc -l < "$f" 2>/dev/null | tr -d ' ')"
  case "$f" in
    *.py|*.ts|*.tsx)
      [ "${lines:-0}" -gt 300 ] && echo "  [size]  $f: $lines lines — limit is 300 (hard 400). Split by responsibility."
      ;;
  esac
  case "$f" in
    *.py)
      grep -nE '^\s*print\(' "$f" 2>/dev/null | head -1 | grep -q . && \
        echo "  [style] $f: print() in source — use a logger."
      command -v ruff >/dev/null 2>&1 && ruff check --quiet --select C901,PLR0913,PLR0915,E501 "$f" 2>/dev/null | head -5
      ;;
    *.ts|*.tsx)
      grep -nE ':[[:space:]]*any([^A-Za-z]|$)' "$f" 2>/dev/null | head -1 | grep -q . && \
        echo "  [style] $f: avoid 'any' — use exact types."
      ;;
  esac
done)"
[ -z "$warnings" ] && exit 0
printf '%s\n' "$warnings" >&2
exit 2
