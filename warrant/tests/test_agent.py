"""
tests/test_agent.py — Future tests for agent/agent.py.

Planned cases (using a fake LLMClient and fake Executor):
    - Agent stops when the LLM returns a final answer.
    - Agent stops at MAX_AGENT_STEPS.
    - A ToolCall is dispatched to the executor and its ToolResult fed back.
    - A DENY result is returned to the LLM so it can re-plan.
    - Every step is sent to the AuditLogger.
"""
