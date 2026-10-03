"""
Backend selection, the OpenClaw adapter and the :8082 API. OpenClaw itself is
replaced by a stand-in runner; nothing here needs the GB10, NemoClaw or a model.

Run from the repo root:  python -m pytest warrant/agent/tests -q
"""

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from warrant.agent import agent, backends, config, remediation, server, toolbox
from warrant.agent.llm_client import LLMError

from conftest import DEGRADED, read_events
from test_agent import EVENT, GOOD_DIAGNOSIS, ScriptedLLM, call

CONFIG = "checkout_service/config.py"
FIX = {"PAYMENT_TIMEOUT": 5.0, "PAYMENT_RETRIES": 1}


# ---- selection ---------------------------------------------------------------

def test_backend_selection(monkeypatch):
    assert isinstance(backends.get_backend("direct"), backends.DirectBackend)
    assert isinstance(backends.get_backend("openclaw"), backends.OpenClawBackend)
    assert isinstance(backends.get_backend(" OpenClaw "), backends.OpenClawBackend)
    monkeypatch.setattr(config, "BACKEND", "direct")
    assert backends.get_backend().name == "direct"
    monkeypatch.setattr(config, "BACKEND", "openclaw")
    assert backends.get_backend().name == "openclaw"
    with pytest.raises(ValueError, match="WARRANT_AGENT_BACKEND"):
        backends.get_backend("gpt")


def test_prompts_do_not_leak_the_answer():
    text = (backends.OPENCLAW_PROMPT + agent.REMEDIATION_PROMPT + json.dumps(remediation.TOOL_SCHEMAS)).lower()
    for word in ("payment_timeout", "payment_retries", "retry storm", "0.01", "config.py"):
        assert word not in text


# ---- direct backend: full deny -> replan -> fix -> verify --------------------

def test_direct_backend_denial_replan_fix_and_verify(repo, service, stream, outcome):
    llm = ScriptedLLM(
        call("get_recent_commits", limit=3),
        call("submit_diagnosis", **GOOD_DIAGNOSIS),
        call("revert_commit_and_push", sha="abc1234"),
        call("apply_remediation", path=CONFIG, change=FIX),
        call("run_tests"),
        call("verify_recovery"),
    )
    result = backends.DirectBackend(llm=llm).run(EVENT, stream, outcome)

    assert result["status"] == "diagnosed" and result["backend"] == "direct"
    assert result["remediation"]["status"] == "resolved"
    assert result["remediation"]["verification"]["recovered"] is True
    # The model was shown the explicit denial before it replanned.
    denial = next(m for m in llm.seen[3] if m["role"] == "tool")
    assert "DENIED: pushing to main" in denial["content"]

    phases = [e["phase"] for e in read_events(stream.path)]
    order = [phases.index(p) for p in ("detect", "investigate", "diagnose", "policy", "act", "verify")]
    assert order == sorted(order)
    assert [a["verdict"] for a in outcome.load()["actions_proposed"]] == ["DENIED", "ALLOWED"]


def test_direct_backend_reports_unresolved(repo, service, stream, outcome):
    service.metrics = dict(DEGRADED)
    llm = ScriptedLLM(call("submit_diagnosis", **GOOD_DIAGNOSIS), call("verify_recovery"), LLMError("gone"))
    result = backends.DirectBackend(llm=llm).run(EVENT, stream, outcome)
    assert result["status"] == "diagnosed"
    assert result["remediation"]["status"] == "unresolved" and "LLM error" in result["remediation"]["reason"]
    # Verification ran and failed: NOT_RECOVERED, with the failed verification kept.
    assert result["remediation"]["final_status"] == "NOT_RECOVERED"
    assert result["remediation"]["verification"]["recovered"] is False


def test_direct_backend_without_verification_is_unverified(repo, service, stream, outcome):
    llm = ScriptedLLM(call("submit_diagnosis", **GOOD_DIAGNOSIS), call("revert_commit_and_push", sha="abc1234"),
                      LLMError("gone"))
    result = backends.DirectBackend(llm=llm).run(EVENT, stream, outcome)
    assert result["remediation"]["status"] == "unresolved"
    assert result["remediation"]["final_status"] == "UNVERIFIED"   # never verified is not "not recovered"
    assert result["remediation"]["verification"] is None
    assert read_events(stream.path)[-1]["title"] == "Incident not resolved"


def test_remediation_tools_are_not_available_while_investigating(repo, stream):
    llm = ScriptedLLM(call("apply_remediation", path=CONFIG, change=FIX), call("submit_diagnosis", **GOOD_DIAGNOSIS))
    agent.investigate(EVENT, llm=llm, events=stream)
    assert "not available during investigation" in llm.seen[-1][-1]["content"]
    assert remediation._config_check()["restored"] is False


