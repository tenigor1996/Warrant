"""
agent/agent.py — Autonomous incident investigation loop.

    incident event -> local LLM -> tool call -> evidence -> ... -> diagnosis

On every turn the model sees the incident, the tool definitions and all
evidence gathered so far, and decides for itself whether to call another
read-only tool or to submit a diagnosis. There is no scripted order of tools
and nothing about the expected cause in the prompt.

Stops when the model calls submit_diagnosis, or after MAX_STEPS model turns
(result status "inconclusive").

Read-only by design: remediation, policy enforcement, verification and the
final report are later stages.
"""

import json
import time
from datetime import datetime, timezone

from warrant.agent import config
from warrant.agent.events import EventStream
from warrant.agent.llm_client import LLMClient, LLMError
from warrant.agent.tools import TOOL_SCHEMAS, run_tool

MAX_TOOL_RESULT_CHARS = 12000
MAX_NUDGES = 2

SYSTEM_PROMPT = """\
You are an on-call site reliability engineer investigating a production incident \
in the checkout service. An automated monitor detected the incident and handed it \
to you. No human is available to guide you.

You have read-only tools for the service's metrics, application logs, stack traces \
and its git repository (recent commits, diffs, configuration and source code). \
Decide which tool to call next based on what you have learned so far.

Rules:
- Base every conclusion on tool output. Do not assume a cause before you have evidence for it.
- A root cause must be supported by more than one independent source of evidence and \
must explain the observed symptoms and when they started.
- This is an investigation only. You cannot change anything; do not attempt a fix.
- Be efficient. When the evidence is sufficient, call submit_diagnosis. \
Otherwise keep investigating.
"""

SUBMIT_DIAGNOSIS = {"type": "function", "function": {
    "name": "submit_diagnosis",
    "description": "Finish the investigation with a root-cause diagnosis backed by the evidence you gathered.",
    "parameters": {
        "type": "object",
        "properties": {
            "root_cause": {"type": "string", "description": "The root cause and the causal chain to the symptoms."},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "evidence": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "source": {"type": "string",
                               "enum": ["metrics", "logs", "stack_trace", "git", "config", "source_code"]},
                    "finding": {"type": "string"},
                }, "required": ["source", "finding"]},
            },
            "offending_commit": {"type": "string", "description": "Sha of the change responsible, if any."},
            "recommended_next_step": {"type": "string", "description": "What remediation should do next."},
        },
        "required": ["root_cause", "confidence", "evidence", "recommended_next_step"],
    },
}}

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
}


def investigate(event: dict, llm=None, events: EventStream = None, max_steps: int = config.MAX_STEPS) -> dict:
    llm = llm or LLMClient()
    events = events or EventStream(config.EVENTS_FILE)
    incident_id = event.get("incident_id") or datetime.now(timezone.utc).strftime("INC-%Y%m%d-%H%M%S")
    tools = TOOL_SCHEMAS + [SUBMIT_DIAGNOSIS]
    tools_used = []

    events.emit("detect", "status", "Incident received",
                f"{event.get('service', 'checkout')} {event.get('status', '')}: "
                f"failure rate {_pct(event.get('failure_rate'))}, p95 {event.get('latency_p95_ms')} ms")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Incident event from the monitor:\n" + json.dumps(event, indent=2)
                                    + "\n\nInvestigate and find the root cause."},
    ]

    nudges = 0
    for step in range(1, max_steps + 1):
        try:
            reply = llm.chat(messages, tools)
        except LLMError as exc:
            events.emit("investigate", "status", "Local model unavailable", str(exc)[:400])
            return _inconclusive(incident_id, step - 1, tools_used, f"LLM error: {exc}")

        calls = reply["tool_calls"]
        messages.append(_assistant_message(reply))
        if reply["content"]:
            events.emit("investigate", "reasoning", "Agent reasoning", reply["content"][:600])

        if not calls:
            nudges += 1
            if nudges > MAX_NUDGES:
                break
            messages.append({"role": "user", "content":
                             "Continue: call an investigation tool, or call submit_diagnosis "
                             "if the evidence is sufficient."})
            continue

        for call in calls:
            if call["name"] == "submit_diagnosis":
                problem = _validate_diagnosis(call["arguments"])
                if problem is None:
                    return _finish(incident_id, call["arguments"], step, tools_used, events)
                result = {"error": problem}
            else:
                args_text = ", ".join(f"{k}={v!r}" for k, v in call["arguments"].items())
                events.emit("investigate", "tool_call", TOOL_TITLES.get(call["name"], call["name"]),
                            f"{call['name']}({args_text})")
                started = time.monotonic()
                result = run_tool(call["name"], call["arguments"])
                duration = int((time.monotonic() - started) * 1000)
                tools_used.append(call["name"])
                events.emit("investigate", "tool_result", _summarize(call["name"], call["arguments"], result),
                            json.dumps(result)[:400], duration_ms=duration)
            messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                             "content": json.dumps(result)[:MAX_TOOL_RESULT_CHARS]})

    events.emit("diagnose", "status", "Investigation inconclusive", f"no diagnosis after {max_steps} steps")
    return _inconclusive(incident_id, max_steps, tools_used, "step limit reached")


def _finish(incident_id, args, steps, tools_used, events):
    diagnosis = {
        "incident_id": incident_id,
        "status": "diagnosed",
        "root_cause": args["root_cause"],
        "confidence": args.get("confidence", "medium"),
        "evidence": args.get("evidence", []),
        "offending_commit": args.get("offending_commit"),
        "recommended_next_step": args["recommended_next_step"],
        "steps": steps,
        "tools_used": tools_used,
        "diagnosed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    events.emit("diagnose", "reasoning", "Root cause identified", diagnosis["root_cause"])
    return diagnosis


def _inconclusive(incident_id, steps, tools_used, reason):
    return {"incident_id": incident_id, "status": "inconclusive", "root_cause": None,
            "confidence": "low", "evidence": [], "recommended_next_step": "escalate to a human",
            "reason": reason, "steps": steps, "tools_used": tools_used}


def _validate_diagnosis(args):
    if not str(args.get("root_cause", "")).strip():
        return "root_cause is required"
    if not args.get("evidence"):
        return "evidence must list the findings that support the root cause"
    if not str(args.get("recommended_next_step", "")).strip():
        return "recommended_next_step is required"
    return None


def _assistant_message(reply):
    message = {"role": "assistant", "content": reply["content"] or None}
    if reply["tool_calls"]:
        message["tool_calls"] = [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
            for c in reply["tool_calls"]
        ]
    return message


def _summarize(name, args, result):
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


def _pct(value):
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "?"
