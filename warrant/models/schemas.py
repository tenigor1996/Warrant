"""
models/schemas.py — Shared data structures.

WHAT IT DEFINES
    Plain dataclasses that every module uses to talk to every other module,
    so no module depends on another's internal formats.

        Event          watcher        → agent
        ToolCall       llm_client     → agent → executor / policy
        PolicyDecision policy         → executor
        ToolResult     executor       → agent → llm_client
        AgentStep      agent          → audit
        AuditEntry     agent/executor → audit_logger

    Fields below are placeholders and will be refined during implementation.

CONNECTS TO
    Every other package.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Event:
    """A normalized incoming trigger (alert, webhook, schedule, ...)."""
    id: str
    source: str
    type: str
    payload: dict = field(default_factory=dict)
    timestamp: Optional[str] = None


@dataclass
class ToolCall:
    """A tool invocation requested by the LLM."""
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class PolicyDecision:
    """Result of checking a ToolCall against policy."""
    allowed: bool
    reason: str = ""
    rule: Optional[str] = None


@dataclass
class ToolResult:
    """Structured outcome of a ToolCall (executed or denied)."""
    tool_call_id: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    decision: Optional[PolicyDecision] = None


@dataclass
class AgentStep:
    """One iteration of the agent loop."""
    step_number: int
    intent: str = ""
    tool_call: Optional[ToolCall] = None
    result: Optional[ToolResult] = None


@dataclass
class AuditEntry:
    """One immutable audit record."""
    timestamp: str
    event: Optional[Event] = None
    intent: str = ""
    requested_action: Optional[ToolCall] = None
    decision: Optional[PolicyDecision] = None
    result: Optional[ToolResult] = None
    state_change: Optional[str] = None
