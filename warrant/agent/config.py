"""Agent settings. Every value can be overridden with an environment variable."""

import os
from pathlib import Path

WARRANT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("WARRANT_STATE_DIR", WARRANT_DIR / "state"))

# Local model served by vLLM (OpenAI-compatible API) on the GB10.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1")
# Empty = use the first model the server reports at /v1/models.
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT", "180"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "2048"))

MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "12"))

# Evidence sources (read-only).
CHECKOUT_URL = os.environ.get("CHECKOUT_URL", "http://localhost:8081")
SERVICE_REPO = Path(os.environ.get("SERVICE_REPO", STATE_DIR / "checkout-service"))
CONFIG_RELPATH = "checkout_service/config.py"

EVENTS_FILE = STATE_DIR / "events.jsonl"
DIAGNOSIS_FILE = STATE_DIR / "diagnosis.json"

PORT = int(os.environ.get("AGENT_PORT", "8082"))
