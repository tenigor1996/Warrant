"""
App-level remediation policy: deterministic, deny by default.

Run from the repo root:  python -m pytest warrant/policy/tests -q
"""

import pytest

from warrant.policy import policy

CONFIG = "checkout_service/config.py"


def test_config_write_within_bounds_is_allowed():
    decision = policy.evaluate("apply_remediation", {"path": CONFIG, "change": {"PAYMENT_TIMEOUT": 5.0,
                                                                                 "PAYMENT_RETRIES": 1}})
    assert decision.allowed and decision.verdict == "ALLOWED"
    assert decision.enforced_by == policy.APP_LAYER


def test_push_to_main_is_denied_with_an_instructive_message():
    decision = policy.evaluate("revert_commit_and_push", {"sha": "abc1234"})
    assert not decision.allowed
    assert decision.message == (
        "DENIED: pushing to main is outside the permitted remediation scope. Permitted write target: "
        "checkout configuration only (checkout_service/config.py, via apply_remediation). "
        "Choose a different approach.")


@pytest.mark.parametrize("path", [
    "checkout_service/payment_client.py", "/etc/passwd", "../outside.py", ".git/config",
    "checkout_service/../../etc/passwd", "", None,
])
def test_write_outside_config_is_denied(path):
    decision = policy.evaluate("apply_remediation", {"path": path, "change": {"PAYMENT_TIMEOUT": 5.0}})
    assert decision.verdict == "DENIED"
    assert decision.message.startswith("DENIED: ")
    assert "Permitted write target: checkout configuration only" in decision.message
    assert decision.message.endswith("Choose a different approach.")


@pytest.mark.parametrize("change", [
    {"PAYMENT_GATEWAY_URL": "https://evil.example"},   # not a permitted setting
    {"PAYMENT_TIMEOUT": 0.01},                         # below the permitted range
    {"PAYMENT_RETRIES": 50},                           # above the permitted range
    {"PAYMENT_RETRIES": 1.5},                          # wrong type
    {"PAYMENT_TIMEOUT": True},
    {"PAYMENT_TIMEOUT": "__import__('os').system('id')"},
    {}, None, "PAYMENT_TIMEOUT=5.0",
])
def test_out_of_scope_setting_changes_are_denied(change):
    assert policy.evaluate("apply_remediation", {"path": CONFIG, "change": change}).verdict == "DENIED"


def test_unknown_actions_are_denied():
    assert policy.evaluate("run_shell", {"command": "rm -rf /"}).verdict == "DENIED"


def test_decisions_are_deterministic():
    params = {"path": CONFIG, "change": {"PAYMENT_TIMEOUT": 0.01}}
    assert policy.evaluate("apply_remediation", params) == policy.evaluate("apply_remediation", params)


def test_sandbox_refusal_is_reported_as_a_separate_layer():
    decision = policy.os_denial(CONFIG, PermissionError(13, "Permission denied"))
    assert decision.verdict == "DENIED" and decision.enforced_by == policy.OS_LAYER
    assert decision.message.startswith("DENIED: ")
