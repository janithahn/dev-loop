#!/usr/bin/env sh
# PostToolUse(Bash) hook: the PR review loop trigger.
#
# A skill fires on the model's judgement of intent. Raising a PR is exactly the moment
# nobody remembers to ask for a review, so the reminder comes from the harness. This hook
# only NUDGES: it writes an advisory to stderr and exits 2, which Claude Code shows to the model
# (exit-0 stdout goes to the debug log only). It never edits, never posts, never merges.
#
# It reads the command that ran (tool_input.command) and nothing else, so a command whose output
# merely mentions a create stays silent. A create is `/dev-loop:pr`, `dl pr`, `dl host pr-create`,
# `az repos pr create`, `gh pr create`, or a curl or python POST to the `/pullrequests` collection
# (matched on the raw command, whose URL is usually quoted). The collection ends the path: a write
# to one PR (`/pullrequests/<id>`, its threads, its reviewers) is no create, nor is a PATCH, a PUT
# or a `-G` read that carries data.
# Turn it off with `review.auto_trigger: false` in dev-loop.yaml. The project is
# CLAUDE_PROJECT_DIR, else git's top level from the working directory.
# An open PR reported in the response (`open PR <branch> -> ...`) is no create: silent.
# So is a refusal (`dl pr: ` in the response) and a create called with `--help` or ` -h` (judged on
# the create's own segment, so an `-h` elsewhere in the command changes nothing). A script given to
# `sh -c` or `bash -c` is read as a command; `gh` may carry options before `pr create`.
# Exit 2 with the nudge on a PR create; exit 0, silent, otherwise.

PLUGIN="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"

command="$(python3 -c '
import json, re, sys
try:
    payload = json.load(sys.stdin)
    out = str(payload.get("tool_response"))
    cmd = payload.get("tool_input", {}).get("command", "")
    if "open PR " not in out and "dl pr: " not in out:
        # a script handed to a shell is a command, not quoted text
        cmd = re.sub(r"\b(?:ba)?sh\s+-\w*c\s+([\"\x27])(.*?)\1", r"; \2 ;", cmd, flags=re.S)
        # line 1: the command without its quoted text; line 2: the raw command (a quoted URL)
        print(re.sub(r"\"[^\"]*\"|\x27[^\x27]*\x27", " ", cmd).replace("\n", ";"))
        print(cmd.replace("\n", ";"))
except (ValueError, AttributeError):
    pass
' 2>/dev/null)"
[ -n "$command" ] || exit 0
raw="$(printf '%s\n' "$command" | sed -n 2p)"
command="$(printf '%s\n' "$command" | sed -n 1p)"

start='(^|[;&|(])[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*'
gh='gh ([^;&|]*[[:space:]])?pr create'
create="$start(/dev-loop:pr|dl pr|dl host pr-create|az repos pr create|$gh)(\$|[;&|]|[[:space:])][^;&|]*)"
help='--help|[[:space:]]-h([[:space:];&|]|$)'
post="$start(curl|python3?)[[:space:]].*/pullrequests([?\"'[:space:]]|\$)"
verb='(-X|--request)[ =]*POST|[[:space:]](-d|--data[a-z-]*|--json)[[:space:]=]|\.post\('
verb="$verb|method=[\"']POST"
other='(-X|--request)[ =]*(PATCH|PUT)|[[:space:]](-G|--get)([[:space:]]|$)'
if ! printf '%s\n' "$command" | grep -oE "$create" | grep -qvE -e "$help"; then
  printf '%s\n' "$raw" | grep -E "$post" | grep -iE "$verb" | grep -qvE "$other" || exit 0
fi

on="$(cd "${ROOT:-.}" && bash "$PLUGIN/bin/dl" config get review.auto_trigger 2>/dev/null)"
[ "$on" = "false" ] && exit 0

cat >&2 <<EOF
[pr-loop] A pull request was just raised from this session.

Run the review/fix loop on it now, before a human reads it: invoke /dev-loop:review with
the PR id from the response above. Round 1 reviews the whole PR; later rounds review only
the fixes. Blockers and majors are fixed and rechecked with no round cap; minors go to the
change's own follow-ups.md. The loop ends at the first round with no blocker and no major.

The loop never votes on or completes the PR; that stays with a person.

If the user asked for the PR without a review, say you are running the loop rather
than skipping it silently; if they decline, stop.
EOF

exit 2
