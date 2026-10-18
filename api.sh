#!/usr/bin/env bash
# Run the PRIDE Map API as a background service: start | stop | restart | status | logs | reap
#
# Why this exists. The API used to be a foreground child of dev.sh on a port chosen by
# find_free_port. Three things followed, and all three were observed on 2026-10-18:
#
#   1. Closing the terminal (or Ctrl-C) killed the API. The web server, started separately,
#      survived — so the UI stayed up and polled an API that no longer existed. Every job then
#      rendered as failed, though the work was running untouched on Modal.
#   2. `kill $API` killed the `uv run` wrapper but not the uvicorn reloader and worker beneath it,
#      so each restart left an orphan reparented to PID 1. A 13h-old one was still resident.
#   3. Those orphans held :8000-:8010, so each restart climbed one port higher. The browser bundle
#      has NEXT_PUBLIC_API_URL baked in at compile time, so a climb silently orphaned open tabs —
#      the UI was built against :8011 while the API answered on :8003.
#
# So this script pins the port, refuses to climb, starts detached, and kills whole process trees.
# Every command is idempotent: `start` on a healthy API is a no-op that exits 0, and `stop` on a
# stopped API is also a no-op that exits 0 — safe to call from any script, in any order, twice.
#
# Running jobs are unaffected by anything here. A remote job lives on Modal and is recovered
# through GET /pipeline/remote/active, which reads Modal's own Dict — so restarting the API and
# reloading the page reattaches to a job already in flight. `status` prints that job.
set -euo pipefail
cd "$(dirname "$0")"

PID_FILE=".api.pid"
LOG_FILE=".api.log"
HEALTH_PATH="/remote-mq"   # a route only this API serves; :8000 is a Docker container that 404s it
START_TIMEOUT=90           # first start also builds the uv environment
LSOF_TIMEOUT=10            # lsof has been seen to take minutes here; never let it block a command

# ---------------------------------------------------------------- port (pinned, never inferred twice)
# One owner for the port: PRIDEMAP_API_PORT wins, else the value the web app was last built with,
# else 8000. Resolving it the same way in every command is what makes them agree with each other
# and with the browser bundle.
recorded_port() {
  [ -f web/.env.local ] || return 1
  sed -n 's/^NEXT_PUBLIC_API_URL=.*:\([0-9][0-9]*\)$/\1/p' web/.env.local | tail -1
}
PORT="${PRIDEMAP_API_PORT:-$(recorded_port || echo 8000)}"
PORT="${PORT:-8000}"
BASE="http://127.0.0.1:$PORT"

# ---------------------------------------------------------------- probes
# Distinguishes OUR API from any other listener on the port. Checking "something answers" is not
# enough: :8000 is answered by a Docker container that also runs uvicorn.
is_ours() {
  curl -fsS -m 3 "$BASE$HEALTH_PATH" 2>/dev/null | grep -q '"available"'
}

# Is anything listening at all? `nc -z` answers in milliseconds; lsof can take minutes on this
# machine, so it is never on a path that merely needs a yes/no.
port_open() {
  nc -z 127.0.0.1 "$PORT" 2>/dev/null
}

# Only called to NAME a holder for a human, never to decide control flow, and always bounded:
# an lsof that hangs must not hang the service script.
port_holder() {
  timeout "$LSOF_TIMEOUT" lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null |
    awk 'NR>1 {print $1" (pid "$2")"; exit}'
}

# Every listening PID, in ONE bounded lsof. Callers filter this set instead of running lsof per
# process — that per-process loop is what hung `reap`.
listening_pids() {
  timeout "$LSOF_TIMEOUT" lsof -nP -iTCP -sTCP:LISTEN -t 2>/dev/null || true
}

# Every descendant of $1, deepest first, so a tree can be killed without orphaning its children.
# This is the part `kill $API` got wrong: uv run -> uvicorn reloader -> worker is three processes.
descendants() {
  local pid=$1 child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    descendants "$child"
  done
  echo "$pid"
}

