#!/usr/bin/env bash
# start_demo.sh — what the "Warrant Demo" icon runs.
#
#   1. reset_demo.sh   clean, healthy state
#   2. run_all.sh      sim, agent, watcher, dashboard (stays in this window)
#   3. opens the dashboard in the browser once it is up
#
# The incident is NOT started here: press "Trigger Demo Incident" on the
# dashboard (or POST :8081/demo/trigger). Close this window or Ctrl+C to stop.
#
# First run: creates .venv and installs the Python packages (again only when a
# requirements file changes).
#
# Settings: a desktop icon does not see variables from ~/.bashrc, so put them in
# warrant/scripts/demo.env (not committed; see demo.env.example).
#
# Overrides (env or demo.env): DASHBOARD_URL, DASHBOARD_OPEN_CMD (command that
# opens a URL), plus everything run_all.sh accepts.

set -uo pipefail

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPTS/../.." && pwd)"

if [ -f "$SCRIPTS/demo.env" ]; then
  set -a; . "$SCRIPTS/demo.env"; set +a
  echo "[START] settings loaded from warrant/scripts/demo.env"
fi

# Python environment: create .venv and install packages when needed.
ensure_python() {
  [ -n "${PYTHON:-}" ] && return 0  # caller chose an interpreter
  local venv="$ROOT/.venv" reqs=() f want have
  for f in "$ROOT/warrant/sim/requirements.txt" "$ROOT/warrant/agent/requirements.txt"; do
    [ -f "$f" ] && reqs+=("$f")
  done
  if [ ! -x "$venv/bin/python" ]; then
    echo "[START] first run: creating Python environment in .venv"
    python3 -m venv "$venv" || {
      echo "[START] could not create .venv (on Ubuntu: sudo apt install python3-venv)"; return 1; }
  fi
  want="$(cat "${reqs[@]}" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
  have="$(cat "$venv/.warrant-requirements" 2>/dev/null || true)"
  if [ "$want" != "$have" ]; then
    echo "[START] installing Python packages (first run or requirements changed)..."
    local args=() 
    for f in "${reqs[@]}"; do args+=(-r "$f"); done
    "$venv/bin/pip" install -q "${args[@]}" || { echo "[START] package install failed"; return 1; }
    echo "$want" > "$venv/.warrant-requirements"
    echo "[START] packages installed"
  fi
}

DASHBOARD_URL="${DASHBOARD_URL:-http://localhost:8083/ui/}"

if [ -z "${DASHBOARD_OPEN_CMD:-}" ]; then
  if command -v xdg-open >/dev/null 2>&1; then DASHBOARD_OPEN_CMD="xdg-open"
  elif command -v open >/dev/null 2>&1; then DASHBOARD_OPEN_CMD="open"
  else DASHBOARD_OPEN_CMD=""
  fi
fi

echo "[START] Warrant demo"

# Already running (e.g. the icon was double-clicked twice)? Do not reset it:
# a reset would wipe the live run. Just bring the dashboard back up.
port_busy() { (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
busy=""
for port in 8081 8082 8083; do port_busy "$port" && busy="$busy $port"; done
if [ -n "$busy" ]; then
  if curl -fsS -m 2 http://localhost:8081/health >/dev/null 2>&1 \
     && curl -fsS -m 2 http://localhost:8082/status >/dev/null 2>&1; then
    echo "[START] the demo is already running — not resetting it."
    if curl -fsS -m 2 "$DASHBOARD_URL" >/dev/null 2>&1 && [ -n "$DASHBOARD_OPEN_CMD" ]; then
      $DASHBOARD_OPEN_CMD "$DASHBOARD_URL" >/dev/null 2>&1 &
      echo "[START] dashboard opened: $DASHBOARD_URL"
    fi
    echo "[START] to restart from scratch, close the demo's other window first."
    sleep 4
    exit 0
  fi
  echo "[START] port(s)$busy already in use by something else. Stop it, then start again."
  read -r -p "Press Enter to close." _
  exit 1
fi

ensure_python || { read -r -p "Press Enter to close." _; exit 1; }
"$SCRIPTS/reset_demo.sh" || { echo "[START] reset failed"; read -r -p "Press Enter to close." _; exit 1; }

# Open the dashboard as soon as it answers (gives up after 60s).
(
  for _ in $(seq 1 240); do
    if curl -fsS -m 1 "$DASHBOARD_URL" >/dev/null 2>&1; then
      if [ -n "$DASHBOARD_OPEN_CMD" ]; then
        $DASHBOARD_OPEN_CMD "$DASHBOARD_URL" >/dev/null 2>&1 &
        echo "[START] dashboard opened: $DASHBOARD_URL"
      else
        echo "[START] open the dashboard: $DASHBOARD_URL"
      fi
      exit 0
    fi
    sleep 0.25
  done
  echo "[START] dashboard did not come up; check the messages above"
) &
OPENER=$!
trap 'kill "$OPENER" 2>/dev/null' EXIT

"$SCRIPTS/run_all.sh"
status=$?

# Keep the window open after a failure so the error can be read.
if [ "$status" -ne 0 ]; then
  read -r -p "[START] stopped with an error. Press Enter to close." _
fi
exit "$status"
