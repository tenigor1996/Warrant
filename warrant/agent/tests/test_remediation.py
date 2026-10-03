"""
Remediation safety, the policy gate and verification. No model, no GB10.

Run from the repo root:  python -m pytest warrant/agent/tests -q
"""

import json
import os

import pytest

from warrant.agent import config, remediation, toolbox
from warrant.sim import workspace

from conftest import DEGRADED, read_events

CONFIG = "checkout_service/config.py"
FIX = {"PAYMENT_TIMEOUT": 5.0, "PAYMENT_RETRIES": 1}


def test_dangerous_action_is_denied_and_changes_nothing(repo):
    head, text = workspace.head(repo), (repo / CONFIG).read_text()
    result = remediation.revert_commit_and_push(sha=head[:7])
    assert result["ok"] is False
    assert result["detail"].startswith("DENIED: pushing to main is outside the permitted remediation scope.")
    assert "Choose a different approach." in result["detail"]
    assert result["policy"]["verdict"] == "DENIED"
    assert workspace.head(repo) == head and (repo / CONFIG).read_text() == text


@pytest.mark.parametrize("path", ["checkout_service/payment_client.py", "../escape.py", "/etc/hosts",
                                  ".git/config", "docs/runbook.md"])
def test_writes_outside_config_are_denied(repo, path):
    result = remediation.apply_remediation(path=path, change=FIX)
    assert result["ok"] is False and result["detail"].startswith(f"DENIED: write to {path} is outside")
    assert not (repo.parent / "escape.py").exists()
    assert workspace.is_incident(repo)


def test_unsafe_values_are_denied(repo):
    for change in ({"PAYMENT_TIMEOUT": 0.001}, {"LOG_LEVEL": "DEBUG"}, {"PAYMENT_TIMEOUT": "os.system('id')"}):
        result = remediation.apply_remediation(path=CONFIG, change=change)
        assert result["ok"] is False and result["detail"].startswith("DENIED: ")
    assert workspace.is_incident(repo)


def test_safe_remediation_restores_config_and_nothing_else(repo):
    before = (repo / CONFIG).read_text()
    result = remediation.apply_remediation(path=CONFIG, change=FIX)
    assert result["ok"] is True and result["policy"]["verdict"] == "ALLOWED"
    assert result["changed"]["PAYMENT_TIMEOUT"] == {"from": 0.01, "to": 5.0}
    assert not workspace.is_incident(repo)
    changed_lines = [(a, b) for a, b in zip(before.splitlines(), (repo / CONFIG).read_text().splitlines()) if a != b]
    assert changed_lines == [("PAYMENT_TIMEOUT = 0.01", "PAYMENT_TIMEOUT = 5.0"),
                             ("PAYMENT_RETRIES = 5", "PAYMENT_RETRIES = 1")]


def test_safe_remediation_accepts_absolute_path_and_json_text(repo):
    result = remediation.apply_remediation(path=str(repo / CONFIG), change=json.dumps({"PAYMENT_TIMEOUT": "5.0",
                                                                                      "PAYMENT_RETRIES": 1}))
    assert result["ok"] is True
    assert workspace.read_config(repo)["PAYMENT_TIMEOUT"] == 5.0


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_os_level_refusal_is_reported_as_denied(repo):
    (repo / CONFIG).chmod(0o444)  # what a sandbox without a write grant looks like to the tool
    result = remediation.apply_remediation(path=CONFIG, change=FIX)
    assert result["ok"] is False and result["detail"].startswith("DENIED: ")
    assert result["policy"]["enforced_by"] == "os-sandbox"


def test_no_tool_takes_a_command(repo):
    for schema in toolbox.TOOL_SCHEMAS:
        properties = schema["function"]["parameters"]["properties"]
        assert not {"command", "cmd", "shell", "script", "args"} & set(properties), schema["function"]["name"]


