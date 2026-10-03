"""
agent/backends.py — The runtime that drives the model for one incident.

    WARRANT_AGENT_BACKEND=openclaw   (default) hand the incident to the OpenClaw agent that
                                     NemoClaw already configured. OpenClaw talks to the local
                                     model and calls Warrant's tools over MCP (mcp_server.py).
                                     Warrant never contacts the model itself on this path.
    WARRANT_AGENT_BACKEND=direct     agent.py talks to vLLM itself (llm_client.py) and calls
                                     the Python tools in-process. Development / test fallback.

Both return the same thing: the diagnosis dict (status "diagnosed" or
"inconclusive") with a "remediation" summary and the backend name added.

There is no silent fallback. If OpenClaw is selected and cannot be run, the
incident ends "inconclusive" with the reason on the timeline.
"""

import json
import re
import shlex
import shutil
import subprocess

from warrant.agent import agent, config
from warrant.agent.events import EventStream
from warrant.agent.outcome import Outcome

BACKENDS = ("openclaw", "direct")

OPENCLAW_PROMPT = """\
You are the on-call site reliability engineer for the checkout service. An automated \
monitor detected a production incident and handed it to you. No human is available.

Use the tools of the "warrant" MCP server. Work in this order:

1. Investigate with the read-only tools (service health, metrics history, logs, stack \
trace, git commits and diffs, configuration, source files). Base every conclusion on tool \
output; a root cause needs more than one independent source of evidence.
2. Call submit_diagnosis with the root cause and the evidence for it.
3. Resolve the incident with a remediation tool. Every remediation is checked by a policy \
gate. If a result says DENIED, it was not applied: read the reason, do not repeat the same \
action, and choose a different approach that fits the permitted scope.
4. Call run_tests.
5. Call verify_recovery. If it reports the service has not recovered, correct the \
remediation and verify again.

Finish with a two-sentence summary of the root cause and what you changed.

Incident event from the monitor:
{event}
"""

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class BackendUnavailable(Exception):
    pass


class DirectBackend:
    name = "direct"

    def __init__(self, llm=None):
        self.llm = llm

    def check(self) -> None:
        pass  # an unreachable model surfaces per incident as "Local model unavailable"

    def describe(self) -> str:
        return f"direct vLLM at {config.LLM_BASE_URL}"

    def run(self, event: dict, events: EventStream, outcome: Outcome) -> dict:
        diagnosis = agent.investigate(event, llm=self.llm, events=events)
        outcome.update(diagnosis=diagnosis)
        result = {**diagnosis, "backend": self.name, "remediation": None}
        if diagnosis["status"] == "diagnosed" and config.AUTO_REMEDIATE:
            result["remediation"] = agent.remediate(diagnosis, llm=self.llm, events=events, outcome=outcome)
        return result


class OpenClawBackend:
    name = "openclaw"

    def __init__(self, command: str = None, runner=subprocess.run):
        self.command = command or config.OPENCLAW_CMD
        self.runner = runner

    def argv(self, message: str, session_id: str) -> list:
        values = {"agent": config.OPENCLAW_AGENT, "session_id": session_id, "message": message}
        template = shlex.split(self.command)
        if not template:
            raise BackendUnavailable("WARRANT_OPENCLAW_CMD is empty")
        if not any("{message}" in part for part in template):
            raise BackendUnavailable("WARRANT_OPENCLAW_CMD must contain {message}")
        # Substituted per argument, never through a shell, so the message cannot be reinterpreted.
        return [_fill(part, values) for part in template]

    def check(self) -> None:
        executable = self.argv("", "check")[0]
        if shutil.which(executable) is None:
            raise BackendUnavailable(
                f"OpenClaw command {executable!r} not found on PATH. Set WARRANT_OPENCLAW_CMD to the "
                "command that reaches the NemoClaw-managed OpenClaw agent, or set "
                "WARRANT_AGENT_BACKEND=direct to use vLLM directly.")

    def describe(self) -> str:
        return f"OpenClaw agent {config.OPENCLAW_AGENT!r} via `{shlex.split(self.command)[0]}`"

    def run(self, event: dict, events: EventStream, outcome: Outcome) -> dict:
        incident_id = event.get("incident_id") or "INC-UNKNOWN"
        agent.emit_detection(event, events)
        try:
            self.check()
            argv = self.argv(OPENCLAW_PROMPT.format(event=json.dumps(event, indent=2)), f"warrant-{incident_id}")
        except BackendUnavailable as exc:
            return self._give_up(incident_id, events, outcome, "OpenClaw unavailable", str(exc))

        events.emit("investigate", "status", "Investigation started",
                    f"{incident_id}: handed to {self.describe()}")
        try:
            completed = self.runner(argv, capture_output=True, text=True, cwd=config.OPENCLAW_CWD,
                                    timeout=config.OPENCLAW_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return self._give_up(incident_id, events, outcome, "OpenClaw timed out",
                                 f"no result within {config.OPENCLAW_TIMEOUT_SECONDS:g}s")
        except OSError as exc:
            return self._give_up(incident_id, events, outcome, "OpenClaw unavailable",
                                 f"could not run {argv[0]!r}: {exc}")
        if completed.returncode != 0:
            return self._give_up(incident_id, events, outcome, "OpenClaw run failed",
                                 f"exit code {completed.returncode}: {(completed.stderr or completed.stdout)[-400:]}")

        data = outcome.load()
        diagnosis = data.get("diagnosis")
        if not diagnosis:
            return self._give_up(incident_id, events, outcome, "Investigation inconclusive",
                                 "OpenClaw finished without calling submit_diagnosis. Check that the "
                                 "warrant MCP server is registered with OpenClaw.")
        return {**diagnosis, "backend": self.name, "remediation": _remediation_summary(data),
                "agent_summary": _THINK_RE.sub("", completed.stdout or "").strip()[-1200:]}

    def _give_up(self, incident_id, events, outcome, title, reason) -> dict:
        events.emit("investigate", "status", title, reason[:400])
        data = outcome.load()
        # Keep a diagnosis that was already submitted before the run broke off.
        base = data.get("diagnosis") or agent._inconclusive(incident_id, 0, data.get("tools_used", []), reason)
        return {**base, "reason": reason, "backend": self.name, "remediation": _remediation_summary(data)}


def get_backend(name: str = None):
    name = (name or config.BACKEND).strip().lower()
    if name == "openclaw":
        return OpenClawBackend()
    if name == "direct":
        return DirectBackend()
    raise ValueError(f"WARRANT_AGENT_BACKEND must be one of {BACKENDS}, got {name!r}")


def _remediation_summary(data: dict):
    verification = data.get("verification")
    if not data.get("actions_proposed") and not verification:
        return None
    recovered = bool(verification and verification.get("recovered"))
    return {"status": "resolved" if recovered else "unresolved",
            "final_status": "RECOVERED" if recovered else "NOT_RECOVERED",
            "verification": verification}


def _fill(part: str, values: dict) -> str:
    for key, value in values.items():
        part = part.replace("{" + key + "}", value)
    return part
