"""
agent/mcp_server.py — Warrant's tools as an MCP server (FastMCP, mcp==1.30.0).

    local Qwen -> OpenClaw -> MCP -> this server -> tools.py / remediation.py

Nothing is reimplemented here. Each MCP tool is a typed signature (FastMCP
builds the input schema from it) that forwards to toolbox.execute(), which runs
the existing Python tool, writes the timeline event and applies the policy
gate. Descriptions come from the same schemas the direct backend uses.

Every tool returns a JSON object and never raises; failures are {"error": "..."}.

Run from the repo root (stdio transport, which is what OpenClaw spawns):
    python -m warrant.agent.mcp_server
WARRANT_MCP_TRANSPORT=sse|streamable-http serves over HTTP instead.
"""

import json
import os
import sys
from typing import Optional, Union

from mcp.server.fastmcp import FastMCP

from warrant.agent import agent, config, toolbox
from warrant.agent.events import EventStream
from warrant.agent.outcome import Outcome

mcp = FastMCP("warrant")

_DESCRIPTIONS = {s["function"]["name"]: s["function"]["description"]
                 for s in toolbox.TOOL_SCHEMAS + [agent.SUBMIT_DIAGNOSIS]}


def _events() -> EventStream:
    return EventStream(config.EVENTS_FILE, echo=sys.stderr)  # stdout is the MCP channel


def _outcome() -> Outcome:
    return Outcome(config.OUTCOME_FILE)


def _call(name: str, **arguments) -> dict:
    arguments = {k: v for k, v in arguments.items() if v is not None}
    try:
        return toolbox.execute(name, arguments, _events(), _outcome())
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _tool(fn):
    return mcp.tool(name=fn.__name__, description=_DESCRIPTIONS[fn.__name__])(fn)


# ---- investigation (read-only, tools.py) ---------------------------------------

@_tool
def get_service_health() -> dict:
    return _call("get_service_health")


@_tool
def get_metrics_history(since: Optional[str] = None) -> dict:
    return _call("get_metrics_history", since=since)


@_tool
def search_logs(query: str, since: Optional[str] = None, limit: Optional[int] = None) -> dict:
    return _call("search_logs", query=query, since=since, limit=limit)


@_tool
def inspect_stack_trace() -> dict:
    return _call("inspect_stack_trace")


@_tool
def get_recent_commits(limit: int = 10) -> dict:
    return _call("get_recent_commits", limit=limit)


@_tool
def get_git_diff(sha: str) -> dict:
    return _call("get_git_diff", sha=sha)


@_tool
def read_config() -> dict:
    return _call("read_config")


@_tool
def read_source_file(path: str) -> dict:
    return _call("read_source_file", path=path)


@_tool
def list_repository_files() -> dict:
    return _call("list_repository_files")


# ---- diagnosis -----------------------------------------------------------------

@_tool
def submit_diagnosis(root_cause: str, confidence: str, evidence: list, recommended_next_step: str,
                     offending_commit: Optional[str] = None) -> dict:
    try:
        args = {"root_cause": root_cause, "confidence": confidence, "evidence": evidence,
                "offending_commit": offending_commit, "recommended_next_step": recommended_next_step}
        problem = agent.validate_diagnosis(args)
        if problem:
            return {"error": problem}
        outcome = _outcome()
        data = outcome.load()
        tools_used = data.get("tools_used", [])
        incident_id = data.get("incident_id") or "INC-UNKNOWN"
        diagnosis = agent.build_diagnosis(incident_id, args, len(tools_used), tools_used, _events())
        outcome.update(diagnosis=diagnosis)
        config.DIAGNOSIS_FILE.write_text(json.dumps(diagnosis, indent=2))
        return {"accepted": True, "incident_id": incident_id,
                "next": "resolve the incident with a remediation tool, then run_tests and verify_recovery"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---- remediation and verification (remediation.py, policy-gated) ---------------

@_tool
def revert_commit_and_push(sha: str) -> dict:
    return _call("revert_commit_and_push", sha=sha)


@_tool
def apply_remediation(path: str, change: Union[dict, str]) -> dict:
    return _call("apply_remediation", path=path, change=change)


@_tool
def run_tests() -> dict:
    return _call("run_tests")


@_tool
def verify_recovery() -> dict:
    return _call("verify_recovery")


def main():
    mcp.run(transport=os.environ.get("WARRANT_MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