# ---- OpenClaw adapter --------------------------------------------------------

class FakeOpenClaw:
    """Stands in for the `openclaw` process: drives the tools the way the MCP server would."""

    def __init__(self, script=(), returncode=0, error=None):
        self.script, self.returncode, self.error, self.argv = script, returncode, error, None

    def __call__(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs
        if self.error:
            raise self.error
        from warrant.agent import mcp_server
        for name, args in self.script:
            getattr(mcp_server, name)(**args)
        return subprocess.CompletedProcess(argv, self.returncode, stdout="<think>x</think>Fixed.", stderr="boom")


@pytest.fixture
def openclaw_on_path(monkeypatch):
    monkeypatch.setattr(backends.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_openclaw_command_is_argv_not_shell(monkeypatch):
    monkeypatch.setattr(config, "OPENCLAW_AGENT", "main")
    backend = backends.OpenClawBackend("sandbox-exec demo -- openclaw agent --agent {agent} --session-id {session_id} -m {message}")
    argv = backend.argv("hello; rm -rf / $(id)", "warrant-INC-1")
    assert argv == ["sandbox-exec", "demo", "--", "openclaw", "agent", "--agent", "main",
                    "--session-id", "warrant-INC-1", "-m", "hello; rm -rf / $(id)"]
    with pytest.raises(backends.BackendUnavailable, match="message"):
        backends.OpenClawBackend("openclaw agent").argv("x", "y")


def test_openclaw_does_not_use_the_model_url(openclaw_on_path, repo, stream, outcome, monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://model.invalid/v1")
    fake = FakeOpenClaw()
    backends.OpenClawBackend(runner=fake).run(EVENT, stream, outcome)
    assert "model.invalid" not in " ".join(fake.argv)
    assert json.dumps(EVENT, indent=2) in fake.argv[-1]


def test_openclaw_unavailable_fails_clearly(repo, stream, outcome, monkeypatch):
    monkeypatch.setattr(backends.shutil, "which", lambda name: None)
    backend = backends.OpenClawBackend()
    with pytest.raises(backends.BackendUnavailable, match="WARRANT_OPENCLAW_CMD"):
        backend.check()
    result = backend.run(EVENT, stream, outcome)
    assert result["status"] == "inconclusive" and "not found on PATH" in result["reason"]
    assert "OpenClaw unavailable" in [e["title"] for e in read_events(stream.path)]


@pytest.mark.parametrize("fake, expected", [
    (FakeOpenClaw(returncode=2), "exit code 2: boom"),
    (FakeOpenClaw(error=subprocess.TimeoutExpired("openclaw", 1)), "no result within"),
    (FakeOpenClaw(error=FileNotFoundError("openclaw")), "could not run"),
    (FakeOpenClaw(), "without calling submit_diagnosis"),
])
def test_openclaw_failures_end_inconclusive(openclaw_on_path, repo, stream, outcome, fake, expected):
    result = backends.OpenClawBackend(runner=fake).run(EVENT, stream, outcome)
    assert result["status"] == "inconclusive" and expected in result["reason"]


def test_openclaw_full_run_through_mcp_tools(openclaw_on_path, repo, service, stream, outcome):
    pytest.importorskip("mcp")
    fake = FakeOpenClaw(script=[
        ("get_recent_commits", {"limit": 3}),
        ("submit_diagnosis", GOOD_DIAGNOSIS),
        ("revert_commit_and_push", {"sha": "abc1234"}),
        ("apply_remediation", {"path": CONFIG, "change": FIX}),
        ("run_tests", {}),
        ("verify_recovery", {}),
    ])
    result = backends.OpenClawBackend(runner=fake).run(EVENT, stream, outcome)

    assert result["status"] == "diagnosed" and result["incident_id"] == "INC-TEST"
    assert result["backend"] == "openclaw" and result["agent_summary"] == "Fixed."
    assert result["remediation"]["final_status"] == "RECOVERED"
    assert json.loads(config.DIAGNOSIS_FILE.read_text())["root_cause"] == "x"

    events = read_events(stream.path)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))  # two writers, one sequence
    kinds = [(e["phase"], e["kind"]) for e in events]
    for expected in [("detect", "status"), ("investigate", "status"), ("investigate", "tool_call"),
                     ("investigate", "tool_result"), ("diagnose", "reasoning"), ("policy", "policy_decision"),
                     ("act", "policy_decision"), ("verify", "tool_result"), ("verify", "status")]:
        assert expected in kinds


def test_policy_blocked_run_is_unverified_everywhere(openclaw_on_path, repo, service, monkeypatch):
    """Only a denied action, no verification: /status, /outcome and report.json must agree."""
    pytest.importorskip("mcp")
    fake = FakeOpenClaw(script=[("submit_diagnosis", GOOD_DIAGNOSIS), ("revert_commit_and_push", {"sha": "abc1234"})])
    monkeypatch.setattr(server, "_state", {**server._state, "state": "investigating", "incident_id": "INC-TEST"})
    server._run(dict(EVENT), backends.OpenClawBackend(runner=fake))

    status = server._snapshot()
    assert status["state"] == "diagnosed"   # not "resolved"
    assert status["remediation"]["final_status"] == "UNVERIFIED" and status["remediation"]["status"] == "unresolved"
    recorded = json.loads(config.OUTCOME_FILE.read_text())
    assert recorded["verification"] is None and recorded["action_executed"] is None
    report = json.loads(config.REPORT_FILE.read_text())
    assert report["final_status"] == "UNVERIFIED" and report["action_executed"] is None
    assert [a["verdict"] for a in report["actions_proposed"]] == ["DENIED"]
    assert {report["final_status"], status["remediation"]["final_status"]} <= {"RECOVERED", "NOT_RECOVERED",
                                                                              "UNVERIFIED"}


def test_failed_verification_is_not_recovered_everywhere(openclaw_on_path, repo, service, monkeypatch):
    pytest.importorskip("mcp")
    service.metrics = dict(DEGRADED)
    fake = FakeOpenClaw(script=[("submit_diagnosis", GOOD_DIAGNOSIS), ("verify_recovery", {})])
    monkeypatch.setattr(server, "_state", {**server._state, "state": "investigating", "incident_id": "INC-TEST"})
    server._run(dict(EVENT), backends.OpenClawBackend(runner=fake))

    assert server._snapshot()["remediation"]["final_status"] == "NOT_RECOVERED"
    assert json.loads(config.OUTCOME_FILE.read_text())["verification"]["final_status"] == "NOT_RECOVERED"
    assert json.loads(config.REPORT_FILE.read_text())["final_status"] == "NOT_RECOVERED"


# ---- :8082 API ---------------------------------------------------------------

class SlowBackend:
    name = "fake"

    def __init__(self):
        self.release = threading.Event()

    def run(self, event, events, outcome):
        outcome.update(diagnosis={"status": "diagnosed", "root_cause": "x"})
        self.release.wait(5)
        toolbox.execute("apply_remediation", {"path": CONFIG, "change": FIX}, events, outcome)
        verification = toolbox.execute("verify_recovery", {}, events, outcome)
        return {"incident_id": event["incident_id"], "status": "diagnosed", "root_cause": "x",
                "remediation": {"status": "resolved", "verification": verification}}


def _http(method, url, body=None):
    request = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_api_runs_asynchronously_and_keeps_its_endpoints(repo, service, monkeypatch):
    backend = SlowBackend()
    monkeypatch.setattr(backends, "get_backend", lambda name=None: backend)
    monkeypatch.setattr(server, "_state", {**server._state, "state": "idle"})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        assert _http("GET", base + "/status")[1]["state"] == "idle"
        assert _http("GET", base + "/diagnosis")[0] == 404

        code, body = _http("POST", base + "/investigate", EVENT)  # returns before the run finishes
        assert (code, body) == (202, {"accepted": True, "incident_id": "INC-TEST"})
        assert _http("POST", base + "/investigate", EVENT)[0] == 409

        deadline = time.time() + 5
        while _http("GET", base + "/diagnosis")[0] != 200 and time.time() < deadline:
            time.sleep(0.02)
        status = _http("GET", base + "/status")[1]
        assert status["state"] == "investigating" and status["stage"] == "remediate"

        backend.release.set()
        while _http("GET", base + "/status")[1]["state"] == "investigating" and time.time() < deadline:
            time.sleep(0.02)
        status = _http("GET", base + "/status")[1]
        assert status["state"] == "resolved" and status["remediation"]["status"] == "resolved"
        assert _http("GET", base + "/diagnosis")[1]["root_cause"] == "x"
        assert _http("GET", base + "/outcome")[1]["verification"]["recovered"] is True
        report = json.loads(config.REPORT_FILE.read_text())  # written before the state turned final
        assert report["final_status"] == "RECOVERED" and report["incident_id"] == "INC-TEST"
        assert read_events(config.EVENTS_FILE)[-1]["title"] == "Incident report generated"
        assert _http("POST", base + "/investigate", EVENT)[0] == 202  # accepts the next incident
    finally:
        backend.release.set()
        httpd.shutdown()
        httpd.server_close()
