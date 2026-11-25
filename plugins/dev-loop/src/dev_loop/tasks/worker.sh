#!/usr/bin/env bash
# Machine worker: poll host work items tagged worker-$DEV_LOOP_WORKER and run one headless Claude
# per task or coordinator comment, up to $SLOTS at once for $TIMEOUT_HOURS and $MAX_TURNS turns
# each, never two runs of one task. The worker never asks a person: questions go on the work
# item. Started by `dl worker`, which sets every DEV_LOOP_* value and the limits from the
# project's config;
# `worker.sh --brief <id>` prints one brief.
# A task or comment that arrives while its task runs queues one re-run after it ($RUN/<id>/again).
# One runner per run dir (flock). A failed run and a swept lock are commented on the task.
# When idle it fast-forwards the checkout to origin/$DEV_LOOP_BASE and re-execs, so a long-lived
# runner never drifts behind the integration branch (it never resets, merges or drops work).
# The re-exec keeps the runner lock: a child still holding fd 9 cannot lock the new runner out.
# With DEV_LOOP_TELEMETRY=1 or CLAUDE_CODE_ENABLE_TELEMETRY=1 each run exports its own
# OTEL_RESOURCE_ATTRIBUTES; DEV_LOOP_TASK always. Test knobs: WAIT WORKER_ONCE=<n polls>.
set -u
: "${DEV_LOOP_WORKER:?run through dl worker}" "${DEV_LOOP_CHECKOUT:?}" "${DEV_LOOP_WORKTREES:?}"
: "${DEV_LOOP_BASE:?}" "${DEV_LOOP_NEVER:?}" "${SLOTS:?}" "${POLL_SECONDS:?}" "${TIMEOUT_HOURS:?}"
: "${DOCKER_SLOTS:?}" "${DEFAULT_BASE:?}" "${DEFAULT_ROLE:?}" "${STALE_HOURS:?}"
REMOTE="${DEV_LOOP_REMOTE:-origin}"  # branches.remote, exported by dl worker
LOG="$HOME/.dev-loop/worker.log"
RUN="$HOME/.dev-loop/worker-run"   # one lock dir per running task, holding its pid

brief() {
  local tree="$DEV_LOOP_WORKTREES/$1" MAY_RUN_STEP=""
  if [ -n "${DEV_LOOP_MAY_RUN:-}" ]; then
    MAY_RUN_STEP="   This machine takes only these kinds of work: $DEV_LOOP_MAY_RUN. A task of another
   kind (its Kind: line, else its paths) is not built and not started: answer it with
   dl tasks comment $1 \"<kind not taken>\" and stop.
"
  fi
  cat <<EOF
You are running headless: run every command in the foreground with a timeout (tests included). Never start a background command and never end your turn to wait for a notification; nothing will wake you, and the run ends.

You are machine worker "$DEV_LOOP_WORKER" on a server, working on host task #$1.
Nobody is at this keyboard. Never ask a person anything and never wait for an answer.

1. Read the task: dl tasks show $1
${MAY_RUN_STEP}2. If it is To Do: dl tasks start $1
3. Before any work, check every branch or commit the brief names with
   git rev-parse --verify <ref>; a missing one: comment the task naming it, and stop.
4. Do the work the brief and the coordinator's comments ask, under the repository's own rules.
   You start in this task's own worktree $tree (off $REMOTE/$DEFAULT_BASE); never work in the
   shared checkout $DEV_LOOP_CHECKOUT. If the brief names another base, git reset --hard to it;
   the loop's clean removes the worktree later.
   Never commit to $REMOTE/$DEV_LOOP_BASE directly, and never touch $DEV_LOOP_NEVER
   unless the brief says so by name. A pull request targets $DEV_LOOP_BASE unless the brief
   names another branch; pass the target explicitly.
5. Report through the task only:
   - progress or a checkpoint: dl tasks comment $1 "<text>"
   - a question or a blocker: comment "QUESTION: <the question and your default>" and
     stop; the coordinator answers with a comment and you run again.
   - finished: dl tasks done $1 "<what changed, branch, pull request link>"
Other runs share this machine: run any Docker test command as
flock $HOME/.dev-loop/docker-$(($1 % DOCKER_SLOTS)).lock <command>  (one run per lock).
Never put secrets, customer data or document text in a comment.
EOF
}

