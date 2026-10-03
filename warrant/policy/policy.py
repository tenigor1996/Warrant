"""
policy/policy.py — Deterministic app-level remediation policy.

    evaluate(action, params) -> Decision(verdict, reason, message, enforced_by)

Pure function of (action, params, rules): no model, no I/O beyond loading the
rules file, same input always gives the same verdict. Anything not explicitly
permitted is denied.

This is the *application-level* check. It runs inside the Warrant process and
only protects against actions taken through Warrant's own tools. OS-level
enforcement (filesystem, network, process) is OpenShell's job and is configured
in policy/openshell/; a write the sandbox refuses is reported through
os_denial() so the model sees the same kind of instructive message.
"""

import json
import posixpath
from dataclasses import dataclass
from pathlib import Path

ALLOWED = "ALLOWED"
DENIED = "DENIED"

APP_LAYER = "warrant-app-policy"
OS_LAYER = "os-sandbox"

DEFAULT_POLICY_FILE = Path(__file__).resolve().parent / "remediation_policy.json"


@dataclass(frozen=True)
class Decision:
    verdict: str        # ALLOWED | DENIED
    reason: str         # short, for the timeline and the report
    message: str        # what the model is told
    enforced_by: str = APP_LAYER

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOWED

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "reason": self.reason, "enforced_by": self.enforced_by}


def load(path=None) -> dict:
    return json.loads(Path(path or DEFAULT_POLICY_FILE).read_text())


def evaluate(action: str, params: dict = None, rules: dict = None) -> Decision:
    rules = rules if rules is not None else load()
    params = params or {}
    if action in rules.get("denied_actions", {}):
        return _deny(rules, rules["denied_actions"][action])
    if action == "apply_remediation":
        return _evaluate_write(params.get("path"), params.get("change"), rules)
    return _deny(rules, f"action {action!r}")


def os_denial(path: str, error: Exception, rules: dict = None) -> Decision:
    """The operating system (OpenShell/Landlock on the GB10) refused a write the app policy allowed."""
    rules = rules if rules is not None else load()
    reason = f"write to {path} was refused by the sandbox ({type(error).__name__})"
    return Decision(DENIED, reason,
                    f"DENIED: {reason}. Permitted write target: {rules['scope_description']}. "
                    "Choose a different approach.", OS_LAYER)


def _evaluate_write(path, change, rules) -> Decision:
    if not isinstance(path, str) or not path.strip():
        return _deny(rules, "write without a target path")
    normalized = posixpath.normpath(path.strip())
    if normalized not in rules["writable_paths"]:
        return _deny(rules, f"write to {path.strip()}")
    if not isinstance(change, dict) or not change:
        return _deny(rules, "a write with no named settings")

    settings = rules["settings"]
    for name, value in change.items():
        rule = settings.get(name)
        if rule is None:
            return _deny(rules, f"changing {name}", f"Permitted settings: {', '.join(sorted(settings))}.")
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not number or (rule["type"] == "integer" and not float(value).is_integer()):
            return _deny(rules, f"{name}={value!r}", f"{name} must be a {rule['type']}.")
        if not rule["min"] <= value <= rule["max"]:
            return _deny(rules, f"{name}={value!r}",
                         f"Permitted range for {name}: {rule['min']} to {rule['max']}.")
    return Decision(ALLOWED, f"write within {normalized}",
                    f"ALLOWED: write within {normalized}.")


def _deny(rules, what, hint="") -> Decision:
    reason = f"{what} is outside the permitted remediation scope"
    message = f"DENIED: {reason}. Permitted write target: {rules['scope_description']}. "
    if hint:
        message += hint + " "
    return Decision(DENIED, reason, message + "Choose a different approach.")
