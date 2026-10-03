"""
security/executor.py — The execution boundary.

This is the boundary between:
    "the LLM wants to do something"
and
    "the machine actually executes something".

Nothing in Warrant should execute anything except through this module.

FUTURE FLOW
    requested action (ToolCall)
      → check policy (security/policy.py)
      → ALLOW or DENY
      → if allowed, execute through OpenShell
      → return structured ToolResult
    Every decision and result is also sent to audit/audit_logger.py.

RECEIVES
    models.schemas.ToolCall

RETURNS
    models.schemas.ToolResult (includes the PolicyDecision; on DENY,
    no execution happens and the result explains why).

CONNECTS TO
    security/policy.py      — decides ALLOW / DENY
    OpenShell               — sandboxed execution backend (not implemented)
    audit/audit_logger.py   — records decision + result
    tools/*                 — tool functions route through here
    agent/agent.py          — receives the ToolResult
    models/schemas.py       — ToolCall, ToolResult, PolicyDecision
    config.py               — OPENSHELL_* settings
"""


class Executor:
    """Policy-gated executor. (Skeleton only.)"""

    def __init__(self, policy, audit_logger):
        """Store the Policy and AuditLogger."""
        raise NotImplementedError

    def execute(self, tool_call):
        """
        Check policy; if allowed, run via OpenShell; return ToolResult.
        (Not implemented.)
        """
        raise NotImplementedError

    def _run_in_openshell(self, tool_call):
        """Future: the only place a real OpenShell call is made. (Not implemented.)"""
        raise NotImplementedError
