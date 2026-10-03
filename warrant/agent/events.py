"""
agent/events.py — Append-only investigation event stream (contracts/event.json).

Every major agent step becomes one JSON line in warrant/state/events.jsonl,
which the dashboard tails to draw the live timeline.

Two processes write the same file when the OpenClaw backend is in use (the
:8082 server and the MCP server OpenClaw spawns), so each append takes a file
lock and numbers itself from what is already in the file.
"""

import fcntl
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path


class EventStream:
    def __init__(self, path: Path, echo=None):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.seq = 0
        # Where the console echo goes. The MCP server passes sys.stderr because
        # its stdout carries the MCP protocol.
        self.echo = echo

    def start_new(self) -> None:
        """Begin a fresh timeline for a new incident."""
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("")
            self.seq = 0

    def emit(self, phase, kind, title, detail="", duration_ms=None,
             policy_verdict=None, policy_reason=None) -> dict:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.seek(0)
                self.seq = sum(1 for line in f if line.strip()) + 1
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
                f.seek(0, 2)
                f.write(json.dumps(event) + "\n")
            print(f"[AGENT] {phase:<11} {kind:<15} {title}", flush=True, file=self.echo or sys.stdout)
            return event
