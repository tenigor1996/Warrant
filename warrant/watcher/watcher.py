"""
watcher/watcher.py — Always-on health watcher.

    MONITOR -> DETECT -> CREATE EVENT -> TRIGGER AGENT

Polls the checkout service's /health every POLL_INTERVAL_SECONDS. When
failure_rate stays above FAILURE_THRESHOLD for CONSECUTIVE_BAD_CHECKS polls,
it builds one incident event and POSTs it to the agent's /investigate.

It fires once per incident: while an incident is active it does not trigger
again. It re-arms after failure_rate stays below RECOVERY_THRESHOLD for
CONSECUTIVE_GOOD_CHECKS polls. If the agent is unreachable, the same event is
retried on later polls until it is delivered.

The watcher does not investigate, diagnose, or change anything.

Run from the repo root:
    python -m warrant.watcher.watcher
"""

import json
import time
import urllib.request
from datetime import datetime, timezone

from warrant.watcher import config


def log(message: str) -> None:
    print(f"[WATCHER] {message}", flush=True)


def fetch_health(url: str = config.CHECKOUT_HEALTH_URL) -> dict:
    with urllib.request.urlopen(url, timeout=config.HTTP_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read())


def post_event(event: dict, url: str = config.AGENT_INVESTIGATE_URL) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(event).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.HTTP_TIMEOUT_SECONDS) as resp:
        resp.read()


class Watcher:
    def __init__(self, fetch=fetch_health, send=post_event, clock=time.time):
        self.fetch = fetch
        self.send = send
        self.clock = clock
        self.incident_active = False
        self.bad_streak = 0
        self.good_streak = 0
        self.pending_event = None  # detected but not yet delivered to the agent
        self.incidents_detected = 0

    def poll_once(self) -> None:
        """One poll. Never raises."""
        try:
            health = self.fetch()
            failure_rate = float(health["failure_rate"])
        except Exception as exc:
            log(f"health check failed: {exc!r}")
            return

        if failure_rate > config.FAILURE_THRESHOLD:
            self._on_degraded(health, failure_rate)
        else:
            self._on_not_degraded(failure_rate)

        if self.pending_event is not None:
            self._deliver()

    def _on_degraded(self, health: dict, failure_rate: float) -> None:
        self.good_streak = 0
        if self.incident_active:
            log(f"incident already active, not triggering again: failure_rate={failure_rate:.2f}")
            return
        self.bad_streak += 1
        log(f"degraded reading {self.bad_streak}/{config.CONSECUTIVE_BAD_CHECKS}: failure_rate={failure_rate:.2f}")
        if self.bad_streak >= config.CONSECUTIVE_BAD_CHECKS:
            self.incident_active = True
            self.bad_streak = 0
            self.incidents_detected += 1
            self.pending_event = self._make_event(health)
            log(f"INCIDENT DETECTED: {self.pending_event['incident_id']}")

    def _on_not_degraded(self, failure_rate: float) -> None:
        self.bad_streak = 0
        if not self.incident_active:
            log(f"service healthy: failure_rate={failure_rate:.2f}")
            return
        if failure_rate < config.RECOVERY_THRESHOLD:
            self.good_streak += 1
            log(f"recovery reading {self.good_streak}/{config.CONSECUTIVE_GOOD_CHECKS}: failure_rate={failure_rate:.2f}")
            if self.good_streak >= config.CONSECUTIVE_GOOD_CHECKS:
                self.incident_active = False
                self.good_streak = 0
                self.pending_event = None
                log("service recovered, watcher re-armed")
        else:
            self.good_streak = 0
            log(f"incident active, service not yet recovered: failure_rate={failure_rate:.2f}")

    def _make_event(self, health: dict) -> dict:
        now = self.clock()
        stamp = datetime.fromtimestamp(now, timezone.utc)
        return {
            "incident_id": f"INC-{stamp:%Y%m%d-%H%M%S}",
            "event_type": "service_degradation",
            "service": config.SERVICE_NAME,
            "detected_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": health.get("status"),
            "failure_rate": health.get("failure_rate"),
            "latency_p95_ms": health.get("latency_p95_ms"),
            "requests_last_minute": health.get("requests_last_minute"),
            "errors_last_minute": health.get("errors_last_minute"),
        }

    def _deliver(self) -> None:
        try:
            self.send(self.pending_event)
        except Exception as exc:
            log(f"could not reach agent, will retry next poll: {exc!r}")
            return
        log(f"investigation event sent: {self.pending_event['incident_id']}")
        self.pending_event = None


def main() -> None:
    log(f"watching {config.CHECKOUT_HEALTH_URL} every {config.POLL_INTERVAL_SECONDS:g}s "
        f"-> {config.AGENT_INVESTIGATE_URL}")
    watcher = Watcher()
    while True:
        watcher.poll_once()
        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
