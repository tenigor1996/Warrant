"""
agent/outcome.py — What happened in the current incident, as one JSON file.

warrant/state/outcome.json is the hand-off between processes: with the OpenClaw
backend the tools run inside the MCP server, and the :8082 server reads this
file to learn the diagnosis, which actions were proposed, what the policy
decided and whether recovery was verified.

Field names follow contracts/report.json where they overlap, so the report
renderer can build on it.
"""

import fcntl
import json
from pathlib import Path

RECOVERED = "RECOVERED"            # recovery verification ran and passed
NOT_RECOVERED = "NOT_RECOVERED"    # recovery verification ran and failed
UNVERIFIED = "UNVERIFIED"          # recovery verification never ran


def final_status(verification) -> str:
    """The one place a verification result becomes a final status (report, /status, backends)."""
    if not verification:
        return UNVERIFIED
    return RECOVERED if verification.get("recovered") is True else NOT_RECOVERED


class Outcome:
    def __init__(self, path: Path):
        self.path = Path(path)

    def reset(self, incident_id: str, event: dict = None) -> None:
        self._write(lambda _: {"incident_id": incident_id, "event": event or {}, "diagnosis": None,
                               "tools_used": [], "actions_proposed": [], "action_executed": None,
                               "action_executed_at": None, "tests": None, "verification": None})

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text() or "{}")
        except (OSError, ValueError):
            return {}

    def update(self, **fields) -> None:
        self._write(lambda data: {**data, **fields})

    def append(self, key: str, item) -> None:
        self._write(lambda data: {**data, key: [*data.get(key, []), item]})

    def _write(self, change) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                data = json.loads(f.read() or "{}")
            except ValueError:
                data = {}
            f.seek(0)
            f.truncate()
            f.write(json.dumps(change(data), indent=2))
