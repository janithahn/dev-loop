#!/usr/bin/env bash
# Self-check for ci/stack_rebase.sh on a temp project whose integration branch is `trunk` and whose
# generated code sits under `gen/client/` (dev-loop.yaml). Run: bash tests/test-stack-rebase.sh
set -euo pipefail
script="$(cd "$(dirname "$0")/../src/dev_loop/ci" && pwd)/stack_rebase.sh"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t 
export CLAUDE_PROJECT_DIR="$tmp"; unset STACK_ONTO
fail() { echo "FAIL: $1"; exit 1; }
cd "$tmp" && git init -q -b trunk . && git config core.autocrlf false
sync() { git update-ref refs/remotes/origin/trunk trunk; }
commit() { git add -A && git commit -qm "$1"; }
guide() { printf 'Prose.\n<!-- do-not-repeat:start -->\n%s\n<!-- do-not-repeat:end -->\nMore prose.\n' "$1" > CLAUDE.md; }
printf 'branches: {integration: trunk}\nsize: {generated: [gen/client/**]}\n' > dev-loop.yaml
mkdir -p src gen/client
guide old; echo base > src/a.py; echo v1 > gen/client/t.ts; commit base
# part a, then part b stacked on it
git checkout -qb part-a; echo a1 > src/a.py; commit a1; echo part-a > src/a.py; commit a2; a_tip=$(git rev-parse HEAD)
git checkout -qb part-b; echo b > src/b.py; guide b-block; echo vb > gen/client/t.ts; commit b1
# trunk squash-merges part a, then regenerates its guide block and client
git checkout -q trunk; echo part-a > src/a.py; commit "squash a"
guide dev-block; echo vdev > gen/client/t.ts; commit regen

git checkout -q part-b
sync; bash "$script" "$a_tip" >/dev/null || fail "stacked part did not rebase"
[ "$(git rev-list --count trunk..HEAD)" = 1 ] || fail "part a's commit was replayed"
grep -qx dev-block CLAUDE.md || fail "guide block did not take trunk's side"
grep -qx vdev gen/client/t.ts || fail "client did not take trunk's side"
[ -f src/b.py ] || fail "part b's own change lost"
# a second run is idempotent: commits already on trunk are dropped, nothing is replayed twice
sync; bash "$script" "$a_tip" >/dev/null || fail "a second run failed"
[ "$(git rev-list --count trunk..HEAD)" = 1 ] || fail "a second run replayed commits"

# a source conflict aborts and leaves the branch as it was
git checkout -qb part-c "$a_tip"; echo part-c > src/a.py; commit c1; before=$(git rev-parse HEAD)
git checkout -q trunk; echo dev > src/a.py; commit "dev edits a"; git checkout -q part-c
if sync; bash "$script" 2>/dev/null; then fail "source conflict did not stop"; fi
[ "$(git rev-parse HEAD)" = "$before" ] || fail "branch moved after an aborted rebase"
[ ! -d .git/rebase-merge ] || fail "rebase left in progress"

# a prose conflict in a guide aborts too
git checkout -qb part-d trunk; sed -i 's/^Prose\./Prose d./' CLAUDE.md; commit d1
git checkout -q trunk; sed -i 's/^Prose\./Prose dev./' CLAUDE.md; commit "dev prose"; git checkout -q part-d
if sync; bash "$script" 2>/dev/null; then fail "guide prose conflict did not stop"; fi
# a client deleted on trunk cannot take trunk's side by checkout: abort, never stage the branch's copy
git checkout -qb part-e trunk; echo ve > gen/client/t.ts; commit e1; e_tip=$(git rev-parse HEAD)
git checkout -q trunk; git rm -q gen/client/t.ts; commit "dev drops t"; git checkout -q part-e
if sync; bash "$script" 2>/dev/null; then fail "a modify/delete client conflict did not stop"; fi
[ "$(git rev-parse HEAD)" = "$e_tip" ] || fail "branch moved after a modify/delete abort"
# a rebase that fails for another reason says why
echo dirty >> src/a.py
err=$(sync; bash "$script" 2>&1) && fail "a dirty tree rebased"
grep -q "without a conflict: ." <<<"$err" || fail "git's own error not printed: $err"
echo "stack_rebase: ok"
