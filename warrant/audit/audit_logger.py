"""
audit/audit_logger.py — Append-only audit trail.

WHAT IT WILL RECORD (one AuditEntry per agent step / decision)
    - event            : the triggering Event
    - agent intent     : what the agent/LLM said it was trying to do
    - requested action : the ToolCall
    - policy decision  : ALLOW / DENY + reason
    - execution result : the ToolResult
    - state change     : what changed on the system, if anything

    Entries will be written as JSON lines to config.AUDIT_LOG_FILE so they
    can be replayed, reviewed, and used for compliance evidence.

RECEIVES
    models.schemas.AuditEntry (or the pieces to build one).

RETURNS
    Nothing.

CONNECTS TO
    agent/agent.py        — logs each AgentStep
    security/executor.py  — logs policy decisions and execution results
    models/schemas.py     — AuditEntry
    config.py             — AUDIT_LOG_FILE, LOG_LEVEL
"""


class AuditLogger:
    """Writes audit entries to an append-only log. (Skeleton only.)"""

    def __init__(self, log_path):
        """Store the audit log location."""
        raise NotImplementedError

    def record(self, entry):
        """Append one AuditEntry. (Not implemented.)"""
        raise NotImplementedError
