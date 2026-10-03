"""
tools — The capabilities the LLM may request.

tool_registry.py : list of tools, their schemas, and name → function mapping.
system_tools.py  : placeholders for system/inspection tools.
file_tools.py    : placeholders for filesystem tools.

Tools describe *what* an action is. They never bypass security/executor.py.
"""