def test_run_tests_reports_failures_during_incident_and_passes_after_fix(repo):
    broken = remediation.run_tests()
    assert broken["failed"] > 0
    remediation.apply_remediation(path=CONFIG, change=FIX)
    fixed = remediation.run_tests()
    assert fixed["failed"] == 0 and fixed["passed"] == 12
    json.dumps(fixed)


def test_run_tests_never_raises(repo, monkeypatch):
    monkeypatch.setattr(config, "SERVICE_REPO", repo / "missing")
    assert "error" in remediation.run_tests()
    monkeypatch.setattr(config, "SERVICE_REPO", repo)
    monkeypatch.setattr(config, "TEST_PYTHON", "/nonexistent/python")
    assert "error" in remediation.run_tests()


def test_verification_fails_before_remediation(repo, service):
    service.metrics = dict(DEGRADED)
    result = remediation.verify_recovery()
    assert result["recovered"] is False and result["final_status"] == "NOT_RECOVERED"
    assert result["config_restored"] is False and result["tests_passed"] is False
    assert result["config"]["differences"]["PAYMENT_TIMEOUT"] == {"current": 0.01, "expected": 5.0}


def test_verification_passes_after_safe_remediation(repo, service):
    remediation.apply_remediation(path=CONFIG, change=FIX)
    result = remediation.verify_recovery()
    assert result == {**result, "recovered": True, "final_status": "RECOVERED", "config_restored": True,
                      "tests_passed": True, "health_recovered": True}
    assert result["tests"] == {"passed": 12, "failed": 0}
    json.dumps(result)


def test_verification_needs_all_three_checks(repo, service):
    remediation.apply_remediation(path=CONFIG, change={"PAYMENT_TIMEOUT": 4.0, "PAYMENT_RETRIES": 1})
    assert remediation.verify_recovery()["config_restored"] is False  # in range, but not the previous value

    remediation.apply_remediation(path=CONFIG, change=FIX)
    service.metrics = dict(DEGRADED)
    result = remediation.verify_recovery()
    assert result["config_restored"] and result["tests_passed"]
    assert result["health_recovered"] is False and result["recovered"] is False


def test_verification_with_service_down_is_not_recovered(repo):
    remediation.apply_remediation(path=CONFIG, change=FIX)
    result = remediation.verify_recovery()
    assert result["recovered"] is False and "error" in result["health"]


def test_timeline_and_outcome_for_deny_then_allow(repo, service, stream, outcome):
    toolbox.execute("revert_commit_and_push", {"sha": "abc1234"}, stream, outcome)
    toolbox.execute("apply_remediation", {"path": CONFIG, "change": FIX}, stream, outcome)
    toolbox.execute("run_tests", {}, stream, outcome)
    toolbox.execute("verify_recovery", {}, stream, outcome)

    events = read_events(stream.path)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    denied, allowed = [e for e in events if e["kind"] == "policy_decision"]
    assert (denied["phase"], denied["policy_verdict"]) == ("policy", "DENIED")
    assert denied["policy_reason"].startswith("DENIED: pushing to main")
    assert (allowed["phase"], allowed["policy_verdict"]) == ("act", "ALLOWED")
    titles = [e["title"] for e in events]
    assert "Remediation applied" in titles and "Tests passed 12/12" in titles
    assert events[-1]["phase"] == "verify" and events[-1]["title"] == "Service recovered"

    data = outcome.load()
    assert [a["verdict"] for a in data["actions_proposed"]] == ["DENIED", "ALLOWED"]
    assert data["action_executed"].startswith("set PAYMENT_TIMEOUT=5.0")
    assert data["tests"] == {"passed": 12, "failed": 0}
    assert data["verification"]["recovered"] is True


def test_toolbox_never_raises(repo, stream):
    assert "error" in toolbox.execute("rm_rf", {}, stream)
    assert "error" in toolbox.execute("apply_remediation", {"bogus": 1}, stream)
    assert "error" in toolbox.execute("get_git_diff", {"sha": "HEAD; rm -rf /"}, stream)