if [ "${1:-}" = "--brief" ]; then brief "$2"; exit 0; fi

log() { echo "$(date -Is) $*" >>"$LOG"; }
tell() {  # a comment on task $1: exit codes and states only, never a run's output
  timeout 120 dl tasks comment "$1" "worker $DEV_LOOP_WORKER: $2" >>"$LOG" 2>&1 ||
    log "#$1: comment failed rc=$?"
}
clean() {  # dl clean --apply (with any extra flag), unless cleanup.auto is false
  [ "$(dl config get cleanup.auto 2>/dev/null)" = "false" ] && return 0
  timeout 300 dl clean --apply ${1:-} >>"$LOG" 2>&1 || log "clean rc=$?"
}
stall_note() {  # the note a stall's re-run opens with, naming the worktree it continues from
  echo "run ended without finishing (exit $2); re-running once to continue from the worktree $DEV_LOOP_WORKTREES/$1"
}
handoff_note() {  # the note a hand-off's re-run opens with: the worktree and the agent's own note
  echo "A previous run reached its context cap and handed off; its work in progress is committed on \
the branch in the worktree $DEV_LOOP_WORKTREES/$1. Continue from there. Hand-over note: $2"
}
role_of() {  # the role the brief names in a `Role:` line, else the worker's default
  local role
  role="$(timeout 120 dl tasks show "$1" 2>>"$LOG" |
    sed -nE 's/^[[:space:]]*[Rr]ole:[[:space:]]*([A-Za-z0-9:_-]+).*/\1/p' | head -1)"
  printf '%s\n' "${role:-$DEFAULT_ROLE}"
}
worktree_dirty() {  # true when $DEV_LOOP_WORKTREES/$1 holds uncommitted or unpushed work
  local t="$DEV_LOOP_WORKTREES/$1"
  [ -e "$t/.git" ] || return 1
  [ -n "$(git -C "$t" status --porcelain 2>/dev/null)" ] && return 0
  [ "$(git -C "$t" rev-list HEAD --not --remotes --count 2>/dev/null)" != 0 ]
}
verdict() {  # "stalled" | "ran" | "unknown" for task $1 since baseline id $2; never fails open
  local now
  [ -n "$2" ] || { echo unknown; return; }  # no baseline read: the stall check cannot judge
  now="$(timeout 120 dl tasks progress "$1" 2>>"$LOG")" || { echo unknown; return; }
  [ -n "$now" ] || { echo unknown; return; }
  [ "${now%% *}" = Done ] && { echo ran; return; }  # Done: the task finished
  # not Done: stalled when nothing was posted, or the worktree still holds unpushed work
  { [ "${now##* }" = "$2" ] || worktree_dirty "$1"; } && echo stalled || echo ran
}
sweep() {  # a lock whose run is gone is swept and its task told; a live run keeps its lock
  local lock
  for lock in "$RUN"/*; do
    [ -d "$lock" ] || continue
    kill -0 "$(cat "$lock/pid" 2>/dev/null)" 2>/dev/null && continue
    rm -rf "$lock"; log "swept orphaned lock ${lock##*/}"
    tell "${lock##*/}" "an earlier run of this task ended without its runner; comment to re-run"
  done
}
enter_worktree() {  # make task $1's own worktree off the base and cd into it; nonzero on failure
  local tree="$DEV_LOOP_WORKTREES/$1"
  [ -d "$tree" ] || git worktree add -q "$tree" "$REMOTE/$DEFAULT_BASE" >>"$LOG" 2>&1 || return 1
  cd "$tree"
}
tag_run() {  # this run's telemetry: DEV_LOOP_TASK always, the OTEL tags only when telemetry is on
  export DEV_LOOP_TASK="$1"
  unset OTEL_RESOURCE_ATTRIBUTES
  if [ "${DEV_LOOP_TELEMETRY:-}" = 1 ] || [ "${CLAUDE_CODE_ENABLE_TELEMETRY:-}" = 1 ]; then
    export OTEL_RESOURCE_ATTRIBUTES="host.name=$DEV_LOOP_WORKER,dev_loop.worker=$DEV_LOOP_WORKER,\
dev_loop.agent=worker,dev_loop.repo=${ORIGIN:-none},dev_loop.task=$1"
  fi
}

