#!/usr/bin/env bash
# run_all.sh — start every Warrant process in order. Ctrl+C stops them all.
#
#   0. check the local model (vLLM, managed by NemoClaw — not started here)
#   1. checkout service  :8081   python -m warrant.sim.app
#   2. agent             :8082   python -m warrant.agent.server
#   3. watcher                   python -m warrant.watcher.watcher
#   4. dashboard         :8083   DASHBOARD_CMD (skipped until warrant/ui/index.html exists)
#
# Logs go to warrant/state/logs/<name>.log. Run reset_demo.sh first for a clean demo.
#
# Overrides (env): PYTHON, LLM_BASE_URL, LLM_MODEL, AGENT_CMD, DASHBOARD_CMD
#
# Usage (from anywhere):  warrant/scripts/run_all.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
STATE="${WARRANT_STATE_DIR:-$ROOT/warrant/state}"
LOGS="$STATE/logs"
mkdir -p "$LOGS"

export LLM_BASE_URL="${LLM_BASE_URL:-http://localhost:8000/v1}"
QPY="$(printf '%q' "$PY")"   # paths may contain spaces
SIM_CMD="$QPY -m warrant.sim.app"
WATCHER_CMD="$QPY -m warrant.watcher.watcher"
AGENT_CMD="${AGENT_CMD:-$QPY -m warrant.agent.server}"
# Serves warrant/ so the page at /ui/ can fetch /state/*.json and /fixtures/*.
DASHBOARD_CMD="${DASHBOARD_CMD:-$QPY -m http.server 8083 --bind 127.0.0.1 --directory $(printf '%q' "$ROOT/warrant")}"

NAMES=()
PIDS=()

say() { echo "[RUN] $*"; }

port_busy() {
  "$PY" -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$1))==0 else 1)"
}

stop_all() {
  trap - INT TERM EXIT
  echo
  say "stopping..."
  local i
  if [ "${#PIDS[@]}" -gt 0 ]; then
    for i in "${!PIDS[@]}"; do
      kill "${PIDS[$i]}" 2>/dev/null && say "stopped ${NAMES[$i]}"
    done
  fi
  wait 2>/dev/null
  exit "${1:-0}"
}
trap 'stop_all 0' INT TERM
trap 'stop_all $?' EXIT

start() {  # start <name> <command string>
  local name="$1"
  bash -c "exec $2" >"$LOGS/$name.log" 2>&1 &
  NAMES+=("$name")
  PIDS+=("$!")
}

wait_http() {  # wait_http <name> <url> <seconds>
  local i
  for i in $(seq 1 $(( $3 * 4 ))); do
    curl -fsS -m 1 "$2" >/dev/null 2>&1 && { say "$1 up ($2)"; return 0; }
    kill -0 "${PIDS[${#PIDS[@]}-1]}" 2>/dev/null || break
    sleep 0.25
  done
  say "FAILED: $1 did not come up. Last log lines ($LOGS/$1.log):"
  tail -n 15 "$LOGS/$1.log"
  exit 1
}

for port in 8081 8082 8083; do
  if port_busy "$port"; then
    say "FAILED: port $port is already in use (already running?). Stop it first."
    trap - EXIT
    exit 1
  fi
done

# 0. local model
if models="$(curl -fsS -m 3 "$LLM_BASE_URL/models" 2>/dev/null)"; then
  say "local model up at $LLM_BASE_URL: $(echo "$models" | "$PY" -c 'import json,sys; print(", ".join(m["id"] for m in json.load(sys.stdin).get("data", [])))')"
else
  say "WARNING: no model at $LLM_BASE_URL — investigations will end 'inconclusive' until it is up."
fi

# 1-4
start sim "$SIM_CMD"
wait_http sim http://localhost:8081/health 30

start agent "$AGENT_CMD"
wait_http agent http://localhost:8082/status 30

start watcher "$WATCHER_CMD"
sleep 1
kill -0 "${PIDS[${#PIDS[@]}-1]}" 2>/dev/null || { say "FAILED: watcher exited:"; tail -n 15 "$LOGS/watcher.log"; exit 1; }
say "watcher up (polling :8081/health every 2s)"

if [ -f "$ROOT/warrant/ui/index.html" ]; then
  start dashboard "$DASHBOARD_CMD"
  wait_http dashboard http://localhost:8083/ui/ 15
  say "dashboard: http://localhost:8083/ui/"
else
  say "dashboard skipped: warrant/ui/index.html not there yet"
fi

echo
say "all running. Logs: $LOGS/"
say "trigger the incident:  curl -X POST localhost:8081/demo/trigger"
say "Ctrl+C to stop everything."

# Stay in the foreground; if any process dies, report it and stop the rest.
while true; do
  for i in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
      say "${NAMES[$i]} exited unexpectedly. Last log lines ($LOGS/${NAMES[$i]}.log):"
      tail -n 15 "$LOGS/${NAMES[$i]}.log"
      stop_all 1
    fi
  done
  sleep 2
done
