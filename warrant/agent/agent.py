"""
agent/agent.py — The Agent: drives the plan → act → observe loop for one event.

WHAT IT WILL DO
    For each incoming Event:
      1. Receive the Event (from events/watcher.py via main.py).
      2. Build the initial context (event + task instructions) and send it,
         together with the available tool definitions, to the LLM.
      3. Receive from the LLM either a final answer or a requested ToolCall.
      4. Pass the ToolCall to the tool/security layer
         (tools/tool_registry.py → security/executor.py).
      5. Receive a ToolResult (which may be a policy DENY).
      6. Send the ToolResult back to the LLM so it can re-plan.
      7. Repeat until the LLM says the task is complete OR
         config.MAX_AGENT_STEPS is reached.
      8. Record every step (AgentStep) with audit/audit_logger.py.

    The Agent is business-agnostic: different business agents (IT incident
    remediation, security response, compliance monitoring, ...) should differ
    only in their system prompt and the tools they are given.

RECEIVES
    models.schemas.Event

RETURNS
    A list of models.schemas.AgentStep describing what happened
    (or a final summary built from them).

CONNECTS TO
    agent/llm_client.py      — talks to the LLM
    tools/tool_registry.py   — tool schemas + name → function lookup
    security/executor.py     — the only path to real execution
    audit/audit_logger.py    — records every step
    models/schemas.py        — Event, ToolCall, ToolResult, AgentStep
    config.py                — MAX_AGENT_STEPS
"""


class Agent:
    """Runs the reasoning loop for a single event. (Skeleton only.)"""

    def __init__(self, llm_client, tool_registry, executor, audit_logger, max_steps: int):
        """Store collaborators. Nothing is created here — main.py injects them."""
        raise NotImplementedError

    def handle_event(self, event):
        """
        Entry point: process one Event until done or step limit reached.
        Receives: Event. Returns: list[AgentStep].
        """
        raise NotImplementedError

    def _step(self, messages):
        """
        One iteration: ask LLM → maybe dispatch ToolCall → collect ToolResult.
        Receives: conversation messages. Returns: AgentStep.
        """
        raise NotImplementedError
