#!/usr/bin/env bash
# Self-check for ci/size_cap.sh on a real `git diff --numstat` in a temp project whose dev-loop.yaml
# names non-default globs and change folder. Run: bash tests/test-size-cap.sh
set -euo pipefail
script="$(cd "$(dirname "$0")/../src/dev_loop/ci" && pwd)/size_cap.sh"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export CLAUDE_PROJECT_DIR="$tmp"; unset SIZE_CAP SIZE_ADVISE
fail() { echo "FAIL: $1"; exit 1; }
cd "$tmp" && git init -q . && git config core.autocrlf false
cat > dev-loop.yaml <<'YAML'
paths: {changes: notes/changes}
size: {cap: 50, advise: 20, production: [lib/**], exclude: ["**/test_*"], generated: [lib/gen/**]}
YAML
git add -A && git commit -qm base
mkdir -p lib/gen notes/changes/row
seq 60 > lib/a.py; seq 500 > lib/test_a.py; seq 500 > lib/gen/client.py; seq 500 > other.py
git add -A && git commit -qm work
stat() { git diff --numstat --no-renames HEAD^1 HEAD; }
out=$(stat | bash "$script") && fail "60 lines passed a 50-line cap: $out"
grep -q "60 production lines" <<<"$out" || fail "no count named: $out"
echo 'Size waiver: lead 2026-01-01 — one module' > notes/changes/row/proposal.md
git add -A && git commit -qm waiver --amend
out=$(stat | bash "$script") || fail "the waiver in notes/changes/row was not read: $out"
grep -q "notes/changes/row/proposal.md" <<<"$out" || fail "the waiver not named: $out"
SIZE_CAP=100 bash "$script" < <(stat) >/dev/null || fail "SIZE_CAP did not override the config"
echo "size_cap.sh checks: ok"
