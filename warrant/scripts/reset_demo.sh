#!/usr/bin/env bash
# reset_demo.sh — put the demo back to a clean, healthy state.
#
# Run before every rehearsal and before judging. Safe to run whether or not
# the services are up:
#   - checkout service running  -> POST /demo/reset (rebuilds repo, clears logs/metrics)
#   - checkout service stopped  -> rebuilds the checkout-service repo directly
# Either way, deletes the agent's previous timeline, diagnosis and report.
#
# Usage (from anywhere):  warrant/scripts/reset_demo.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
STATE="${WARRANT_STATE_DIR:-$ROOT/warrant/state}"
SIM_URL="${CHECKOUT_URL:-http://localhost:8081}"
AGENT_URL="${AGENT_URL:-http://localhost:8082}"

say() { echo "[RESET] $*"; }

# Warn if the agent is mid-investigation: it will keep writing to the timeline.
if status="$(curl -fsS -m 2 "$AGENT_URL/status" 2>/dev/null)"; then
  case "$status" in
    *'"investigating"'*) say "WARNING: agent is mid-investigation; its events will reappear. Restart the agent for a clean run." ;;
  esac
fi

# If the service answers /health but the reset call fails (e.g. it is shutting
# down right now), fall through to the offline rebuild.
WATCHER_LOG="$STATE/logs/watcher.log"
watcher_log_start=0
[ -f "$WATCHER_LOG" ] && watcher_log_start=$(wc -c < "$WATCHER_LOG" | tr -d ' ')

if curl -fsS -m 2 "$SIM_URL/health" >/dev/null 2>&1 \
   && curl -fsS -m 30 -X POST "$SIM_URL/demo/reset" >/dev/null 2>&1; then
  say "checkout service reset (running at $SIM_URL)"
else
  WARRANT_STATE_DIR="$STATE" "$PY" -c "
import os
from pathlib import Path
from warrant.sim import workspace
state = Path(os.environ['WARRANT_STATE_DIR'])
workspace.build(state / 'checkout-service')
for name in ('app.log', 'metrics.jsonl', 'health.json'):
    (state / name).unlink(missing_ok=True)
"
  say "checkout service not running or not responding; rebuilt repo and cleared logs/metrics in $STATE"
fi

rm -f "$STATE/events.jsonl" "$STATE/diagnosis.json" "$STATE/outcome.json" "$STATE/report.json"
say "cleared agent timeline, diagnosis, outcome and report"

# Verify.
WARRANT_STATE_DIR="$STATE" "$PY" -c "
import os, sys
from pathlib import Path
from warrant.sim import workspace
ws = Path(os.environ['WARRANT_STATE_DIR']) / 'checkout-service'
c = workspace.read_config(ws)
print(f\"[RESET] config: PAYMENT_TIMEOUT = {c['PAYMENT_TIMEOUT']}, PAYMENT_RETRIES = {c['PAYMENT_RETRIES']}\")
sys.exit(1 if workspace.is_incident(ws) else 0)
" || { say "FAILED: config is not healthy"; exit 1; }

if health="$(curl -fsS -m 2 "$SIM_URL/health" 2>/dev/null)"; then
  say "health: $health"
fi
# The agent keeps its last result in memory; only a restart or the next incident clears it.
if status="$(curl -fsS -m 2 "$AGENT_URL/status" 2>/dev/null)"; then
  last="$(echo "$status" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("state", ""))' 2>/dev/null)"
  case "$last" in
    idle|investigating|"") ;;
    *) say "note: agent /status still shows the last incident ($last) until it is restarted or a new incident starts." ;;
  esac
fi
# A watcher still in "incident active" mode ignores a new trigger until it has
# seen ~3 healthy readings. Wait for that, so the next trigger is always caught.
if pgrep -f "warrant.watcher.watcher" >/dev/null 2>&1; then
  say "waiting for the watcher to re-arm..."
  armed=""
  if [ -f "$WATCHER_LOG" ]; then
    for _ in $(seq 1 40); do
      new="$(tail -c +"$((watcher_log_start + 1))" "$WATCHER_LOG" 2>/dev/null || true)"
      case "$new" in *"service healthy"*|*"re-armed"*) armed=yes; break ;; esac
      sleep 0.5
    done
  else
    sleep 8; armed=yes  # watcher started by hand, no log to read
  fi
  [ -n "$armed" ] && say "watcher armed" || say "WARNING: watcher did not confirm it is armed; wait a few seconds before triggering"
fi
say "ready — healthy. You can trigger the incident now."
