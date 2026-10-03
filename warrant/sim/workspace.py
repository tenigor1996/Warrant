"""
sim/workspace.py — The checkout-service git repository the agent investigates.

The simulated service's code, config, tests and git history live in their own
repository under warrant/state/checkout-service/ (gitignored by the team repo),
so the incident commit never touches the team's own history.

    build(ws)             fresh repo with a plausible baseline history (healthy)
    trigger_incident(ws)  commits the bad config change, plus an unrelated
                          follow-up commit so the culprit is not HEAD
    read_config(ws)       current config values as a dict

The running simulator (engine.py) hot-reloads config.py from this repo, so
whatever the file says is what the service does.
"""

import ast
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "service_template"
CONFIG_RELPATH = "checkout_service/config.py"
RUNBOOK_RELPATH = "docs/runbook.md"

HEALTHY_CONFIG = {"PAYMENT_TIMEOUT": 5.0, "PAYMENT_RETRIES": 1}
INCIDENT_CONFIG = {"PAYMENT_TIMEOUT": 0.01, "PAYMENT_RETRIES": 5}

PRIYA = ("Priya Natarajan", "priya.natarajan@shop.example")
MARCUS = ("Marcus Webb", "marcus.webb@shop.example")
DANA = ("Dana Kowalski", "dana.kowalski@shop.example")
JORDAN = ("Jordan Ellis", "jordan.ellis@shop.example")

DAY = 86400
# Files added after the initial commit, in the order the baseline history adds them.
LATER_FILES = ("README.md", RUNBOOK_RELPATH, "requirements.txt")


class IncidentAlreadyActive(Exception):
    pass


def build(ws: Path, now: float = None) -> str:
    """Recreate the repo from the template with a healthy baseline history. Returns HEAD sha."""
    now = time.time() if now is None else now
    ws = Path(ws)
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(TEMPLATE_DIR, ws, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

    later = {rel: (ws / rel).read_text() for rel in LATER_FILES}
    for rel in LATER_FILES:
        (ws / rel).unlink()
    write_config(ws, LOG_LEVEL="DEBUG")

    _git(ws, "init", "-q", "-b", "main")
    _commit(ws, PRIYA, now - 14 * DAY, "Initial checkout service with payment gateway client")

    (ws / "README.md").write_text(later["README.md"])
    _commit(ws, MARCUS, now - 10 * DAY, "Add README with local run instructions")

    write_config(ws, LOG_LEVEL="INFO")
    _commit(ws, PRIYA, now - 6 * DAY, "Reduce log verbosity in production config")

    (ws / RUNBOOK_RELPATH).write_text(later[RUNBOOK_RELPATH])
    _commit(ws, DANA, now - 3 * DAY, "Add checkout incident runbook")

    (ws / "requirements.txt").write_text(later["requirements.txt"])
    _commit(ws, MARCUS, now - 1 * DAY, "Pin test dependencies")

    return head(ws)


def trigger_incident(ws: Path, now: float = None) -> dict:
    """Commit the bad config change and a follow-up docs commit. Returns commit info."""
    now = time.time() if now is None else now
    ws = Path(ws)
    if is_incident(ws):
        raise IncidentAlreadyActive("incident config is already in place")

    previous = head(ws)
    write_config(ws, **INCIDENT_CONFIG)
    _commit(
        ws, JORDAN, now - 180,
        "Tune payment client for peak sale traffic\n\n"
        "Fail fast on slow gateway responses and retry instead of holding\n"
        "the request open.",
    )
    bad = head(ws)

    runbook = ws / RUNBOOK_RELPATH
    runbook.write_text(runbook.read_text().replace(
        "- Primary: #checkout-oncall",
        "- Primary: #checkout-oncall (pager: checkout-primary)",
    ))
    _commit(ws, DANA, now - 60, "Add pager alias to runbook escalation")

    return {"previous_head": previous, "incident_commit": bad, "head": head(ws)}


def read_config(ws: Path) -> dict:
    """Parse NAME = literal assignments from config.py without executing it."""
    tree = ast.parse((Path(ws) / CONFIG_RELPATH).read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            values[node.targets[0].id] = ast.literal_eval(node.value)
    return values


def write_config(ws: Path, **values) -> None:
    """Rewrite `NAME = value` lines in config.py, leaving everything else intact."""
    path = Path(ws) / CONFIG_RELPATH
    text = path.read_text()
    for name, value in values.items():
        literal = f'"{value}"' if isinstance(value, str) else repr(value)
        text, count = re.subn(rf"^{name} = .*$", f"{name} = {literal}", text, flags=re.M)
        if count != 1:
            raise KeyError(f"{name} not found in {CONFIG_RELPATH}")
    path.write_text(text)


def is_incident(ws: Path) -> bool:
    config = read_config(ws)
    return any(config[k] != v for k, v in HEALTHY_CONFIG.items())


def head(ws: Path) -> str:
    return _git(ws, "rev-parse", "HEAD").strip()


def _commit(ws: Path, author, when: float, message: str) -> None:
    name, email = author
    date = f"@{int(when)} +0000"
    env = {
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": date,
    }
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", message, env=env)


def _git(ws: Path, *args, env=None) -> str:
    full_env = {**os.environ, **(env or {})}
    cmd = ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args]
    return subprocess.run(cmd, cwd=ws, env=full_env, check=True, capture_output=True, text=True).stdout