work() {  # one task run, holding its lock dir; re-run once on a stall, and on a queued arrival
  local done="$RUN/.done-$1-$BASHPID" rc note="" tries=0 base pg
  exec 9>&-  # the runner's lock stays with the runner, not with its runs
  if ! enter_worktree "$1"; then  # no run starts in the shared checkout
    tell "$1" "could not set up its worktree off $REMOTE/$DEFAULT_BASE; comment to re-run"
    rm -rf "$RUN/$1"; return
  fi
  tag_run "$1"
  # the context gate caps the run at its role and, at 90%, writes a hand-over note here
  export DEV_LOOP_ROLE="$(role_of "$1")" DEV_LOOP_HANDOVER="$RUN/$1/handover"
  while :; do
    clean --when-full  # make room before the run when the worktree root's disk is at the threshold
    base="$(timeout 120 dl tasks progress "$1" 2>>"$LOG")" && base="${base##* }" ||
      { log "task #$1: baseline progress unread"; base=""; }  # empty: the stall check cannot judge
    log "task #$1: start"
    setsid timeout "${TIMEOUT_HOURS}h" claude -p ${DEV_LOOP_MODEL:+--model "$DEV_LOOP_MODEL"} \
      ${MAX_TURNS:+--max-turns "$MAX_TURNS"} --permission-mode bypassPermissions \
      "$note$(brief "$1")" >>"$LOG" 2>&1 &
    pg=$!; wait "$pg"; rc=$?; kill -- -"$pg" 2>/dev/null  # kill the run's whole process group
    log "task #$1: exit $rc"; note=""
    if [ -s "$RUN/$1/handover" ]; then  # a context hand-off: re-run with the note, not a stall
      note="$(handoff_note "$1" "$(cat "$RUN/$1/handover")")"$'\n\n'; rm -f "$RUN/$1/handover"
      tell "$1" "hand-off at the context cap; re-running to continue from $DEV_LOOP_WORKTREES/$1"
      log "task #$1: hand-off; re-running"; continue
    fi
    case "$(verdict "$1" "$base")" in
      unknown)  # the stall check could not read the task: never silent, hand it over
        tell "$1" "run ended (exit $rc) but the stall check could not read the task; needs the coordinator"
        mv "$RUN/$1" "$done"; break ;;
      stalled)
        if [ "$tries" = 1 ]; then  # a second stall: hand it over, no third run
          tell "$1" "stalled twice; needs the coordinator"; mv "$RUN/$1" "$done"; break
        fi
        tries=1; note="$(stall_note "$1" "$rc")"$'\n\n'  # re-run once, prompt opening with the note
        tell "$1" "$(stall_note "$1" "$rc")"; log "task #$1: stalled; re-running once"; continue ;;
    esac
    [ "$rc" = 0 ] || tell "$1" "run ended with exit $rc (124: the timeout)"
    clean  # tidy this task's leftovers before the next
    mv "$RUN/$1" "$done"  # atomic: a later poll finds no lock and starts the task afresh
    [ -e "$done/again" ] && mkdir "$RUN/$1" 2>/dev/null || break
    echo "$BASHPID" >"$RUN/$1/pid"; rm -rf "$done"; tries=0
    log "task #$1: re-run for what arrived while it ran"
  done
  rm -rf "$done"
}

