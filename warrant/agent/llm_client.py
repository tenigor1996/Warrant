"""
agent/llm_client.py — Client for the local LLM.

WHAT IT WILL DO
    - Connect to the local LLM at config.LLM_ENDPOINT using config.LLM_MODEL_NAME.
    - Send the conversation messages.
    - Expose tool definitions (from tools/tool_registry.py) to the model.
    - Receive the model's reply and parse any structured tool calls into
      models.schemas.ToolCall objects.

    IMPORTANT: this module NEVER executes commands or tools itself.
    It only translates between the Agent and the model. Execution happens
    exclusively in security/executor.py.

RECEIVES
    - messages: list of role/content dicts
    - tools: list of tool schemas (from ToolRegistry.get_schemas())

RETURNS
    An LLM response: either final text or one or more ToolCall objects.

CONNECTS TO
    agent/agent.py          — its only caller
    tools/tool_registry.py  — source of tool schemas (passed in by the agent)
    models/schemas.py       — ToolCall
    config.py               — endpoint, model name, timeout
"""


class LLMClient:
    """Thin wrapper around the local LLM API. (Skeleton only.)"""

    def __init__(self, endpoint: str, model: str, timeout: int):
        """Store connection settings. No network calls yet."""
        raise NotImplementedError

    def chat(self, messages, tools):
        """
        Send messages + tool definitions; return final text or ToolCall(s).
        Does not execute anything.
        """
        raise NotImplementedError
