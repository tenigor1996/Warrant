"""
Loop mechanics with a scripted stand-in for the model. Whether the real local
model reaches the right diagnosis can only be checked against vLLM on the GB10.

Run from the repo root:  python -m pytest warrant/agent/tests -q
"""

import json

import pytest

from warrant.agent import agent, config, tools
from warrant.agent.events import EventStream
from warrant.agent.llm_client import LLMClient, LLMError
from warrant.sim import workspace

EVENT = {"incident_id": "INC-TEST", "event_type": "service_degradation", "service": "checkout",
         "status": "degraded", "failure_rate": 0.68, "latency_p95_ms": 3400, "errors_last_minute": 160}

GOOD_DIAGNOSIS = {"root_cause": "x", "confidence": "high",
                  "evidence": [{"source": "git", "finding": "y"}], "recommended_next_step": "z"}


class ScriptedLLM:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen = []

    def chat(self, messages, tool_schemas):
        self.seen.append(json.loads(json.dumps(messages)))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def call(name, **args):
    return {"content": "", "tool_calls": [{"id": f"c_{name}", "name": name, "arguments": args}], "raw": {}}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    ws = tmp_path / "checkout-service"
    workspace.build(ws)
    workspace.trigger_incident(ws)
    monkeypatch.setattr(config, "SERVICE_REPO", ws)
    monkeypatch.setattr(config, "CHECKOUT_URL", "http://127.0.0.1:9")  # nothing listens
    return ws


@pytest.fixture
def stream(tmp_path):
    s = EventStream(tmp_path / "events.jsonl")
    s.start_new()
    return s


def read_events(stream):
    return [json.loads(line) for line in stream.path.read_text().splitlines()]


def test_runs_requested_tools_then_returns_diagnosis(repo, stream):
    llm = ScriptedLLM(call("get_recent_commits", limit=3), call("read_config"),
                      call("submit_diagnosis", **GOOD_DIAGNOSIS))
    result = agent.investigate(EVENT, llm=llm, events=stream)

    assert result["status"] == "diagnosed"
    assert result["incident_id"] == "INC-TEST"
    assert result["tools_used"] == ["get_recent_commits", "read_config"]
    tool_msgs = [m for m in llm.seen[-1] if m["role"] == "tool"]
    assert "Tune payment client" in tool_msgs[0]["content"]
    assert "PAYMENT_TIMEOUT" in tool_msgs[1]["content"]

    kinds = [(e["phase"], e["kind"]) for e in read_events(stream)]
    assert kinds[0] == ("detect", "status")
    assert ("investigate", "tool_call") in kinds and ("investigate", "tool_result") in kinds
    assert kinds[-1] == ("diagnose", "reasoning")


def test_prompt_does_not_leak_the_answer():
    text = (agent.SYSTEM_PROMPT + json.dumps(tools.TOOL_SCHEMAS)).lower()
    for word in ("payment_timeout", "payment_retries", "retry storm", "0.01"):
        assert word not in text


def test_step_limit_returns_inconclusive(repo, stream):
    llm = ScriptedLLM(*[call("read_config")] * 3)
    result = agent.investigate(EVENT, llm=llm, events=stream, max_steps=3)
    assert result["status"] == "inconclusive"


def test_llm_failure_does_not_crash(repo, stream):
    result = agent.investigate(EVENT, llm=ScriptedLLM(LLMError("connection refused")), events=stream)
    assert result["status"] == "inconclusive"
    assert "LLM error" in result["reason"]


def test_incomplete_diagnosis_is_sent_back(repo, stream):
    llm = ScriptedLLM(call("submit_diagnosis", root_cause="x", confidence="high", evidence=[],
                           recommended_next_step="z"),
                      call("submit_diagnosis", **GOOD_DIAGNOSIS))
    result = agent.investigate(EVENT, llm=llm, events=stream)
    assert result["status"] == "diagnosed"
    assert "evidence" in llm.seen[-1][-1]["content"]


def test_unknown_tool_and_unreachable_service_return_errors(repo):
    assert "error" in tools.run_tool("rm_rf", {})
    assert "error" in tools.run_tool("get_service_health", {})
    assert "error" in tools.run_tool("search_logs", {})  # missing query


def test_git_tools_find_the_config_change(repo):
    commits = tools.get_recent_commits(limit=10)["commits"]
    assert len(commits) == 7
    bad = next(c for c in commits if "checkout_service/config.py" in c["files"]
               and c["message"].startswith("Tune"))
    diff = tools.get_git_diff(sha=bad["sha"])["diff"]
    assert "+PAYMENT_TIMEOUT = 0.01" in diff and "+PAYMENT_RETRIES = 5" in diff
    assert "error" in tools.get_git_diff(sha="HEAD; rm -rf /")


def test_read_source_file_stays_inside_repo(repo):
    assert "def charge" in tools.read_source_file(path="checkout_service/payment_client.py")["content"]
    assert "error" in tools.read_source_file(path="../../etc/passwd")
    assert "error" in tools.read_source_file(path=".git/config")


def test_llm_client_parses_text_tool_calls(monkeypatch):
    client = LLMClient(model="m")
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"choices": [{"message": {
        "content": '<think>hmm</think>Checking.\n<tool_call>{"name": "read_config", "arguments": {}}</tool_call>'
    }}]})
    reply = client.chat([], [])
    assert reply["tool_calls"][0]["name"] == "read_config"
    assert reply["content"] == "Checking."
