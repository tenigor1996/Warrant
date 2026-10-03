"""
agent/report.py — Final incident report (contracts/report.json) -> state/report.json.

    generate_incident_report() -> report.json shape

Built at incident close, after the last verification, from what was actually
recorded while the incident ran:

    state/outcome.json     monitor event, diagnosis, policy verdicts, action, tests, verification
    state/events.jsonl     timeline timestamps (fallback for detected_at / resolved_at)
    :8081/metrics/history  the service's own metric snapshots (healthy baseline, pre-fix reading)

Nothing is filled in to make the shape look complete. A field whose source is
missing is null (or an empty list), and its name is listed on the timeline and
in outcome.json under "report_missing".

The hash is not reimplemented here: warrant/report/report_renderer.py owns the
canonicalization, and this module calls its compute_sha256().
"""

import json
import os
from pathlib import Path

from warrant.agent import config
from warrant.agent.events import EventStream
from warrant.agent.outcome import RECOVERED, Outcome, final_status
from warrant.agent.tools import _http_get
from warrant.report.report_renderer import HASH_FIELD, compute_sha256


def generate_incident_report(outcome: Outcome = None, events: EventStream = None, write: bool = True) -> dict:
    """Build, seal and (by default) write the report for the incident in outcome.json."""
    outcome = outcome or Outcome(config.OUTCOME_FILE)
    events = events or EventStream(config.EVENTS_FILE)
    report, missing = build_report(outcome.load(), _read_timeline(events.path), _metrics_history())
    report[HASH_FIELD] = compute_sha256(report)
    if write:
        write_report(report, config.REPORT_FILE)
        outcome.update(report_missing=missing)
        detail = f"{report['incident_id']} {report['final_status']}"
        events.emit("report", "status", "Incident report generated",
                    detail + (f"; unavailable: {', '.join(missing)}" if missing else ""))
    return report


def build_report(data: dict, timeline: list, history: list) -> tuple:
    """Pure: (report without its hash, names of fields whose source was unavailable)."""
    monitor = data.get("event") or {}
    diagnosis = data.get("diagnosis") or {}
    verification = data.get("verification") or {}
    health_after = verification.get("health") or {}

    status = final_status(verification)

    detected_at = monitor.get("detected_at") or _first_timestamp(timeline, phase="detect")
    resolved_at = None
    if status == RECOVERED:
        resolved_at = health_after.get("timestamp") or _first_timestamp(timeline, title="Service recovered")

    before = _reading_before_fix(history, detected_at, data.get("action_executed_at")) or monitor
    baseline = _healthy_baseline(history, detected_at)
    tests = data.get("tests") or {}

    report = {
        "incident_id": data.get("incident_id"),
        "detected_at": detected_at,
        "resolved_at": resolved_at,
        "symptoms": _symptoms(baseline, before),
        "evidence": _evidence(diagnosis.get("evidence")),
        "root_cause": diagnosis.get("root_cause"),
        "offending_commit": diagnosis.get("offending_commit"),
        "actions_proposed": [{"action": a.get("action"), "verdict": a.get("verdict"), "reason": a.get("reason")}
                             for a in data.get("actions_proposed") or []],
        "action_executed": data.get("action_executed"),
        "tests": _pick(tests, "passed", "failed"),
        "metrics_before": _pick(before, "failure_rate", "latency_p95_ms"),
        "metrics_after": _pick(health_after, "failure_rate", "latency_p95_ms"),
        "final_status": status,
    }
    # resolved_at is legitimately null until the incident is verified recovered.
    expected = [k for k in report if k != "resolved_at" or status == RECOVERED]
    missing = [k for k in expected if report[k] is None or report[k] == []]
    return report, missing


def write_report(report: dict, path: Path) -> None:
    """Write to a temp file and rename, so a reader sees the old report or the new one, never half."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---- sources -----------------------------------------------------------------

def _metrics_history() -> list:
    try:
        return _http_get("/metrics/history")["snapshots"]
    except Exception:
        return []  # service unreachable: the report falls back to the monitor's own reading


def _read_timeline(path: Path) -> list:
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def _first_timestamp(timeline, **match):
    for entry in timeline:
        if all(entry.get(k) == v for k, v in match.items()):
            return entry.get("timestamp")
    return None


def _reading_before_fix(history, detected_at, fixed_at):
    """The service's last snapshot before the remediation was applied (None if there was no fix)."""
    if not fixed_at:
        return None
    during = [s for s in history
              if s.get("timestamp", "") <= fixed_at and (not detected_at or s.get("timestamp", "") >= detected_at)]
    return during[-1] if during else None


def _healthy_baseline(history, detected_at):
    """The service's last 'healthy' snapshot before the incident was detected."""
    if not detected_at:
        return None
    healthy = [s for s in history if s.get("status") == "healthy" and s.get("timestamp", "") < detected_at]
    return healthy[-1] if healthy else None


def _symptoms(baseline, incident) -> list:
    rows = (("failure rate", "failure_rate", lambda v: f"{v:.0%}"),
            ("p95 latency", "latency_p95_ms", lambda v: f"{round(v)}ms"),
            ("payment gateway calls", "payment_requests_per_minute", lambda v: f"{round(v)}/min"))
    symptoms = []
    for label, key, fmt in rows:
        now = incident.get(key)
        if not _is_number(now):
            continue
        was = (baseline or {}).get(key)
        if _is_number(was):
            symptoms.append(f"{label} {fmt(was)} -> {fmt(now)}")
        else:
            symptoms.append(f"{label} {fmt(now)} during the incident (no healthy baseline recorded)")
    return symptoms


def _evidence(items) -> list:
    """The findings the agent submitted with its diagnosis, unedited."""
    evidence = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("finding"):
            evidence.append({"source": str(item.get("source") or "unspecified"), "finding": str(item["finding"])})
        elif isinstance(item, str) and item.strip():
            evidence.append({"source": "unspecified", "finding": item.strip()})
    return evidence


def _pick(source: dict, *keys):
    """{key: number} for all keys, or None unless every one of them was recorded."""
    if not all(_is_number(source.get(k)) for k in keys):
        return None
    return {k: source[k] for k in keys}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
