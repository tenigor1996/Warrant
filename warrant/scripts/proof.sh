#!/usr/bin/env bash
# proof.sh — answer "is this real or staged?" live, in front of a judge.
#
# Shows, straight from the source:
#   1. the checkout service's real git history
#   2. the most recent change to its config file, as a diff
#   3. the config the running service is using right now
#   4. the service's own test suite, run live
#   5. live metrics from the running service
#   6. raw log lines from the service
#
# Run it during the incident (tests fail) and again after the fix (tests pass).
#
# Usage (from anywhere):  warrant/scripts/proof.sh

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
STATE="${WARRANT_STATE_DIR:-$ROOT/warrant/state}"
REPO="$STATE/checkout-service"
CONFIG="checkout_service/config.py"
SIM_URL="${CHECKOUT_URL:-http://localhost:8081}"

section() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

if [ ! -d "$REPO/.git" ]; then
  echo "No checkout-service repo at $REPO. Run warrant/scripts/reset_demo.sh first."
  exit 1
fi

section "1. Git history of the checkout service ($REPO)"
git -C "$REPO" log --format='%h  %ad  %<(16)%an %s' --date=format:'%b %d %H:%M'

section "2. Most recent change to $CONFIG"
git -C "$REPO" log -1 --format='%h  %an  %ad%n%n    %s%n' --date=format:'%b %d %H:%M' -- "$CONFIG"
git -C "$REPO" log -1 -p --format= --color=always -- "$CONFIG" | sed -n '/^.\{0,10\}@@/,$p'

section "3. Config the service is running with right now"
grep -E '^PAYMENT_' "$REPO/$CONFIG"

section "4. The service's own test suite, run live"
(cd "$REPO" && "$PY" -m pytest -q -rf --tb=no -p no:logging --color=yes 2>&1 | grep -vE '^\s*$|short test summary')

section "5. Live metrics ($SIM_URL/metrics)"
if metrics="$(curl -fsS -m 2 "$SIM_URL/metrics" 2>/dev/null)"; then
  echo "$metrics" | "$PY" -m json.tool
else
  echo "checkout service not running"
fi

section "6. Raw log lines ($STATE/app.log)"
if [ -f "$STATE/app.log" ]; then
  timeouts=$(grep -c 'PaymentTimeout' "$STATE/app.log" || true)
  failed=$(grep -c 'checkout failed' "$STATE/app.log" || true)
  echo "PaymentTimeout lines: $timeouts    checkout failed lines: $failed"
  echo
  grep -E '^[0-9]{4}-' "$STATE/app.log" | grep -E 'deploy|config|retry attempt|checkout failed|checkout completed' | tail -n 6
else
  echo "no log yet"
fi
echo
