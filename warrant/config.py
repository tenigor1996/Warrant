"""
config.py — Central configuration for Warrant.

WHAT IT WILL DO
    Hold every tunable setting in one place. Later this may read overrides
    from environment variables or a config file; for now it is constants only.

RECEIVES
    Nothing (may later read env vars / a config file).

RETURNS
    Module-level constants imported by other modules.

CONNECTS TO
    main.py (builds everything from these values),
    agent/llm_client.py (LLM endpoint, model),
    agent/agent.py (step limit),
    events/watcher.py (event source path),
    security/executor.py + security/policy.py (OpenShell settings, allowed paths),
    audit/audit_logger.py (log paths, level).
"""

from pathlib import Path

# --- Paths ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ALERTS_FILE = DATA_DIR / "alerts.jsonl"      # event source read by the watcher
AUDIT_LOG_FILE = DATA_DIR / "audit.jsonl"    # written by the audit logger

# --- Local LLM ----------------------------------------------------------------
LLM_ENDPOINT = "http://localhost:11434"      # placeholder local LLM URL
LLM_MODEL_NAME = "TODO-model-name"
LLM_TIMEOUT_SECONDS = 60

# --- Agent ------------------------------------------------------------------
MAX_AGENT_STEPS = 10                          # hard cap on plan/act iterations

# --- OpenShell --------------------------------------------------------------
OPENSHELL_ENABLED = False                     # execution is disabled until implemented
OPENSHELL_BINARY = "openshell"                # placeholder
OPENSHELL_TIMEOUT_SECONDS = 30
OPENSHELL_WORKDIR = BASE_DIR

# --- Logging ----------------------------------------------------------------
LOG_LEVEL = "INFO"
