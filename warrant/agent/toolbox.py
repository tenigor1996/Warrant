"""
agent/toolbox.py — Runs one tool call and records it on the timeline.

Both runtimes go through execute(): the direct loop in agent.py and the MCP
server that OpenClaw talks to. That keeps the timeline, the policy events and
the outcome file identical whichever backend drove the model.

Timeline entries are factual: what was called, what came back, what the policy
decided. Model reasoning is not recorded here.
"""

import json
import time
from datetime import datetime, timezone

from warrant.agent import remediation, tools

TOOLS = {**tools.TOOLS, **remediation.TOOLS}
TOOL_SCHEMAS = tools.TOOL_SCHEMAS + remediation.TOOL_SCHEMAS

TOOL_TITLES = {
    "get_service_health": "Checking service health",
    "get_metrics_history": "Reviewing metrics history",
    "search_logs": "Searching application logs",
    "inspect_stack_trace": "Inspecting stack trace",
    "get_recent_commits": "Inspecting git history",
    "get_git_diff": "Reading commit diff",
    "read_config": "Reading configuration",
    "read_source_file": "Reading source code",
    "list_repository_files": "Listing repository files",
    "revert_commit_and_push": "Revert commit and push to main",
    "apply_remediation": "Change checkout-service settings",
    "run_tests": "Running service tests",
    "verify_recovery": "Verifying recovery",
}

POLICY_GATED = ("revert_commit_and_push", "apply_remediation")
VERIFICATION = ("run_tests", "verify_recovery")


def execute(name: str, arguments: dict, events=None, outcome=None) -> dict:
    """Run a tool. Always returns a JSON-serializable dict; never raises."""
    arguments = arguments or {}
    call_text = f"{name}({', '.join(f'{k}={v!r}' for k, v in arguments.items())})"
    phase = "verify" if name in VERIFICATION else "investigate"
    if name not in POLICY_GATED:
        _emit(events, phase, "tool_call", TOOL_TITLES.get(name, name), call_text)

    started = time.monotonic()
    try:
        fn = TOOLS.get(name)
        result = fn(**arguments) if fn else {"error": f"unknown tool {name!r}; available: {sorted(TOOLS)}"}
        json.dumps(result)
    except Exception as exc:  # tools catch their own errors; this is the last line of defence
        result = {"error": f"{type(exc).__name__}: {exc}"}
    duration = int((time.monotonic() - started) * 1000)

    try:
        _record(name, arguments, call_text, result, duration, phase, events, outcome)
    except Exception as exc:
        print(f"[AGENT] could not record {name}: {exc!r}", flush=True, file=getattr(events, "echo", None))
    return result


def _record(name, arguments, call_text, result, duration, phase, events, outcome):
    if outcome is not None:
        outcome.append("tools_used", name)

    decision = result.get("policy") if name in POLICY_GATED else None
    if decision:
        allowed = decision["verdict"] == "ALLOWED"
        _emit(events, "act" if allowed else "policy", "policy_decision", TOOL_TITLES[name], call_text,
              duration_ms=duration, policy_verdict=decision["verdict"],
              policy_reason=decision["reason"] if allowed else result["detail"])
        if outcome is not None:
            outcome.append("actions_proposed", {"action": call_text, "verdict": decision["verdict"],
                                                "reason": decision["reason"],
                                                "enforced_by": decision["enforced_by"]})
        if allowed:
            applied = bool(result.get("ok"))
            _emit(events, "act", "status", "Remediation applied" if applied else "Remediation failed",
                  str(result.get("detail", ""))[:400])
            if applied and outcome is not None:
                outcome.update(action_executed=result["detail"],
                               action_executed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return

    if name == "run_tests" and "error" not in result:
        total = result["passed"] + result["failed"]
        verdict = "passed" if result["failed"] == 0 else "failed"
        shown = result["passed"] if result["failed"] == 0 else result["failed"]
        _emit(events, phase, "tool_result", f"Tests {verdict} {shown}/{total}",
              json.dumps({"passed": result["passed"], "failed": result["failed"]}), duration_ms=duration)
        if outcome is not None:
            outcome.update(tests={"passed": result["passed"], "failed": result["failed"]})
        return

    if name == "verify_recovery" and "error" not in result:
        health = result.get("health", {})
        detail = (f"config restored: {_yn(result['config_restored'])}, tests passing: {_yn(result['tests_passed'])}, "
                  f"failure rate {_pct(health.get('failure_rate'))}, p95 {health.get('latency_p95_ms', '?')} ms")
        _emit(events, phase, "status", "Service recovered" if result["recovered"] else "Service not recovered",
              detail, duration_ms=duration)
        if outcome is not None:
            outcome.update(verification=result, tests=result.get("tests"))
        return

    _emit(events, phase, "tool_result", summarize(name, arguments, result), json.dumps(result)[:400],
          duration_ms=duration)


def summarize(name, args, result):
    """Short factual title for the timeline. Describes what came back, not what it means."""
    if "error" in result:
        return f"{name} failed: {str(result['error'])[:80]}"
    if name == "get_service_health":
        return (f"Service {result.get('status')}: failure rate {_pct(result.get('failure_rate'))}, "
                f"p95 {result.get('latency_p95_ms')} ms")
    if name == "get_metrics_history":
        return f"{result.get('count')} metric snapshots"
    if name == "search_logs":
        return f"{result.get('count')} log entries matching '{args.get('query')}'"
    if name == "inspect_stack_trace":
        return f"Stack trace: {str(result.get('exception'))[:80]}"
    if name == "get_recent_commits":
        return f"{len(result.get('commits', []))} recent commits"
    if name == "get_git_diff":
        return f"Diff of commit {str(args.get('sha'))[:7]}"
    if name == "read_config":
        return f"Read {result.get('path')}"
    if name == "read_source_file":
        return f"Read {args.get('path')}"
    return f"{name} returned"


def _emit(events, *args, **kwargs):
    if events is not None:
        events.emit(*args, **kwargs)


def _yn(value):
    return "yes" if value else "no"


def _pct(value):
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "?"
