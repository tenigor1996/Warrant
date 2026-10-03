"""
Determinism and evidence checks for the simulated incident.

Run from the repo root:  python -m pytest warrant/sim/tests -q
"""

import subprocess
import sys

import pytest

from warrant.sim import workspace
from warrant.sim.engine import Simulator

T0 = 1_800_000_000.0


@pytest.fixture
def sim(tmp_path):
    ws = tmp_path / "checkout-service"
    workspace.build(ws, T0)
    s = Simulator(ws, tmp_path)
    s._reset_counters(T0)
    return s


def _run(sim, start, seconds):
    for t in range(int(start), int(start + seconds) + 1):
        sim.advance(t)
    return sim.metrics(start + seconds)


def _repo_tests(ws):
    return subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ws, capture_output=True, text=True)


def test_healthy_baseline(sim):
    m = _run(sim, T0, 60)
    assert m["status"] == "healthy"
    assert m["failure_rate"] < 0.05
    assert m["latency_p95_ms"] < 200
    assert m["payment_requests_per_minute"] < 300


def test_incident_produces_retry_storm(sim):
    before = _run(sim, T0, 60)
    workspace.trigger_incident(sim.ws, T0 + 60)
    after = _run(sim, T0 + 60, 60)
    assert after["status"] == "degraded"
    assert 0.55 <= after["failure_rate"] <= 0.80
    assert after["latency_p95_ms"] > 2000
    assert after["payment_requests_per_minute"] > 3 * before["payment_requests_per_minute"]
    assert after["retry_count"] > 500


def test_restoring_config_recovers_within_20s(sim):
    _run(sim, T0, 30)
    workspace.trigger_incident(sim.ws, T0 + 30)
    _run(sim, T0 + 30, 60)
    workspace.write_config(sim.ws, **workspace.HEALTHY_CONFIG)
    m = _run(sim, T0 + 90, 20)
    assert m["failure_rate"] < 0.05
    assert m["latency_p95_ms"] < 300


def test_same_config_same_outcome(tmp_path):
    results = []
    for run in ("a", "b"):
        ws = tmp_path / run / "checkout-service"
        workspace.build(ws, T0)
        s = Simulator(ws, tmp_path / run)
        s._reset_counters(T0)
        _run(s, T0, 20)
        workspace.trigger_incident(ws, T0 + 20)
        results.append(_run(s, T0 + 20, 40))
    assert results[0] == results[1]


def test_logs_show_symptoms_not_cause(sim):
    _run(sim, T0, 10)
    workspace.trigger_incident(sim.ws, T0 + 10)
    _run(sim, T0 + 10, 20)
    log = sim.app_log.read_text()
    assert "PaymentTimeout" in log
    assert "retry attempt 1/5" in log and "retry attempt 5/5" in log
    assert "checkout failed" in log
    assert "Traceback" in log
    assert "PAYMENT_TIMEOUT" not in log and "PAYMENT_RETRIES" not in log


def test_git_history_contains_buried_config_change(sim):
    info = workspace.trigger_incident(sim.ws, T0)
    log = workspace._git(sim.ws, "log", "--format=%h %s").splitlines()
    assert len(log) == 7
    assert info["incident_commit"] != info["head"]  # culprit is not HEAD
    diff = workspace._git(sim.ws, "show", info["incident_commit"])
    assert "-PAYMENT_TIMEOUT = 5.0" in diff and "+PAYMENT_TIMEOUT = 0.01" in diff
    assert "-PAYMENT_RETRIES = 1" in diff and "+PAYMENT_RETRIES = 5" in diff


def test_repo_tests_pass_healthy_fail_incident_pass_after_fix(sim):
    healthy = _repo_tests(sim.ws)
    assert healthy.returncode == 0, healthy.stdout

    workspace.trigger_incident(sim.ws, T0)
    incident = _repo_tests(sim.ws)
    assert incident.returncode != 0
    assert "failed" in incident.stdout

    workspace.write_config(sim.ws, **workspace.HEALTHY_CONFIG)
    fixed = _repo_tests(sim.ws)
    assert fixed.returncode == 0, fixed.stdout
