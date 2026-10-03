"""
agent — Autonomous incident investigation, remediation and verification.

server.py       HTTP intake (:8082): POST /investigate, GET /status, GET /diagnosis, GET /outcome
backends.py     which runtime drives the model: openclaw (default) or direct
mcp_server.py   the tools as an MCP server, for OpenClaw
agent.py        direct backend: the LLM tool-calling loop
llm_client.py   direct backend: local model client (vLLM OpenAI-compatible API)
tools.py        read-only evidence tools
remediation.py  policy-gated remediation tools, run_tests, verify_recovery
toolbox.py      runs a tool call and records it on the timeline (shared by both backends)
events.py       timeline -> state/events.jsonl
outcome.py      per-incident result -> state/outcome.json
"""
