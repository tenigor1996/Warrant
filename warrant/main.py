"""
main.py — Entry point and top-level coordinator for Warrant.

WHAT IT WILL DO
    Wire the modules together and run the event loop:

        watcher  →  agent  →  tools  →  security/execution  →  audit

    1. Load settings from config.py.
    2. Build the shared components (LLMClient, ToolRegistry, Policy,
       Executor, AuditLogger).
    3. Build the Agent and hand it those components.
    4. Start the EventWatcher and, for every Event it yields, call
       agent.handle_event(event).

RECEIVES
    Nothing directly (may later accept CLI args, e.g. --config, --once).

RETURNS
    Process exit code.

CONNECTS TO
    config.py, events/watcher.py, agent/agent.py, agent/llm_client.py,
    tools/tool_registry.py, security/policy.py, security/executor.py,
    audit/audit_logger.py
"""


def main() -> int:
    """Build all components and run the watcher → agent loop. (Not implemented.)"""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
