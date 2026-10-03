"""
The MCP surface OpenClaw sees. Runs FastMCP in-process: no OpenClaw, no model.

Run from the repo root:  python -m pytest warrant/agent/tests -q
"""

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from warrant.agent import mcp_server, toolbox  # noqa: E402

from conftest import read_events  # noqa: E402

INVESTIGATION = {"get_service_health", "get_metrics_history", "search_logs", "inspect_stack_trace",
                 "get_recent_commits", "get_git_diff", "read_config", "read_source_file",
                 "list_repository_files"}
REMEDIATION = {"revert_commit_and_push", "apply_remediation", "run_tests", "verify_recovery"}


def listed():
    return {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}


def call_tool(name, arguments):
    """Call through the MCP layer and decode the JSON the client would receive."""
    result = asyncio.run(mcp_server.mcp.call_tool(name, arguments))
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_every_warrant_tool_is_exposed():
    names = set(listed())
    assert INVESTIGATION | REMEDIATION | {"submit_diagnosis"} == names
    assert set(toolbox.TOOLS) <= names  # nothing in the Python toolbox is missing from MCP


def test_descriptions_and_schemas_come_from_the_existing_tools():
    tools = listed()
    for schema in toolbox.TOOL_SCHEMAS:
        fn = schema["function"]
        assert tools[fn["name"]].description == fn["description"]
        assert set(tools[fn["name"]].inputSchema.get("properties", {})) == set(fn["parameters"]["properties"])
        assert set(tools[fn["name"]].inputSchema.get("required", [])) == set(fn["parameters"]["required"])


def test_tools_delegate_to_tools_py(repo, state):
    commits = call_tool("get_recent_commits", {"limit": 3})["commits"]
    assert len(commits) == 3 and commits[0]["message"].startswith("Add pager alias")
    assert "PAYMENT_TIMEOUT" in call_tool("read_config", {})["content"]
    assert "def charge" in call_tool("read_source_file", {"path": "checkout_service/payment_client.py"})["content"]
    assert "checkout_service/config.py" in call_tool("list_repository_files", {})["files"]


def test_errors_are_returned_not_raised(repo, state):
    assert "error" in call_tool("get_service_health", {})                      # service not running
    assert "error" in call_tool("search_logs", {"query": "timeout"})
    assert "error" in call_tool("read_source_file", {"path": "../../etc/passwd"})
    assert "error" in call_tool("get_git_diff", {"sha": "HEAD; rm -rf /"})
    assert "error" in call_tool("submit_diagnosis", {"root_cause": "", "confidence": "low", "evidence": [],
                                                    "recommended_next_step": ""})


def test_dangerous_action_is_denied_over_mcp(repo, state):
    result = call_tool("revert_commit_and_push", {"sha": "abc1234"})
    assert result["ok"] is False and result["detail"].startswith("DENIED: ")
    result = call_tool("apply_remediation", {"path": "checkout_service/payment_client.py",
                                             "change": {"PAYMENT_TIMEOUT": 5.0}})
    assert result["detail"].startswith("DENIED: write to checkout_service/payment_client.py")


def test_calls_are_written_to_the_timeline(repo, state):
    call_tool("read_config", {})
    call_tool("apply_remediation", {"path": "checkout_service/config.py",
                                    "change": {"PAYMENT_TIMEOUT": 5.0, "PAYMENT_RETRIES": 1}})
    events = read_events(state / "events.jsonl")
    assert [(e["phase"], e["kind"]) for e in events] == [
        ("investigate", "tool_call"), ("investigate", "tool_result"),
        ("act", "policy_decision"), ("act", "status")]
    assert events[2]["policy_verdict"] == "ALLOWED"


def test_stdout_stays_clean_for_the_mcp_protocol(repo, state, capsys):
    call_tool("read_config", {})
    captured = capsys.readouterr()
    assert captured.out == "" and "[AGENT]" in captured.err
