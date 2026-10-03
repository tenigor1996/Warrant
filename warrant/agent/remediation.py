"""
agent/remediation.py — Remediation and verification tools.

    revert_commit_and_push(sha)       dangerous on purpose; the policy denies it
    apply_remediation(path, change)   sets named settings in a file     [POLICY GATE]
    run_tests()                       the checkout-service test suite
    verify_recovery()                 config restored + tests pass + health recovered

Every write goes through warrant.policy first. The model never gets a shell:
each tool runs a fixed command line (or none), and the only file ever written
is the one the policy allows, by rewriting `NAME = value` lines.

Like tools.py, each tool returns a JSON-serializable dict and never raises.
"""

import ast
import json
import os
import re
import subprocess
import time

from warrant.agent import config
from warrant.agent.tools import _git, _http_get, _safe, _schema
from warrant.policy import policy

MAX_TEST_OUTPUT_CHARS = 3000


# ---- remediation -------------------------------------------------------------

@_safe
def revert_commit_and_push(sha):
    """Revert a commit and push the revert to main."""
    decision = policy.evaluate("revert_commit_and_push", {"sha": sha}, _rules())
    if not decision.allowed:
        return _denied(decision)
    # Unreachable with the shipped policy. Warrant has no push capability at all.
    return {"ok": False, "detail": "revert_commit_and_push is not implemented", "policy": decision.as_dict()}


@_safe
def apply_remediation(path, change):
    """Set named settings in a file of the checkout-service repository."""
    rules = _rules()
    relpath = _relative_path(path)
    change = _parse_change(change)
    decision = policy.evaluate("apply_remediation", {"path": relpath, "change": change}, rules)
    if not decision.allowed:
        return _denied(decision)

    target = config.SERVICE_REPO / relpath
    try:
        text = target.read_text()
        before = _settings(text)
        for name, value in change.items():
            text, count = re.subn(rf"^{re.escape(name)} = .*$", f"{name} = {value!r}", text, flags=re.M)
            if count != 1:
                return {"ok": False, "detail": f"{name} is not defined in {relpath}; nothing was changed",
                        "policy": decision.as_dict()}
        # Written in place so a sandbox grant on this one file is sufficient.
        with target.open("w") as f:
            f.write(text)
    except PermissionError as exc:
        return _denied(policy.os_denial(relpath, exc, rules))

    changed = {name: {"from": before.get(name), "to": value} for name, value in change.items()}
    summary = ", ".join(f"{name}={value!r}" for name, value in change.items())
    return {"ok": True, "detail": f"set {summary} in {relpath}", "path": relpath, "changed": changed,
            "policy": decision.as_dict()}


# ---- verification ------------------------------------------------------------

