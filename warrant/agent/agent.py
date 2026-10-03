"""
agent/agent.py — Autonomous incident investigation loop.

    incident event -> local LLM -> tool call -> evidence -> ... -> diagnosis

On every turn the model sees the incident, the tool definitions and all
evidence gathered so far, and decides for itself whether to call another
read-only tool or to submit a diagnosis. There is no scripted order of tools
and nothing about the expected cause in the prompt.

Stops when the model calls submit_diagnosis, or after MAX_STEPS model turns
(result status "inconclusive").

investigate() is read-only. remediate() is the follow-on stage for the direct
backend: the model proposes a fix through the policy-gated remediation tools,
sees any denial, replans, and finishes by verifying recovery.

This module is the direct-vLLM runtime. With the OpenClaw backend the same
tools are reached over MCP instead (mcp_server.py) and OpenClaw runs the loop.
"""

import json
from datetime import datetime, timezone

from warrant.agent import config, remediation, toolbox
from warrant.agent.events import EventStream
from warrant.agent.llm_client import LLMClient, LLMError
from warrant.agent.outcome import final_status
from warrant.agent.tools import TOOL_SCHEMAS

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

REMEDIATION_PROMPT = """\
You are an on-call site reliability engineer. You have diagnosed a production \
incident in the checkout service and must now resolve it. No human is available.

You have the read-only investigation tools plus remediation and verification tools.

Rules:
- Choose the remediation that addresses the diagnosed root cause.
- Every remediation is checked by a policy gate. If a result says DENIED, it was not \
applied: read the reason, do not repeat the same action, and choose a different approach \
that fits the permitted scope.
- After a remediation is applied, call run_tests, then call verify_recovery.
- If verify_recovery reports the service has not recovered, use what it reports to correct \
the remediation and verify again.
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

def investigate(event: dict, llm=None, events: EventStream = None, max_steps: int = config.MAX_STEPS) -> dict:
    llm = llm or LLMClient()
    events = events or EventStream(config.EVENTS_FILE)
    incident_id = event.get("incident_id") or datetime.now(timezone.utc).strftime("INC-%Y%m%d-%H%M%S")
    tools_used = []

    emit_detection(event, events)
    events.emit("investigate", "status", "Investigation started", f"{incident_id}: read-only evidence gathering")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Incident event from the monitor:\n" + json.dumps(event, indent=2)
                                    + "\n\nInvestigate and find the root cause."},
    ]

    def on_call(call, step):
        if call["name"] == "submit_diagnosis":
            problem = validate_diagnosis(call["arguments"])
            if problem is None:
                return None, build_diagnosis(incident_id, call["arguments"], step, tools_used, events)
            return {"error": problem}, None
        if call["name"] in remediation.TOOLS:  # investigation is read-only
            return {"error": f"{call['name']} is not available during investigation"}, None
        tools_used.append(call["name"])
        return toolbox.execute(call["name"], call["arguments"], events), None

    diagnosis, steps, error = _run_loop(
        llm, messages, TOOL_SCHEMAS + [SUBMIT_DIAGNOSIS], events, max_steps, "investigate", on_call,
        "Continue: call an investigation tool, or call submit_diagnosis if the evidence is sufficient.")
    if diagnosis is not None:
        return diagnosis
    if error is not None:
        return _inconclusive(incident_id, steps, tools_used, f"LLM error: {error}")
    events.emit("diagnose", "status", "Investigation inconclusive", f"no diagnosis after {max_steps} steps")
    return _inconclusive(incident_id, max_steps, tools_used, "step limit reached")


def remediate(diagnosis: dict, llm=None, events: EventStream = None, outcome=None,
              max_steps: int = config.MAX_STEPS) -> dict:
    """Direct backend: propose a fix through the policy gate, then verify. Returns a remediation summary."""
    llm = llm or LLMClient()
    events = events or EventStream(config.EVENTS_FILE)
    events.emit("plan", "status", "Remediation started", "policy-gated remediation, then tests and health check")

    summary = {k: diagnosis.get(k) for k in ("root_cause", "confidence", "evidence", "offending_commit",
                                             "recommended_next_step")}
    messages = [
        {"role": "system", "content": REMEDIATION_PROMPT},
        {"role": "user", "content": "Diagnosis:\n" + json.dumps(summary, indent=2)
                                    + "\n\nResolve the incident and verify recovery."},
    ]
    tools_used = []
    last_verification = []  # the most recent verify_recovery that produced a verdict

    def on_call(call, step):
        tools_used.append(call["name"])
        result = toolbox.execute(call["name"], call["arguments"], events, outcome)
        if call["name"] == "verify_recovery" and "error" not in result:
            last_verification[:] = [result]
        done = call["name"] == "verify_recovery" and result.get("recovered") is True
        return result, (result if done else None)

    verification, steps, error = _run_loop(
        llm, messages, TOOL_SCHEMAS + remediation.TOOL_SCHEMAS, events, max_steps, "act", on_call,
        "Continue: apply a remediation, or call run_tests and verify_recovery if one has been applied.")
    if verification is not None:
        return {"status": "resolved", "final_status": final_status(verification), "verification": verification,
                "steps": steps, "tools_used": tools_used}
    reason = f"LLM error: {error}" if error is not None else "step limit reached without verified recovery"
    events.emit("verify", "status", "Incident not resolved", reason[:400])
    verification = last_verification[0] if last_verification else None
    return {"status": "unresolved", "final_status": final_status(verification), "verification": verification,
            "reason": reason, "steps": steps, "tools_used": tools_used}


def _run_loop(llm, messages, tool_schemas, events, max_steps, phase, on_call, nudge):
    """
    Model turns until on_call returns a final value. on_call(call, step) -> (tool result, final or None).
    Returns (final or None, steps taken, LLMError or None).
    """
    nudges = 0
    for step in range(1, max_steps + 1):
        try:
            reply = llm.chat(messages, tool_schemas)
        except LLMError as exc:
            events.emit(phase, "status", "Local model unavailable", str(exc)[:400])
            return None, step - 1, exc

        calls = reply["tool_calls"]
        messages.append(_assistant_message(reply))
        if reply["content"]:
            # The model's visible message only; llm_client strips <think> blocks.
            events.emit(phase, "reasoning", "Agent note", reply["content"][:300])

        if not calls:
            nudges += 1
            if nudges > MAX_NUDGES:
                break
            messages.append({"role": "user", "content": nudge})
            continue

        for call in calls:
            result, final = on_call(call, step)
            if final is not None:
                return final, step, None
            messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                             "content": json.dumps(result)[:MAX_TOOL_RESULT_CHARS]})
    return None, max_steps, None


def emit_detection(event: dict, events: EventStream) -> None:
    events.emit("detect", "status", "Incident received",
                f"{event.get('service', 'checkout')} {event.get('status', '')}: "
                f"failure rate {_pct(event.get('failure_rate'))}, p95 {event.get('latency_p95_ms')} ms")


def build_diagnosis(incident_id, args, steps, tools_used, events):
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


def validate_diagnosis(args):
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


def _pct(value):
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "?"