# TERM the whole tree, then verify and escalate. A stray uvicorn that has stopped servicing its
# event loop ignores SIGTERM: the 13h orphan found on 2026-10-18 survived a TERM and was reported
# as reaped, so "signal sent" is not evidence of death — only a failing kill -0 is.
kill_tree() {
  local pid=$1 p alive
  local tree
  tree=$(descendants "$pid")
  for p in $tree; do kill -TERM "$p" 2>/dev/null || true; done

  for _ in 1 2 3 4 5; do
    alive=0
    for p in $tree; do kill -0 "$p" 2>/dev/null && alive=1; done
    [ "$alive" = 0 ] && return 0
    sleep 1
  done

  for p in $tree; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null || true; done
  sleep 1
  for p in $tree; do
    if kill -0 "$p" 2>/dev/null; then
      echo "   ⚠ pid $p survived SIGKILL — it is stuck in the kernel, not merely ignoring signals" >&2
      return 1
    fi
  done
  return 0
}

running_pid() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && echo "$pid"
}

# ---------------------------------------------------------------- commands
cmd_start() {
  if is_ours; then
    echo "✅ API already healthy on $BASE${PID_FILE:+ (pid $(running_pid 2>/dev/null || echo 'not ours to manage'))}"
    return 0   # idempotent: a second start must not spawn a rival server
  fi

  if port_open; then
    local holder
    holder=$(port_holder || true)
    echo "❌ :$PORT is held by ${holder:-an unidentified process}, which is not this API." >&2
    echo "   Refusing to pick another port — the browser bundle has one baked in, and climbing" >&2
    echo "   is what silently orphaned open tabs before. Free the port, or pin a different one:" >&2
    echo "     PRIDEMAP_API_PORT=<port> $0 start   (then restart the web server so it rebuilds)" >&2
    return 1
  fi

  # Reload OFF by default: the reloader restarts on every file write, and a save that does not
  # import leaves the parent alive with no listening socket — an API that looks up and is not.
  # Opt back in with PRIDEMAP_API_RELOAD=1 while editing.
  echo "🚀 Starting API on $BASE (reload=${PRIDEMAP_API_RELOAD:-0}, log: $LOG_FILE)"
  PORT="$PORT" PRIDEMAP_API_RELOAD="${PRIDEMAP_API_RELOAD:-0}" \
    nohup uv run api/main.py >>"$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"
  disown "$pid" 2>/dev/null || true

  local waited=0
  until is_ours; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "❌ API exited during startup. Last lines of $LOG_FILE:" >&2
      tail -20 "$LOG_FILE" >&2 || true
      rm -f "$PID_FILE"
      return 1
    fi
    if [ "$waited" -ge "$START_TIMEOUT" ]; then
      echo "❌ API did not answer $BASE$HEALTH_PATH within ${START_TIMEOUT}s. Last lines:" >&2
      tail -20 "$LOG_FILE" >&2 || true
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done

  echo "✅ API healthy on $BASE (pid $pid, survives this terminal closing)"
  record_url
}

# Keep the web app's compile-time constant in step with the port actually serving.
record_url() {
  local url="http://localhost:$PORT"
  if [ -f web/.env.local ] && grep -q "^NEXT_PUBLIC_API_URL=$url$" web/.env.local; then
    return 0   # already correct — do not rewrite and make the file look changed
  fi
  if [ -f web/.env.local ]; then
    grep -v '^NEXT_PUBLIC_API_URL=' web/.env.local > web/.env.local.tmp || true
    mv web/.env.local.tmp web/.env.local
  fi
  echo "NEXT_PUBLIC_API_URL=$url" >> web/.env.local
  echo "📝 web/.env.local → NEXT_PUBLIC_API_URL=$url"
  echo "   Restart the web server if it is already running: it bakes this in at compile time."
}

cmd_stop() {
  local pid stopped=0
  if pid=$(running_pid); then
    kill_tree "$pid"
    stopped=1
  fi
  # Also take anything still listening on the port, so a server started by another route (dev.sh,
  # a bare `uv run api/main.py`) does not survive a stop and get mistaken for this one later.
  if port_open; then
    local listener
    for listener in $(timeout "$LSOF_TIMEOUT" lsof -nP -ti TCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true); do
      kill_tree "$listener"
      stopped=1
    done
  fi
  rm -f "$PID_FILE"
  if [ "$stopped" = 1 ]; then
    echo "🛑 API stopped (:$PORT)"
  else
    echo "✅ API already stopped (:$PORT)"   # idempotent: nothing to do is success
  fi
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start
}

