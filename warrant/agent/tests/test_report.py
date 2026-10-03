"""
The final incident report: every field traced to recorded runtime data, sealed
with the renderer's hash. No model, no GB10.

Run from the repo root:  python -m pytest warrant/agent/tests -q
"""

import json
from pathlib import Path

import pytest

from warrant.agent import backends, config, report, toolbox
from warrant.agent.outcome import Outcome
from warrant.report import report_renderer

from conftest import DEGRADED, read_events
from test_agent import ScriptedLLM, call

CONTRACT = json.loads((Path(report.__file__).resolve().parents[1] / "contracts" / "report.json").read_text())
CONFIG = "checkout_service/config.py"
FIX = {"PAYMENT_TIMEOUT": 5.0, "PAYMENT_RETRIES": 1}

MONITOR_EVENT = {"incident_id": "INC-20261003-144218", "event_type": "service_degradation", "service": "checkout",
                 "detected_at": "2026-10-03T14:42:18Z", "status": "degraded", "failure_rate": 0.17,
                 "latency_p95_ms": 3181, "requests_last_minute": 240, "errors_last_minute": 40}
DIAGNOSIS = {"root_cause": "timeout cut and retries raised in commit e9d0ce8", "confidence": "high",
             "offending_commit": "e9d0ce8", "recommended_next_step": "restore the settings",
             "evidence": [{"source": "git", "finding": "commit e9d0ce8 changed the payment settings"},
                          {"source": "logs", "finding": "412 PaymentTimeout entries"}]}
# What the service itself recorded: healthy, then the incident. Timestamps far in the future
# sort after any real "now", so the last one is the reading just before the fix.
HISTORY = [
    {"timestamp": "2026-10-03T14:42:00Z", "status": "healthy", "failure_rate": 0.01, "latency_p95_ms": 125,
     "payment_requests_per_minute": 240},
    {"timestamp": "2026-10-03T14:42:20Z", "status": "degraded", "failure_rate": 0.4, "latency_p95_ms": 3000,
     "payment_requests_per_minute": 900},
    {"timestamp": "2026-10-03T14:42:40Z", "status": "degraded", "failure_rate": 0.68, "latency_p95_ms": 3184,
     "payment_requests_per_minute": 1136},
]
AFTER = {"timestamp": "2026-10-03T14:43:40Z", "status": "recovering", "failure_rate": 0.01, "latency_p95_ms": 126}


@pytest.fixture
def completed(repo, service, stream, monkeypatch):
    """A whole incident through the direct backend: investigate, denied, replan, fix, test, verify."""
    service.history = HISTORY
    service.metrics = dict(AFTER)
    # Pin "when the fix was applied" so it lines up with the recorded history.
    real_update = Outcome.update
    monkeypatch.setattr(Outcome, "update", lambda self, **f: real_update(
        self, **{**f, **({"action_executed_at": "2026-10-03T14:42:45Z"} if "action_executed_at" in f else {})}))
    outcome = Outcome(config.OUTCOME_FILE)
    outcome.reset(MONITOR_EVENT["incident_id"], MONITOR_EVENT)
    llm = ScriptedLLM(call("get_recent_commits", limit=3), call("submit_diagnosis", **DIAGNOSIS),
                      call("revert_commit_and_push", sha="e9d0ce8"),
                      call("apply_remediation", path=CONFIG, change=FIX),
                      call("run_tests"), call("verify_recovery"))
    backends.DirectBackend(llm=llm).run(MONITOR_EVENT, stream, outcome)
    return report.generate_incident_report(outcome, stream)


def test_completed_incident_has_exactly_the_contract_fields(completed):
    assert set(completed) == set(CONTRACT)
    assert all(value is not None and value != [] for value in completed.values())
    for key in ("symptoms", "evidence", "actions_proposed"):
        assert isinstance(completed[key], list)
    for key in ("tests", "metrics_before", "metrics_after"):
        assert set(completed[key]) == set(CONTRACT[key])
    assert set(completed["actions_proposed"][0]) == set(CONTRACT["actions_proposed"][0])
    assert set(completed["evidence"][0]) == set(CONTRACT["evidence"][0])
    assert Outcome(config.OUTCOME_FILE).load()["report_missing"] == []


