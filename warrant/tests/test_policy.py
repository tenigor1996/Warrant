"""
tests/test_policy.py — Future tests for security/policy.py and executor.py.

Planned cases:
    - Unknown actions are denied by default.
    - Deny-list wins over allow-list.
    - Paths outside allowed prefixes are denied.
    - Blocked commands / network hosts are denied.
    - Executor does not call OpenShell when the decision is DENY.
"""