self_update() {  # idle, clean and on integration: fast-forward to origin/$DEV_LOOP_BASE and re-exec
  # a task's lock dir present means a run: not idle (ignore the runner lock and heartbeat files)
  [ "$(ls -A "$RUN" | grep -vcE '^(\.runner\.lock|\.heartbeat)$')" -eq 0 ] || return 0
  local ref="$REMOTE/$DEV_LOOP_BASE" before after
  git rev-parse --verify -q "$ref" >/dev/null 2>&1 || return 0           # ref not fetched: skip
  [ "$(git symbolic-ref --short -q HEAD)" = "$DEV_LOOP_BASE" ] || return 0  # not on integration
  [ -z "$(git status --porcelain)" ] || return 0                        # dirty: never touch work
  before="$(git rev-parse HEAD)"
  git merge --ff-only "$ref" >>"$LOG" 2>&1 || return 0                  # unpushed work: not ff-able
  after="$(git rev-parse HEAD)"
  [ "$before" = "$after" ] && return 0                                   # already current
  log "self-update: fast-forwarded $DEV_LOOP_BASE to ${after:0:12}; re-exec"
  DEV_LOOP_RUNNER_LOCK=held exec bash "$0"  # the new runner keeps the lock it holds
}

mkdir -p "$RUN"
{ [ "${DEV_LOOP_RUNNER_LOCK:-}" = held ] && [ /dev/fd/9 -ef "$RUN/.runner.lock" ]; } ||
  exec 9>"$RUN/.runner.lock"  # trust an inherited fd 9 only on this run dir's lock
unset DEV_LOOP_RUNNER_LOCK  # set only by self_update; no run inherits it
flock -n 9 || { echo "worker $DEV_LOOP_WORKER: another runner holds $RUN" >&2; exit 1; }
cd "$DEV_LOOP_CHECKOUT" || exit 1
git remote get-url "$REMOTE" >/dev/null 2>&1 || REMOTE="$(git remote | head -1)"  # a lone remote
ORIGIN="$(git remote get-url "$REMOTE" 2>/dev/null | sed 's#\.git$##; s#.*[/:]##')"
polls=0
while :; do
  touch "$RUN/.heartbeat"  # mtime only; a watchdog restarts the runner when this goes stale
  sweep                 # dead locks are cleared every poll, never only at start-up
  if timeout 120 git fetch -q "$REMOTE" 2>>"$LOG"; then self_update; else log "fetch failed rc=$?"; fi
  feed="$(timeout 300 dl tasks watch --once 2>>"$LOG")" || log "watch failed rc=$?"
  swept="$(timeout 300 dl tasks sweep "$RUN" "$DEV_LOOP_WORKTREES" "$STALE_HOURS" 2>>"$LOG")" ||
    log "sweep failed rc=$?"  # Doing-no-live-run re-runs and reopened ids join the feed
  feed="$feed"$'\n'"$swept"
  for id in $(sed 's/:.*//' <<<"$feed" | grep -oE '#[0-9]+' | tr -d '#' | sort -un); do
    if ! mkdir "$RUN/$id" 2>/dev/null; then  # running: queue one re-run (its end may race us)
      touch "$RUN/$id/again" 2>/dev/null && { log "#$id: running; re-run queued"; continue; }
      mkdir "$RUN/$id" 2>/dev/null || continue
    fi
    echo "$$" >"$RUN/$id/pid"  # placeholder (runner alive) so the slot-wait sweep keeps it
    while [ "$(ls "$RUN" | wc -l)" -gt "$SLOTS" ]; do sweep; touch "$RUN/.heartbeat"; sleep "${WAIT:-10}"; done
    work "$id" & echo $! >"$RUN/$id/pid" 2>/dev/null
  done
  polls=$((polls + 1))
  [ -n "${WORKER_ONCE:-}" ] && [ "$polls" -ge "$WORKER_ONCE" ] && { wait; exit 0; }
  sleep "$POLL_SECONDS"
done