def test_fields_come_from_the_recorded_incident(completed):
    assert completed["incident_id"] == MONITOR_EVENT["incident_id"]
    assert completed["detected_at"] == MONITOR_EVENT["detected_at"]       # the monitor's event
    assert completed["resolved_at"] == AFTER["timestamp"]                 # the reading that passed verification
    assert completed["root_cause"] == DIAGNOSIS["root_cause"]             # the submitted diagnosis
    assert completed["offending_commit"] == "e9d0ce8"
    assert completed["evidence"] == DIAGNOSIS["evidence"]
    assert completed["symptoms"] == ["failure rate 1% -> 68%", "p95 latency 125ms -> 3184ms",
                                     "payment gateway calls 240/min -> 1136/min"]


def test_denied_and_allowed_actions_survive(completed):
    denied, allowed = completed["actions_proposed"]
    assert denied == {"action": "revert_commit_and_push(sha='e9d0ce8')", "verdict": "DENIED",
                      "reason": "pushing to main is outside the permitted remediation scope"}
    assert allowed["verdict"] == "ALLOWED" and allowed["action"].startswith("apply_remediation(")
    assert allowed["reason"] == "write within checkout_service/config.py"
    assert completed["action_executed"] == "set PAYMENT_TIMEOUT=5.0, PAYMENT_RETRIES=1 in checkout_service/config.py"


def test_test_counts_and_recovery_metrics_survive(completed):
    assert completed["tests"] == {"passed": 12, "failed": 0}
    assert completed["metrics_before"] == {"failure_rate": 0.68, "latency_p95_ms": 3184}   # last snapshot before the fix
    assert completed["metrics_after"] == {"failure_rate": 0.01, "latency_p95_ms": 126}     # verification's reading
    assert completed["final_status"] == "RECOVERED"


def test_hash_is_the_renderers_hash(completed):
    assert completed["report_sha256"] == report_renderer.compute_sha256(completed)
    on_disk = json.loads(config.REPORT_FILE.read_text())
    assert on_disk == completed
    assert on_disk["report_sha256"] == report_renderer.compute_sha256(on_disk)
    sealed, digest, previous = report_renderer.seal(on_disk)   # the renderer agrees with what was stored
    assert digest == previous and len(digest) == 64 and digest == digest.lower()


@pytest.mark.parametrize("field, value", [
    ("root_cause", "something else"), ("final_status", "NOT_RECOVERED"), ("offending_commit", "0000000"),
    ("tests", {"passed": 12, "failed": 1}), ("metrics_after", {"failure_rate": 0.0, "latency_p95_ms": 126}),
    ("action_executed", "nothing"), ("resolved_at", "2026-10-03T14:43:41Z"),
])
def test_tampering_changes_the_hash(completed, field, value):
    tampered = {**completed, field: value}
    assert report_renderer.compute_sha256(tampered) != completed["report_sha256"]


def test_removing_a_denied_action_changes_the_hash(completed):
    tampered = {**completed, "actions_proposed": completed["actions_proposed"][1:]}
    assert report_renderer.compute_sha256(tampered) != completed["report_sha256"]


def test_report_is_generated_last_and_written_atomically(completed, stream):
    events = read_events(stream.path)
    assert (events[-1]["phase"], events[-1]["title"]) == ("report", "Incident report generated")
    assert events[-1]["detail"] == f"{MONITOR_EVENT['incident_id']} RECOVERED"
    assert events[-2]["title"] == "Service recovered"
    assert not list(config.STATE_DIR.glob("report.json.tmp"))


