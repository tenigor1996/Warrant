"""Agent settings. Every value can be overridden with an environment variable."""

import os
import sys
from pathlib import Path

WARRANT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("WARRANT_STATE_DIR", WARRANT_DIR / "state"))

# Which runtime drives the model.
#   openclaw  watcher -> :8082 -> OpenClaw (NemoClaw) -> local Qwen -> MCP -> Warrant tools
#   direct    agent.py -> llm_client.py -> vLLM -> Python tools   (development / test fallback)
BACKEND = os.environ.get("WARRANT_AGENT_BACKEND", "openclaw").strip().lower()

# OpenClaw runtime. The command is split with shlex and run without a shell;
# {agent}, {session_id} and {message} are substituted per argument. Override the
# whole command if OpenClaw has to be reached through the sandbox, e.g. by
# prefixing whatever you use to exec inside the NemoClaw sandbox.
OPENCLAW_CMD = os.environ.get(
    "WARRANT_OPENCLAW_CMD",
    "openclaw agent --agent {agent} --local --session-id {session_id} --message {message}",
)
OPENCLAW_AGENT = os.environ.get("WARRANT_OPENCLAW_AGENT", "main")
OPENCLAW_TIMEOUT_SECONDS = float(os.environ.get("WARRANT_OPENCLAW_TIMEOUT", "900"))
OPENCLAW_CWD = os.environ.get("WARRANT_OPENCLAW_CWD") or None

# Direct backend only: local model served by vLLM (OpenAI-compatible API) on the GB10.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")
# Empty = use the first model the server reports at /v1/models.
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT", "180"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "2048"))

MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "12"))
# Direct backend: continue from diagnosis into remediation and verification.
AUTO_REMEDIATE = os.environ.get("WARRANT_AUTO_REMEDIATE", "1") not in ("0", "false", "no")

# Evidence sources.
CHECKOUT_URL = os.environ.get("CHECKOUT_URL", "http://localhost:8081")
SERVICE_REPO = Path(os.environ.get("SERVICE_REPO", STATE_DIR / "checkout-service"))
CONFIG_RELPATH = "checkout_service/config.py"

# Remediation policy rules (app-level checks; see warrant/policy/README.md).
POLICY_FILE = Path(os.environ.get("WARRANT_POLICY_FILE", WARRANT_DIR / "policy" / "remediation_policy.json"))

# Verification.
TEST_PYTHON = os.environ.get("WARRANT_TEST_PYTHON", sys.executable)
TEST_TIMEOUT_SECONDS = float(os.environ.get("WARRANT_TEST_TIMEOUT", "120"))
VERIFY_TIMEOUT_SECONDS = float(os.environ.get("WARRANT_VERIFY_TIMEOUT", "45"))
VERIFY_INTERVAL_SECONDS = float(os.environ.get("WARRANT_VERIFY_INTERVAL", "3"))
RECOVERED_FAILURE_RATE = float(os.environ.get("WARRANT_RECOVERED_FAILURE_RATE", "0.05"))
RECOVERED_P95_MS = float(os.environ.get("WARRANT_RECOVERED_P95_MS", "1000"))

EVENTS_FILE = STATE_DIR / "events.jsonl"
DIAGNOSIS_FILE = STATE_DIR / "diagnosis.json"
OUTCOME_FILE = STATE_DIR / "outcome.json"
REPORT_FILE = STATE_DIR / "report.json"

PORT = int(os.environ.get("AGENT_PORT", "8082"))
