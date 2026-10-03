"""
security/policy.py — Policy rules for what the agent may do.

WHAT IT WILL DEFINE
    - Allowed actions     : explicit allow-list of tool/action names.
    - Denied actions      : explicit deny-list (deny always wins).
    - Filesystem rules    : allowed/denied path prefixes, read vs write.
    - Command rules       : allowed binaries / argument patterns; blocked ones.
    - Network rules       : allowed hosts/ports, or no network at all.

    Default stance: DENY anything not explicitly allowed.

RECEIVES
    A models.schemas.ToolCall (the requested action and its arguments).

RETURNS
    A models.schemas.PolicyDecision (ALLOW or DENY + reason).

CONNECTS TO
    security/executor.py — its only caller; asks before every execution
    models/schemas.py    — ToolCall, PolicyDecision
    config.py            — paths / settings that inform rules
"""

# --- Placeholder rule sets (empty = nothing allowed) ---------------------------
ALLOWED_ACTIONS: set = set()
DENIED_ACTIONS: set = set()
ALLOWED_PATHS: list = []
DENIED_PATHS: list = []
ALLOWED_COMMANDS: list = []
DENIED_COMMANDS: list = []
ALLOWED_NETWORK_HOSTS: list = []


class Policy:
    """Evaluates a requested action against the rules. (Skeleton only.)"""

    def evaluate(self, tool_call):
        """Return a PolicyDecision for the given ToolCall. (Not implemented.)"""
        raise NotImplementedError
