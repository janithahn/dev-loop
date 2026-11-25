#!/usr/bin/env bash
# Publishes local `main` to the release remote as ONE commit tagged v<version> (the plugin.json
# version): on top of the remote's main when it has one, else as a fresh history. Refuses an
# existing tag, a missing private name list (DEV_LOOP_PRIVATE_NAMES, else
# ~/.config/dev-loop/private-names.txt), a neutrality offender (test_neutral.py, KNOWN must be
# empty) or a secret-scan hit, all checked on the exact tree being released.
# Usage: RELEASE_REMOTE=<url or path> tools/release.sh [--push]   (dry run without --push)
set -euo pipefail
remote=${RELEASE_REMOTE:?set RELEASE_REMOTE to the release repository}
cd "$(git rev-parse --show-toplevel)"
manifest=plugins/dev-loop/.claude-plugin/plugin.json
version=$(git show "main:$manifest" \
  | python3 -c "import json, sys; print(json.load(sys.stdin)['version'])")
tag="v$version"
echo "version $version from main $(git rev-parse --short main)"
if git ls-remote --exit-code --tags "$remote" "refs/tags/$tag" >/dev/null; then
  echo "REFUSED: $tag exists on the release remote; bump the version"; exit 1
fi
out=$(mktemp -d)
if git ls-remote --exit-code "$remote" refs/heads/main >/dev/null; then
  git clone -q "$remote" "$out"
  find "$out" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
else
  git -C "$out" init -q -b main
fi
git archive main | tar -x -C "$out"
# the release commit is made as this repo's configured user
name=$(git config user.name) email=$(git config user.email)
cd "$out"
git config user.name "$name" && git config user.email "$email" && git config core.autocrlf false
git add -A
python3 - <<'EOF' || { echo "REFUSED: the neutrality guard failed"; exit 1; }
import sys
sys.path.insert(0, "plugins/dev-loop/tests")
import test_neutral as t
if not t.private_names():
    print(f"REFUSED: no private name list at {t.private_file()}; set DEV_LOOP_PRIVATE_NAMES")
    sys.exit(1)
bad = t.check(t._tracked())
print(f"KNOWN={len(t.KNOWN)} offenders={bad}")
sys.exit(1 if t.KNOWN or bad else 0)
EOF
python3 plugins/dev-loop/src/dev_loop/ci/secret_scan.py || { echo "REFUSED: secret scan"; exit 1; }
git commit -q -m "dev-loop $version"
git tag -a "$tag" -m "dev-loop $tag"
echo "release commit $(git rev-parse --short HEAD), $(git ls-files | wc -l) files, tag $tag in $out"
[ "${1:-}" = "--push" ] || { echo "dry run: not pushed"; exit 0; }
git push -q "$remote" main "$tag"
echo "pushed main and $tag"
