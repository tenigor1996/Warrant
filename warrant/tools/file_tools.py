"""
tools/file_tools.py — Placeholders for filesystem tools.

WHAT IT WILL DO
    Define filesystem tools the LLM can request, e.g. reading a file,
    tailing a log, listing a directory. All paths will be checked against
    security/policy.py filesystem restrictions before anything is touched,
    and access will go through security/executor.py.

RECEIVES
    Tool arguments (from a models.schemas.ToolCall), mostly paths.

RETURNS
    models.schemas.ToolResult (produced by the executor).

CONNECTS TO
    tools/tool_registry.py — registers these functions
    security/executor.py   — performs the (policy-checked) access
    security/policy.py     — filesystem allow/deny rules
    models/schemas.py      — ToolResult
"""

# Placeholder tools — no real file access is implemented.


def read_file(path: str):
    """Future: return file contents (size-limited). (Not implemented.)"""
    raise NotImplementedError


def tail_log(path: str, lines: int = 100):
    """Future: return the last N lines of a log. (Not implemented.)"""
    raise NotImplementedError


def list_directory(path: str):
    """Future: list entries in a directory. (Not implemented.)"""
    raise NotImplementedError
