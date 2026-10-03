"""
agent — Autonomous incident investigation.

server.py      HTTP intake (:8082): POST /investigate, GET /status, GET /diagnosis
agent.py       the LLM tool-calling loop
llm_client.py  local model client (vLLM OpenAI-compatible API); never executes anything
tools.py       read-only evidence tools
events.py      investigation timeline -> state/events.jsonl
"""