# Orphans from the old dev.sh lifecycle: an api/main.py whose parent died, holding no socket.
# They are harmless until they hold a port, at which point the next start climbs past it.
cmd_reap() {
  local reaped=0 failed=0 pid ppid serving managed child
  serving=" $(listening_pids | tr '\n' ' ') "   # one bounded lsof, then membership tests
  managed=$(running_pid 2>/dev/null || echo "")

  while read -r pid ppid; do
    [ -n "${pid:-}" ] || continue
    [ "$ppid" = 1 ] || continue                  # only orphans, never a live child of some runner
    [ "$pid" = "$managed" ] && continue          # never the server this script is managing

    # Skip if ANYTHING in this process's tree is listening. The listener is the uvicorn child, not
    # the `uv run` parent, so testing only $pid would have reaped the live server's own parent and
    # taken the API down with it — detached servers also have PPID 1.
    local tree_serves=0
    for child in $(descendants "$pid"); do
      case "$serving" in *" $child "*) tree_serves=1 ;; esac
    done
    [ "$tree_serves" = 1 ] && continue

    echo "   reaping orphan pid $pid (parent died, nothing in its tree is listening)"
    if kill_tree "$pid"; then reaped=$((reaped + 1)); else failed=$((failed + 1)); fi
  done < <(pgrep -f "api/main.py" 2>/dev/null | xargs -I{} ps -o pid=,ppid= -p {} 2>/dev/null || true)

  if [ "$failed" -gt 0 ]; then
    echo "❌ $reaped reaped, $failed could not be killed — see warnings above" >&2
    return 1
  fi
  [ "$reaped" -gt 0 ] && echo "🧹 $reaped orphan(s) reaped" || echo "✅ no orphaned API processes"
  return 0
}

cmd_status() {
  echo "port        : $PORT"
  echo "pid file    : $(running_pid 2>/dev/null || echo 'none')"
  # Report the port from the fast probe, and only name the holder if lsof actually answers —
  # printing "nothing listening" next to a healthy API is the kind of contradiction that sent this
  # whole investigation after the wrong layer twice.
  if port_open; then
    echo "listener    : :$PORT is open — $(port_holder || echo 'holder unidentified (lsof slow)')"
  else
    echo "listener    : nothing listening on :$PORT"
  fi
  if is_ours; then
    echo "health      : ✅ this API is answering on $BASE"
  else
    echo "health      : ❌ not answering $BASE$HEALTH_PATH"
  fi
  echo "web expects : $(recorded_port >/dev/null 2>&1 && echo ":$(recorded_port)" || echo 'unrecorded')"
  if [ "$(recorded_port 2>/dev/null || echo "$PORT")" != "$PORT" ]; then
    echo "            ⚠ the web bundle was built for a different port — restart the web server"
  fi
  # A job in flight lives on Modal, not here. Printing it is the answer to "did I lose my run?".
  # The full reply embeds the whole run log and every stage, which is unreadable here — print only
  # the two fields that answer "is my run still there?". `status logs` and the UI show the rest.
  if is_ours; then
    local acc=${PRIDEMAP_STATUS_ACCESSION:-PXD046787} reply job state
    reply=$(curl -fsS -m 20 "$BASE/dataset/$acc/pipeline/remote/active" 2>/dev/null || true)
    # Pulled out with sed rather than a nested python -c: the quoting in an inline f-string that
    # also needs shell escaping is where the first attempt silently printed nothing.
    job=$(printf '%s' "$reply" | sed -n 's/.*"job_id":[ ]*"\([^"]*\)".*/\1/p')
    state=$(printf '%s' "$reply" | sed -n 's/.*"status":[ ]*"\([^"]*\)".*/\1/p')
    if [ -n "$job" ]; then
      echo "remote job  : $job — ${state:-unknown} ($acc)"
    else
      echo "remote job  : none tracked for $acc"
    fi
  fi
}

cmd_logs() { tail -f "$LOG_FILE"; }

case "${1:-status}" in
  start)   cmd_start   ;;
  stop)    cmd_stop    ;;
  restart) cmd_restart ;;
  status)  cmd_status  ;;
  logs)    cmd_logs    ;;
  reap)    cmd_reap    ;;
  *) echo "usage: $0 {start|stop|restart|status|logs|reap}" >&2; exit 2 ;;
esac
