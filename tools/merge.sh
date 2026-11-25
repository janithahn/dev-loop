#!/usr/bin/env bash
# Merges task branches from $MERGE_REMOTE (default origin) into main, prunes the neutrality guard's
# KNOWN list, runs the base suite plus every test file the merged branches touched, then
# fast-forwards main and pushes it. A branch that conflicts is skipped and named; a conflict in
# test_neutral.py alone takes main's side (the prune then settles KNOWN).
# Usage: [MERGE_REMOTE=hub] [EXTRA_TESTS="test_x.py ..."] tools/merge.sh <branch>...
set -uo pipefail
remote=${MERGE_REMOTE:-origin}
guard=plugins/dev-loop/tests/test_neutral.py
cd "$(git rev-parse --show-toplevel)" || exit 1
export GIT_CONFIG_NOSYSTEM=1 GCM_INTERACTIVE=never
git fetch -q "$remote" || exit 1
git checkout -q main && git merge -q --ff-only "$remote/main" || { echo "main not ff"; exit 1; }
work="merge/$(date +%H%M%S)"
git checkout -q -b "$work" main
merged=""
for b in "$@"; do
  if git merge -q --no-ff "$remote/$b" -m "Merge $b" >/dev/null 2>&1; then
    merged="$merged $b"; continue
  fi
  if [ "$(git diff --name-only --diff-filter=U)" = "$guard" ]; then
    git checkout -q --ours "$guard" && git add "$guard" && git commit -q --no-edit
    merged="$merged $b"
  else
    echo "CONFLICT $b: $(git diff --name-only --diff-filter=U | tr '\n' ' ')"
    git merge --abort
  fi
done
if [ -z "$merged" ]; then
  git checkout -q main; git branch -q -D "$work"; echo "nothing merged"; exit 1
fi
python3 - "$guard" <<'EOF' || exit 1
import ast, re, sys
sys.path.insert(0, "plugins/dev-loop/tests")
import test_neutral as t
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
found = re.search(r"^KNOWN: list\[str\] = (\[.*?\])", text, re.MULTILINE | re.DOTALL)
known = ast.literal_eval(found.group(1))
offending = set(t.check(t._tracked()))
keep = [name for name in known if name in offending]
listed = "[" + "".join(f'\n    "{name}",' for name in keep) + ("\n]" if keep else "]")
text = text[:found.start(1)] + listed + text[found.end(1):]
open(path, "w", encoding="utf-8", newline="\n").write(text)
print(f"KNOWN {len(known)} -> {len(keep)}; new offenders: {sorted(offending - set(known))}")
EOF
git add "$guard"
git diff --cached --quiet \
  || git commit -q -m "tests: KNOWN drops the files the merged tasks made neutral"
cd plugins/dev-loop/tests || exit 1
touched=$(git diff --name-only main HEAD -- . | sed -n 's|^plugins/dev-loop/tests/||p' \
  | grep -E '(^|/)test_[^/]*\.py$')
base="test_neutral.py test_agents.py test_templates.py test_main.py test_dev_loop_config.py
  test_dev_loop_schema.py test_pr_review.py test_tasklist.py test_versions.py test_host_ado.py
  test_record.py test_record_writer.py test_record_unparsed.py test_record_done.py
  test_dev_loop_config_roles.py tools/test_size_cap.py test_review_records.py test_distil.py
  test_impact_records.py test_review_lessons.py test_review_pitfalls.py test_review_rules.py
  test_review_trend.py test_recall.py ${EXTRA_TESTS:-}"
tests=""
for f in $(printf '%s\n' $base $touched | sort -u); do [ -f "$f" ] && tests="$tests $f"; done
runner="python3 -m pytest"
if command -v uv >/dev/null; then
  runner="uv run -q --no-project --with pytest --with pyyaml python -m pytest"
fi
log=$(git rev-parse --absolute-git-dir)/merge-test.log
# shellcheck disable=SC2086 # the runner and the test list split on spaces
timeout 1200 $runner $tests -q -p no:cacheprovider > "$log" 2>&1
rc=$?
tail -4 "$log"
cd - >/dev/null || exit 1
if [ $rc -ne 0 ]; then
  echo "TESTS FAILED on $work (kept; log $log)"; git checkout -q main; exit 1
fi
git checkout -q main && git merge -q --ff-only "$work" && git push -q "$remote" main \
  && git branch -q -d "$work"
echo "merged:$merged"
git log --oneline -1
