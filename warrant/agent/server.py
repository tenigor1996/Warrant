"""
agent/server.py — Agent HTTP API (port 8082).

    POST /investigate   incident event from the watcher; starts an investigation
                        in the background and returns 202 immediately
    GET  /status        idle | investigating | diagnosed | inconclusive | failed
    GET  /diagnosis     latest diagnosis

One investigation at a time; a second POST while one runs gets 409.

Run from the repo root:
    python -m warrant.agent.server
"""

import json
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from warrant.agent import config
from warrant.agent.agent import investigate
from warrant.agent.events import EventStream

_lock = threading.Lock()
_state = {"state": "idle", "incident_id": None, "started_at": None, "diagnosis": None}


def _run(event):
    stream = EventStream(config.EVENTS_FILE)
    stream.start_new()
    try:
        diagnosis = investigate(event, events=stream)
        config.DIAGNOSIS_FILE.write_text(json.dumps(diagnosis, indent=2))
        with _lock:
            _state.update(state=diagnosis["status"], diagnosis=diagnosis)
    except Exception:
        traceback.print_exc()
        with _lock:
            _state.update(state="failed")


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
                          started_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        threading.Thread(target=_run, args=(event,), daemon=True).start()
        self._send(202, {"accepted": True, "incident_id": event["incident_id"]})

    def do_GET(self):
        with _lock:
            snapshot = dict(_state)
        if self.path == "/status":
            return self._send(200, snapshot)
        if self.path == "/diagnosis":
            if snapshot["diagnosis"] is None:
                return self._send(404, {"error": "no diagnosis yet"})
            return self._send(200, snapshot["diagnosis"])
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
    print(f"[AGENT] listening on :{config.PORT}, model at {config.LLM_BASE_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
