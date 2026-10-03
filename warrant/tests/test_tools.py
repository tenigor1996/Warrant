"""
tests/test_tools.py — Future tests for tools/.

Planned cases:
    - ToolRegistry registers tools and returns their schemas.
    - ToolRegistry.get() resolves names; unknown names return None.
    - Tool functions route through the executor, never execute directly.
"""
