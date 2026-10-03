"""
tools/tool_registry.py — Registry of tools available to the LLM.

WHAT IT WILL DO
    - Maintain the list of tools the LLM is allowed to see.
    - Expose each tool's schema (name, description, JSON parameters) in the
      format agent/llm_client.py sends to the model.
    - Map tool names to the Python functions in system_tools.py / file_tools.py.
    - Allow different business agents to be given different subsets of tools.

RECEIVES
    - Tool registrations (name, description, parameter schema, function)
    - Lookups by tool name (from a ToolCall)

RETURNS
    - get_schemas(): list of tool schema dicts for the LLM
    - get(name): the tool's Python function (or None if unknown)

CONNECTS TO
    tools/system_tools.py, tools/file_tools.py — the tool implementations
    agent/agent.py          — asks for schemas and resolves ToolCalls
    agent/llm_client.py     — receives schemas (via the agent)
    security/executor.py    — tool functions route execution through it
"""


class ToolRegistry:
    """Holds tool definitions and maps names to functions. (Skeleton only.)"""

    def __init__(self):
        """Create an empty registry."""
        raise NotImplementedError

    def register(self, name: str, description: str, parameters: dict, func):
        """Add one tool."""
        raise NotImplementedError

    def get_schemas(self) -> list:
        """Return tool schemas for the LLM."""
        raise NotImplementedError

    def get(self, name: str):
        """Return the function registered under `name`."""
        raise NotImplementedError
