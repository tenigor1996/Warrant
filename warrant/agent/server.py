"""
agent/server.py — Agent HTTP API (port 8082).

    POST /investigate   incident event from the watcher; starts an investigation
                        in the background and returns 202 immediately
    GET  /status        idle | investigating | diagnosed | resolved | inconclusive | failed
                        ("resolved" = diagnosed, remediated and recovery verified);
                        "stage" says how far a running incident has got
    GET  /diagnosis     latest diagnosis (available as soon as it is submitted)
    GET  /outcome       actions proposed, policy verdicts, tests, verification

When an incident ends (after the last verification, or when the run gives up)
the report is written to state/report.json; see report.py.

One investigation at a time; a second POST while one runs gets 409.

The model runtime is chosen by WARRANT_AGENT_BACKEND (see backends.py):
"openclaw" (default) or "direct".

Run from the repo root:
    python -m warrant.agent.server
"""

import json
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from warrant.agent import backends, config
from warrant.agent.events import EventStream
from warrant.agent.outcome import Outcome
from warrant.agent.report import generate_incident_report

_lock = threading.Lock()
_state = {"state": "idle", "incident_id": None, "started_at": None, "diagnosis": None,
          "backend": config.BACKEND, "remediation": None}


def _run(event, backend=None):
    stream = EventStream(config.EVENTS_FILE)
    stream.start_new()
    outcome = Outcome(config.OUTCOME_FILE)
    try:
        outcome.reset(event["incident_id"], event)
        config.DIAGNOSIS_FILE.unlink(missing_ok=True)
        config.REPORT_FILE.unlink(missing_ok=True)  # the previous incident's report
        backend = backend or backends.get_backend()
        result = backend.run(event, stream, outcome)
        remediation = result.get("remediation")
        diagnosis = {k: v for k, v in result.items() if k != "remediation"}
        config.DIAGNOSIS_FILE.write_text(json.dumps(diagnosis, indent=2))
        resolved = bool(remediation) and remediation.get("status") == "resolved"
        try:  # the incident is over; a report problem must not change how it ended
            generate_incident_report(outcome, stream)
        except Exception:
            traceback.print_exc()
            stream.emit("report", "status", "Incident report not generated", "see the agent log")
        with _lock:
            _state.update(state="resolved" if resolved else diagnosis["status"], diagnosis=diagnosis,
                          remediation=remediation)
    except Exception:
        traceback.print_exc()
        with _lock:
            _state.update(state="failed")


def _snapshot() -> dict:
    """Current state, filled in from the outcome file while an incident is still running."""
    with _lock:
        snapshot = dict(_state)
    if snapshot["state"] == "investigating":
        data = Outcome(config.OUTCOME_FILE).load()
        if data.get("incident_id") == snapshot["incident_id"]:
            snapshot["diagnosis"] = data.get("diagnosis")
            snapshot["stage"] = ("verify" if data.get("action_executed") else
                                 "remediate" if data.get("diagnosis") else "investigate")
    return snapshot


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/investigate":
            return self._send(404, {"error": "not found"})
        try:
            event = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if not isinstance(event, dict):
                raise ValueError("event must be a JSON object")
        except ValueError as exc:
            return self._send(400, {"error": f"invalid event: {exc}"})

        with _lock:
            if _state["state"] == "investigating":
                return self._send(409, {"error": "investigation already running",
                                        "incident_id": _state["incident_id"]})
            event.setdefault("incident_id", datetime.now(timezone.utc).strftime("INC-%Y%m%d-%H%M%S"))
            _state.update(state="investigating", incident_id=event["incident_id"], diagnosis=None,
                          remediation=None,
                          started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        threading.Thread(target=_run, args=(event,), daemon=True).start()
        self._send(202, {"accepted": True, "incident_id": event["incident_id"]})

    def do_GET(self):
        snapshot = _snapshot()
        if self.path == "/status":
            return self._send(200, snapshot)
        if self.path == "/diagnosis":
            if snapshot["diagnosis"] is None:
                return self._send(404, {"error": "no diagnosis yet"})
            return self._send(200, snapshot["diagnosis"])
        if self.path == "/outcome":
            return self._send(200, Outcome(config.OUTCOME_FILE).load())
        self._send(404, {"error": "not found"})

    def do_OPTIONS(self):
        self._send(204, None)

    def _send(self, code, body):
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass


def main():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", config.PORT), Handler)
    backend = backends.get_backend()  # an unknown backend name stops the server here
    print(f"[AGENT] listening on :{config.PORT}, backend {backend.name}: {backend.describe()}", flush=True)
    try:
        backend.check()
    except backends.BackendUnavailable as exc:
        print(f"[AGENT] WARNING: {exc}\n[AGENT] incidents will end 'inconclusive' until this is fixed.",
              flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