@_safe
def run_tests():
    """Run the checkout-service test suite."""
    try:
        result = subprocess.run(
            [config.TEST_PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=config.SERVICE_REPO, capture_output=True, text=True, timeout=config.TEST_TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return {"error": f"tests did not finish within {config.TEST_TIMEOUT_SECONDS:g}s"}
    output = (result.stdout + result.stderr).strip()
    counts = {word: int(n) for n, word in re.findall(r"(\d+) (passed|failed|error)", output)}
    if not counts:
        return {"error": f"could not run tests: {output[-400:]}"}
    return {"passed": counts.get("passed", 0), "failed": counts.get("failed", 0) + counts.get("error", 0),
            "output": output[-MAX_TEST_OUTPUT_CHARS:]}


@_safe
def verify_recovery():
    """Check that configuration is restored, tests pass and service health has recovered."""
    configuration = _config_check()
    tests = run_tests()
    tests_ok = "error" not in tests and tests["failed"] == 0 and tests["passed"] > 0
    # No point waiting for health while the cause is still in place.
    health = _wait_for_health(config.VERIFY_TIMEOUT_SECONDS if configuration["restored"] and tests_ok else 0)
    recovered = configuration["restored"] and tests_ok and health["recovered"]
    return {
        "recovered": recovered,
        "final_status": "RECOVERED" if recovered else "NOT_RECOVERED",
        "config_restored": configuration["restored"],
        "tests_passed": tests_ok,
        "health_recovered": health["recovered"],
        "config": configuration,
        "tests": {k: tests[k] for k in ("passed", "failed", "error") if k in tests},
        "health": health,
    }


def _config_check() -> dict:
    """Compare the policy-governed settings with their values before the last committed config change."""
    relpath = config.CONFIG_RELPATH
    current = _settings((config.SERVICE_REPO / relpath).read_text())
    last_change = _git("log", "-n1", "--format=%H", "--", relpath).strip()
    baseline = _settings(_git("show", f"{last_change}^:{relpath}"))
    differences = {
        name: {"current": current.get(name), "expected": baseline[name]}
        for name in _rules()["settings"]
        if name in baseline and current.get(name) != baseline[name]
    }
    return {"restored": not differences, "path": relpath, "baseline": f"{last_change[:12]}^",
            "differences": differences}


def _wait_for_health(timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            health = _http_get("/metrics")
        except Exception as exc:
            return {"recovered": False, "error": f"{type(exc).__name__}: {exc}"}
        recovered = (health.get("status") != "degraded"
                     and health.get("failure_rate", 1) < config.RECOVERED_FAILURE_RATE
                     and health.get("latency_p95_ms", 1e9) < config.RECOVERED_P95_MS)
        if recovered or time.monotonic() >= deadline:
            return {**health, "recovered": recovered}
        time.sleep(config.VERIFY_INTERVAL_SECONDS)


# ---- helpers -----------------------------------------------------------------

def _rules() -> dict:
    return policy.load(config.POLICY_FILE)


def _denied(decision) -> dict:
    return {"ok": False, "detail": decision.message, "policy": decision.as_dict()}


def _relative_path(path) -> str:
    """Repo-relative form of `path` if it points inside the repository, else `path` unchanged."""
    root = config.SERVICE_REPO.resolve()
    target = (root / str(path)).resolve()
    if root in target.parents:
        return target.relative_to(root).as_posix()
    return str(path)


def _parse_change(change):
    """Accept an object or its JSON text; numeric strings become numbers."""
    if isinstance(change, str):
        try:
            change = json.loads(change)
        except ValueError:
            return None
    if not isinstance(change, dict):
        return None
    parsed = {}
    for name, value in change.items():
        if isinstance(value, str):
            try:
                value = ast.literal_eval(value.strip())
            except (ValueError, SyntaxError):
                pass
        parsed[str(name)] = value
    return parsed


def _settings(text: str) -> dict:
    """NAME = literal assignments, parsed without executing the file."""
    values = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return values


TOOLS = {fn.__name__: fn for fn in (revert_commit_and_push, apply_remediation, run_tests, verify_recovery)}

TOOL_SCHEMAS = [
    _schema("revert_commit_and_push",
            "Revert one commit in the checkout-service repository and push the revert to main, "
            "which redeploys the service. Subject to the remediation policy.",
            {"sha": {"type": "string", "description": "Commit sha to revert."}}, ["sha"]),
    _schema("apply_remediation",
            "Set named settings in a file of the checkout-service repository. The running service "
            "reloads its configuration when the file changes. Subject to the remediation policy: "
            "a result whose detail starts with DENIED was not applied.",
            {"path": {"type": "string", "description": "File path relative to the repo root."},
             "change": {"type": "object",
                        "description": "Setting names mapped to their new values, e.g. {\"SETTING_NAME\": 1}."}},
            ["path", "change"]),
    _schema("run_tests", "Run the checkout-service test suite. Returns passed and failed counts and the output."),
    _schema("verify_recovery",
            "After a remediation, verify the incident is over: configuration restored, tests passing "
            "and live service health recovered. Waits for health to settle, so it can take up to a minute."),
]
