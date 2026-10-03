"""Watcher settings. Every value can be overridden with an environment variable."""

import os

CHECKOUT_HEALTH_URL = os.environ.get("CHECKOUT_HEALTH_URL", "http://localhost:8081/health")
AGENT_INVESTIGATE_URL = os.environ.get("AGENT_INVESTIGATE_URL", "http://localhost:8082/investigate")

POLL_INTERVAL_SECONDS = float(os.environ.get("WATCHER_POLL_INTERVAL", "2"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("WATCHER_HTTP_TIMEOUT", "3"))

# Incident: failure_rate above FAILURE_THRESHOLD for CONSECUTIVE_BAD_CHECKS polls in a row.
FAILURE_THRESHOLD = float(os.environ.get("WATCHER_FAILURE_THRESHOLD", "0.15"))
CONSECUTIVE_BAD_CHECKS = int(os.environ.get("WATCHER_BAD_CHECKS", "2"))

# Re-arm: failure_rate below RECOVERY_THRESHOLD for CONSECUTIVE_GOOD_CHECKS polls in a row.
RECOVERY_THRESHOLD = float(os.environ.get("WATCHER_RECOVERY_THRESHOLD", "0.05"))
CONSECUTIVE_GOOD_CHECKS = int(os.environ.get("WATCHER_GOOD_CHECKS", "3"))

SERVICE_NAME = "checkout"
