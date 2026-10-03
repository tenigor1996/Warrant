"""
agent/events.py — Append-only investigation event stream (contracts/event.json).

Every major agent step becomes one JSON line in warrant/state/events.jsonl,
which the dashboard tails to draw the live timeline.
"""

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class EventStream:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.seq = 0

    def start_new(self) -> None:
        """Begin a fresh timeline for a new incident."""
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("")
            self.seq = 0

    def emit(self, phase, kind, title, detail="", duration_ms=None,
             policy_verdict=None, policy_reason=None) -> dict:
        with self.lock:
            self.seq += 1
            event = {
                "seq": self.seq,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "phase": phase,
                "kind": kind,
                "title": title,
                "detail": detail,
                "policy_verdict": policy_verdict,
                "policy_reason": policy_reason,
                "duration_ms": duration_ms,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(event) + "\n")
            print(f"[AGENT] {phase:<11} {kind:<15} {title}", flush=True)
            return event