def test_write_never_exposes_a_partial_file(state, monkeypatch):
    target = state / "report.json"
    report.write_report({"incident_id": "old"}, target)

    def crash(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(report.os, "replace", crash)
    with pytest.raises(OSError):
        report.write_report({"incident_id": "new"}, target)
    assert json.loads(target.read_text()) == {"incident_id": "old"}   # still the complete previous report


# ---- missing data ------------------------------------------------------------

def test_missing_data_is_reported_as_missing_not_invented(state, stream):
    """Nothing recorded but the incident id, and the service is unreachable."""
    outcome = Outcome(config.OUTCOME_FILE)
    outcome.reset("INC-EMPTY")
    result = report.generate_incident_report(outcome, stream)

    assert set(result) == set(CONTRACT)
    assert result["incident_id"] == "INC-EMPTY" and result["final_status"] == "UNVERIFIED"
    for field in ("detected_at", "resolved_at", "root_cause", "offending_commit", "action_executed", "tests",
                  "metrics_before", "metrics_after"):
        assert result[field] is None
    assert result["symptoms"] == [] and result["evidence"] == [] and result["actions_proposed"] == []
    assert result["report_sha256"] == report_renderer.compute_sha256(result)

    missing = outcome.load()["report_missing"]
    assert {"root_cause", "tests", "metrics_before", "metrics_after", "evidence"} <= set(missing)
    assert "resolved_at" not in missing   # not expected for an incident that was never verified
    assert "unavailable: " in read_events(stream.path)[-1]["detail"]


def test_inconclusive_openclaw_run_still_gets_an_honest_report(repo, stream, monkeypatch):
    monkeypatch.setattr(backends.shutil, "which", lambda name: None)
    outcome = Outcome(config.OUTCOME_FILE)
    outcome.reset(MONITOR_EVENT["incident_id"], MONITOR_EVENT)
    backends.OpenClawBackend().run(MONITOR_EVENT, stream, outcome)
    result = report.generate_incident_report(outcome, stream)

    assert result["final_status"] == "UNVERIFIED" and result["root_cause"] is None
    assert result["detected_at"] == MONITOR_EVENT["detected_at"]
    # No fix was applied and the service is unreachable: only the monitor's own reading exists.
    assert result["metrics_before"] == {"failure_rate": 0.17, "latency_p95_ms": 3181}
    assert result["symptoms"] == ["failure rate 17% during the incident (no healthy baseline recorded)",
                                  "p95 latency 3181ms during the incident (no healthy baseline recorded)"]


def test_failed_verification_is_not_reported_as_recovered(repo, service, stream):
    service.metrics = dict(DEGRADED, timestamp="2026-10-03T14:43:40Z")
    outcome = Outcome(config.OUTCOME_FILE)
    outcome.reset(MONITOR_EVENT["incident_id"], MONITOR_EVENT)
    outcome.update(diagnosis=DIAGNOSIS)
    toolbox.execute("apply_remediation", {"path": CONFIG, "change": FIX}, stream, outcome)
    toolbox.execute("verify_recovery", {}, stream, outcome)
    result = report.generate_incident_report(outcome, stream)

    assert result["final_status"] == "NOT_RECOVERED" and result["resolved_at"] is None
    assert result["metrics_after"] == {"failure_rate": 0.68, "latency_p95_ms": 3184}
    assert result["tests"] == {"passed": 12, "failed": 0}


def test_timestamps_fall_back_to_the_timeline(state):
    timeline = [{"phase": "detect", "title": "Incident received", "timestamp": "2026-10-03T14:42:19Z"},
                {"phase": "verify", "title": "Service recovered", "timestamp": "2026-10-03T14:43:41Z"}]
    data = {"incident_id": "INC-1", "verification": {"recovered": True, "health": {"failure_rate": 0.0,
                                                                                 "latency_p95_ms": 120}}}
    built, missing = report.build_report(data, timeline, [])
    assert built["detected_at"] == "2026-10-03T14:42:19Z" and built["resolved_at"] == "2026-10-03T14:43:41Z"
    assert "resolved_at" not in missing and "root_cause" in missing


def test_fixture_hash_matches_the_shared_algorithm():
    fixture = json.loads((Path(report.__file__).resolve().parents[1] / "fixtures" / "report.json").read_text())
    assert fixture["report_sha256"] == report_renderer.compute_sha256(fixture)
