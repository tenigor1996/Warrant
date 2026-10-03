"""
tools/system_tools.py — Placeholders for system-level tools.

WHAT IT WILL DO
    Define system tools the LLM can request, e.g. inspecting processes,
    services, disk usage, or restarting a service. Each tool will:
      - accept validated arguments from a ToolCall,
      - describe the intended action,
      - route the actual execution through security/executor.py
        (never subprocess / os.system directly).

RECEIVES
    Tool arguments (from a models.schemas.ToolCall).

RETURNS
    models.schemas.ToolResult (produced by the executor).

CONNECTS TO
    tools/tool_registry.py — registers these functions
    security/executor.py   — performs the (policy-checked) execution
    models/schemas.py      — ToolResult
"""

# Placeholder tools — no real commands are implemented.


def get_system_status(**kwargs):
    """Future: report CPU / memory / uptime. (Not implemented.)"""
    raise NotImplementedError


def list_processes(**kwargs):
    """Future: list running processes. (Not implemented.)"""
    raise NotImplementedError


def check_service(name: str):
    """Future: report status of a named service. (Not implemented.)"""
    raise NotImplementedError


def restart_service(name: str):
    """Future: restart a named service — must pass policy. (Not implemented.)"""
    raise NotImplementedError
