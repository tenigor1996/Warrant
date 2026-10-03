"""Shared fixtures: an incident repo, an isolated state dir and a stand-in checkout service."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from warrant.agent import config
from warrant.agent.events import EventStream
from warrant.agent.outcome import Outcome
from warrant.sim import workspace

DEGRADED = {"status": "degraded", "failure_rate": 0.68, "latency_p95_ms": 3184}
HEALTHY = {"status": "healthy", "failure_rate": 0.01, "latency_p95_ms": 126}


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point every agent path at tmp_path so nothing touches warrant/state."""
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(config, "DIAGNOSIS_FILE", tmp_path / "diagnosis.json")
    monkeypatch.setattr(config, "OUTCOME_FILE", tmp_path / "outcome.json")
    monkeypatch.setattr(config, "REPORT_FILE", tmp_path / "report.json")
    monkeypatch.setattr(config, "VERIFY_TIMEOUT_SECONDS", 0)
    return tmp_path


@pytest.fixture
def repo(state, monkeypatch):
    ws = state / "checkout-service"
    workspace.build(ws)
    workspace.trigger_incident(ws)
    monkeypatch.setattr(config, "SERVICE_REPO", ws)
    monkeypatch.setattr(config, "CHECKOUT_URL", "http://127.0.0.1:9")  # nothing listens
    return ws


@pytest.fixture
def stream(state):
    s = EventStream(state / "events.jsonl")
    s.start_new()
    return s


@pytest.fixture
def outcome(state):
    o = Outcome(state / "outcome.json")
    o.reset("INC-TEST")
    return o


@pytest.fixture
def service(monkeypatch):
    """Stand-in for the checkout service: /metrics returns service.metrics, /metrics/history service.history."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            history = self.path.startswith("/metrics/history")
            body = json.dumps({"snapshots": server.history} if history else server.metrics).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.metrics = dict(HEALTHY)
    server.history = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "CHECKOUT_URL", f"http://127.0.0.1:{server.server_port}")
    yield server
    server.shutdown()
    server.server_close()


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]
