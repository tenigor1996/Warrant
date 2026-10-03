#!/usr/bin/env python3
"""Contract gate for the files the WARRANT dashboard consumes.

Validates whichever of ``health.json``, ``events.jsonl`` and ``report.json``
are present in a state directory against the frozen contracts in
``warrant/contracts/``, from the point of view of the dashboard that reads
them. This is a checking tool only: it never writes to the state directory.

Severity levels
---------------

``FAIL``
    The UI cannot render this correctly: wrong type, unparseable content, or
    an enum value the UI has no mapping for (``final_status``,
    ``actions_proposed[].verdict``, a hash that does not verify).
``WARN``
    The UI renders, but something has drifted and the result is degraded or
    misleading: non-canonical casing, an unknown ``phase``/``kind``/event
    ``policy_verdict`` (these fall back to neutral styling), seq anomalies,
    or no DENIED event at all.
``INFO``
    Honest missing data. Nulls and empty lists are an accepted part of the
    contract -- the UI renders an em dash or a placeholder for them.

Exit status is 1 if anything FAILed, 0 otherwise. ``--strict`` promotes every
WARN to a FAIL.

Usage:
    python validate_state.py              # ../state/, falling back to ../fixtures/
    python validate_state.py <dir>
    python validate_state.py <dir> --strict

Stdlib only -- no dependencies.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The hash algorithm lives in report_renderer and is not reimplemented here;
# if it ever changes, this validator follows it automatically.
try:
    from report_renderer import HASH_FIELD, compute_sha256

    HASHER_ERROR = None
except Exception as err:  # pragma: no cover - only on a broken checkout
    HASH_FIELD = "report_sha256"
    compute_sha256 = None
    HASHER_ERROR = err

STATE_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "state"))
FIXTURES_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "fixtures"))

HEALTH_STATUSES = ("healthy", "degraded", "recovering")
PHASES = (
    "detect",
    "investigate",
    "diagnose",
    "plan",
    "policy",
    "act",
    "verify",
    "report",
)
KINDS = ("tool_call", "tool_result", "reasoning", "policy_decision", "status")
VERDICTS = ("ALLOWED", "DENIED")
FINAL_STATUSES = ("RECOVERED", "NOT_RECOVERED", "UNVERIFIED")

ISO8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|z|[+-]\d{2}:?\d{2})?$"
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")

FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"


# --------------------------------------------------------------------------- #
# finding collection
# --------------------------------------------------------------------------- #


class FileReport(object):
    """Findings for a single state file."""

    def __init__(self, filename, path):
        self.filename = filename
        self.path = path
        self.present = os.path.isfile(path)
        self.findings = []
        self.summary = []

    def add(self, level, where, message):
        self.findings.append((level, where, message))

    def fail(self, where, message):
        self.add(FAIL, where, message)

    def warn(self, where, message):
        self.add(WARN, where, message)

    def info(self, where, message):
        self.add(INFO, where, message)

    def note(self, line):
        self.summary.append(line)

    def count(self, level):
        return sum(1 for item in self.findings if item[0] == level)

    def verdict(self, strict=False):
        if not self.present:
            return "SKIP"
        if self.count(FAIL) or (strict and self.count(WARN)):
            return FAIL
        if self.count(WARN):
            return WARN
        return "PASS"


# --------------------------------------------------------------------------- #
# small type helpers
# --------------------------------------------------------------------------- #


def is_number(value):
    """True for JSON numbers. Booleans are not numbers, however much Python disagrees."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def typename(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def norm_enum(value):
    """Mirror the UI's normEnum: strings are trimmed, everything else is empty."""
    return value.strip() if isinstance(value, str) else ""


def short(value, limit=60):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "..."


def is_empty(value):
    return value is None or value == [] or value == {} or value == ""


def check_timestamp(rep, where, value, required=False):
    """timestamp fields: a string or null; warn on a shape the UI cannot parse."""
    if value is None:
        if required:
            rep.warn(where, "timestamp is null")
        return
    if not isinstance(value, str):
        rep.fail(where, "timestamp must be a string or null, got {}".format(typename(value)))
        return
    if not ISO8601.match(value.strip()):
        rep.warn(where, "timestamp {!r} is not ISO8601".format(short(value)))


def check_number(rep, where, value, label, low=None, high=None, level=WARN):
    """number-or-null field; range violations report at ``level``."""
    if value is None:
        return False
    if not is_number(value):
        rep.fail(where, "{} must be a number or null, got {} ({})".format(
            label, typename(value), short(value)))
        return False
    if low is not None and value < low:
        rep.add(level, where, "{} is {}, below the expected minimum {}".format(label, value, low))
    if high is not None and value > high:
        rep.add(level, where, "{} is {}, above the expected maximum {}".format(label, value, high))
    return True


def check_string(rep, where, value, label, level=FAIL):
    """string-or-null field. Returns True if a usable string is present."""
    if value is None:
        return False
    if not isinstance(value, str):
        rep.add(level, where, "{} must be a string or null, got {} ({})".format(
            label, typename(value), short(value)))
        return False
    return True


def report_extra_keys(rep, obj, known, where="object"):
    extra = [key for key in obj if key not in known]
    if extra:
        rep.info(where, "extra keys the UI ignores: {}".format(", ".join(sorted(extra))))


def load_json(rep, path):
    """Read and parse a JSON file, recording any failure. Returns (ok, value)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (IOError, OSError) as err:
        rep.fail("file", "cannot read: {}".format(err))
        return False, None
    if not text.strip():
        rep.fail("file", "file is empty")
        return False, None
    try:
        return True, json.loads(text)
    except ValueError as err:
        rep.fail("file", "not valid JSON: {}".format(err))
        return False, None


# --------------------------------------------------------------------------- #
# health.json
# --------------------------------------------------------------------------- #

HEALTH_KEYS = (
    "timestamp",
    "status",
    "failure_rate",
    "latency_p95_ms",
    "requests_last_minute",
    "errors_last_minute",
)


def validate_health(path):
    rep = FileReport("health.json", path)
    if not rep.present:
        return rep

    ok, data = load_json(rep, path)
    if not ok:
        return rep
    if not isinstance(data, dict):
        rep.fail("file", "expected a JSON object, got {}".format(typename(data)))
        return rep

    for key in HEALTH_KEYS:
        if key not in data:
            rep.warn(key, "field missing from the contract shape")

    check_timestamp(rep, "timestamp", data.get("timestamp"))

    status = data.get("status")
    if "status" in data:
        if not isinstance(status, str):
            rep.fail("status", "must be a string, got {} ({})".format(
                typename(status), short(status)))
        else:
            normalized = norm_enum(status).lower()
            if normalized not in HEALTH_STATUSES:
                rep.fail("status", "{!r} is not one of {}; the pill renders UNKNOWN".format(
                    short(status), "/".join(HEALTH_STATUSES)))
            elif status != normalized:
                rep.warn("status", "casing/whitespace drift: {!r} -> {!r} (UI normalizes, "
                                   "but the contract value is lowercase)".format(status, normalized))

    check_number(rep, "failure_rate", data.get("failure_rate"), "failure_rate",
                 low=0, high=1, level=FAIL)
    check_number(rep, "latency_p95_ms", data.get("latency_p95_ms"), "latency_p95_ms", low=0)
    check_number(rep, "requests_last_minute", data.get("requests_last_minute"),
                 "requests_last_minute", low=0)
    check_number(rep, "errors_last_minute", data.get("errors_last_minute"),
                 "errors_last_minute", low=0)

    nulls = [key for key in HEALTH_KEYS if key in data and data[key] is None]
    if nulls:
        rep.info("fields", "null (UI renders --): {}".format(", ".join(nulls)))

    report_extra_keys(rep, data, HEALTH_KEYS)
    rep.note("status={} failure_rate={} p95={}".format(
        data.get("status"), data.get("failure_rate"), data.get("latency_p95_ms")))
    return rep


# --------------------------------------------------------------------------- #
# events.jsonl
# --------------------------------------------------------------------------- #

EVENT_KEYS = (
    "seq",
    "timestamp",
    "phase",
    "kind",
    "title",
    "detail",
    "policy_verdict",
    "policy_reason",
    "duration_ms",
)


def validate_event(rep, event, where):
    """Validate one event object. Returns (seq_or_None, verdict_or_None)."""
    seq = event.get("seq")
    if "seq" not in event:
        rep.fail(where, "seq is missing; the row never renders")
        seq = None
    elif not is_number(seq):
        rep.fail(where, "seq must be a number, got {} ({}); the row never renders".format(
            typename(seq), short(seq)))
        seq = None
    elif seq == 0:
        rep.warn(where, "seq is 0; the timeline filter drops it (0 > 0 is false)")
    elif seq < 0:
        rep.warn(where, "seq is negative ({})".format(seq))

    check_timestamp(rep, where, event.get("timestamp"))

    phase = event.get("phase")
    if phase is not None and not isinstance(phase, str):
        rep.fail(where, "phase must be a string or null, got {}".format(typename(phase)))
    else:
        normalized = norm_enum(phase).lower()
        if not normalized:
            rep.warn(where, "phase is missing or empty")
        elif normalized not in PHASES:
            rep.warn(where, "unknown phase {!r}; falls back to the report accent".format(
                short(phase)))
        elif phase != normalized:
            rep.warn(where, "phase casing drift: {!r} -> {!r}".format(phase, normalized))

    kind = event.get("kind")
    if kind is not None and not isinstance(kind, str):
        rep.fail(where, "kind must be a string or null, got {}".format(typename(kind)))
    else:
        normalized = norm_enum(kind).lower()
        if not normalized:
            rep.warn(where, "kind is missing or empty")
        elif normalized not in KINDS:
            rep.warn(where, "unknown kind {!r}; the row loses its kind styling".format(
                short(kind)))
        elif kind != normalized:
            rep.warn(where, "kind casing drift: {!r} -> {!r}".format(kind, normalized))

    if not check_string(rep, where, event.get("title"), "title"):
        if event.get("title") is None:
            rep.warn(where, "title is missing or null; the row renders with no heading")
    check_string(rep, where, event.get("detail"), "detail")
    check_string(rep, where, event.get("policy_reason"), "policy_reason")

    verdict = None
    raw_verdict = event.get("policy_verdict")
    if raw_verdict is not None:
        if not isinstance(raw_verdict, str):
            rep.fail(where, "policy_verdict must be a string or null, got {}".format(
                typename(raw_verdict)))
        else:
            verdict = norm_enum(raw_verdict).upper()
            if verdict not in VERDICTS:
                rep.warn(where, "policy_verdict {!r} is not ALLOWED/DENIED; renders as a "
                                "neutral chip and will not fire the shake".format(
                                    short(raw_verdict)))
                verdict = None
            elif raw_verdict != verdict:
                rep.warn(where, "policy_verdict casing drift: {!r} -> {!r}".format(
                    raw_verdict, verdict))

    check_number(rep, where, event.get("duration_ms"), "duration_ms", low=0)
    report_extra_keys(rep, event, EVENT_KEYS, where)
    return seq, verdict


def validate_events(path):
    rep = FileReport("events.jsonl", path)
    if not rep.present:
        return rep

    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (IOError, OSError) as err:
        rep.fail("file", "cannot read: {}".format(err))
        return rep

    seqs = []
    verdicts = []
    parsed = 0
    blank = 0

    for number, raw in enumerate(lines, start=1):
        where = "line {}".format(number)
        if not raw.strip():
            blank += 1
            continue
        try:
            event = json.loads(raw)
        except ValueError as err:
            rep.fail(where, "unparseable JSON: {} | {}".format(err, short(raw)))
            continue
        if not isinstance(event, dict):
            rep.fail(where, "expected a JSON object, got {} ({}); a non-object line blanks "
                            "every panel on each poll".format(typename(event), short(raw)))
            continue
        parsed += 1
        seq, verdict = validate_event(rep, event, where)
        if seq is not None:
            seqs.append(seq)
        if verdict:
            verdicts.append(verdict)

    if blank:
        rep.info("file", "{} blank line(s) skipped".format(blank))
    if parsed == 0:
        rep.fail("file", "no valid event objects found")
        return rep

    rep.note("{} event(s) parsed from {} line(s)".format(parsed, len(lines)))

    # seq range, gaps, duplicates, ordering
    numeric = [int(value) for value in seqs if float(value).is_integer()]
    if len(numeric) != len(seqs):
        rep.warn("seq", "non-integer seq values present; gap analysis uses integers only")
    if numeric:
        low, high = min(numeric), max(numeric)
        rep.note("seq range {}..{}".format(low, high))

        seen = {}
        duplicates = []
        for value in numeric:
            seen[value] = seen.get(value, 0) + 1
        duplicates = sorted(value for value, hits in seen.items() if hits > 1)
        if duplicates:
            rep.warn("seq", "duplicate seq {}; those rows render twice".format(
                ", ".join(str(value) for value in duplicates)))
        else:
            rep.note("no duplicate seq")

        gaps = [value for value in range(low, high + 1) if value not in seen]
        if gaps:
            preview = ", ".join(str(value) for value in gaps[:12])
            if len(gaps) > 12:
                preview += ", ... (+{} more)".format(len(gaps) - 12)
            rep.warn("seq", "{} gap(s) in the seq range: {}".format(len(gaps), preview))
        else:
            rep.note("no gaps in {}..{}".format(low, high))

        out_of_order = [
            (index + 1, numeric[index - 1], numeric[index])
            for index in range(1, len(numeric))
            if numeric[index] < numeric[index - 1]
        ]
        if out_of_order:
            for position, previous, current in out_of_order[:8]:
                rep.warn("seq", "out of order at event {}: {} follows {}; the lower seq is "
                                "dropped permanently".format(position, current, previous))
            if len(out_of_order) > 8:
                rep.warn("seq", "... and {} further out-of-order event(s)".format(
                    len(out_of_order) - 8))
        else:
            rep.note("seq in ascending order")

    denied = verdicts.count("DENIED")
    allowed = verdicts.count("ALLOWED")
    if denied:
        rep.note("policy verdicts: {} DENIED, {} ALLOWED".format(denied, allowed))
    else:
        rep.warn("CRITICAL", "*** no event carries policy_verdict DENIED -- the demo's key "
                             "frame (denial chip + shake) will never fire ***")
    return rep


# --------------------------------------------------------------------------- #
# report.json
# --------------------------------------------------------------------------- #

REPORT_KEYS = (
    "incident_id",
    "detected_at",
    "resolved_at",
    "symptoms",
    "evidence",
    "root_cause",
    "offending_commit",
    "actions_proposed",
    "action_executed",
    "tests",
    "metrics_before",
    "metrics_after",
    "final_status",
    HASH_FIELD,
)

REPORT_STRINGS = (
    "incident_id",
    "root_cause",
    "offending_commit",
    "action_executed",
)


def check_metrics(rep, data, field):
    value = data.get(field)
    if value is None:
        return
    if not isinstance(value, dict):
        rep.fail(field, "must be an object or null, got {} ({})".format(
            typename(value), short(value)))
        return
    check_number(rep, field, value.get("failure_rate"), "failure_rate", low=0, high=1)
    check_number(rep, field, value.get("latency_p95_ms"), "latency_p95_ms", low=0)
    report_extra_keys(rep, value, ("failure_rate", "latency_p95_ms"), field)
    missing = [key for key in ("failure_rate", "latency_p95_ms") if value.get(key) is None]
    if missing:
        rep.info(field, "null (UI renders --): {}".format(", ".join(missing)))


def validate_report(path):
    rep = FileReport("report.json", path)
    if not rep.present:
        return rep

    ok, data = load_json(rep, path)
    if not ok:
        return rep
    if not isinstance(data, dict):
        rep.fail("file", "expected a JSON object, got {}".format(typename(data)))
        return rep

    for field in REPORT_STRINGS:
        check_string(rep, field, data.get(field), field)
    check_timestamp(rep, "detected_at", data.get("detected_at"))
    check_timestamp(rep, "resolved_at", data.get("resolved_at"))

    # symptoms: list of strings
    symptoms = data.get("symptoms")
    if symptoms is not None:
        if not isinstance(symptoms, list):
            rep.fail("symptoms", "must be a list or null, got {} ({})".format(
                typename(symptoms), short(symptoms)))
        else:
            for index, item in enumerate(symptoms):
                if not isinstance(item, str):
                    rep.fail("symptoms[{}]".format(index),
                             "must be a string, got {} ({})".format(typename(item), short(item)))

    # evidence: list of {source, finding}
    evidence = data.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, list):
            rep.fail("evidence", "must be a list or null, got {} ({})".format(
                typename(evidence), short(evidence)))
        else:
            for index, item in enumerate(evidence):
                where = "evidence[{}]".format(index)
                if not isinstance(item, dict):
                    rep.fail(where, "must be an object {{source, finding}}, got {} ({})".format(
                        typename(item), short(item)))
                    continue
                for key in ("source", "finding"):
                    if key not in item or item[key] is None:
                        rep.warn(where, "{} is missing or null".format(key))
                    else:
                        check_string(rep, where, item[key], key)
                report_extra_keys(rep, item, ("source", "finding"), where)

    # actions_proposed: list of {action, verdict, reason}
    actions = data.get("actions_proposed")
    if actions is not None:
        if not isinstance(actions, list):
            rep.fail("actions_proposed", "must be a list or null, got {} ({})".format(
                typename(actions), short(actions)))
        else:
            denials = 0
            for index, item in enumerate(actions):
                where = "actions_proposed[{}]".format(index)
                if not isinstance(item, dict):
                    rep.fail(where, "must be an object {{action, verdict, reason}}, got {} "
                                    "({})".format(typename(item), short(item)))
                    continue
                if not item.get("action"):
                    rep.warn(where, "action is missing or empty")
                else:
                    check_string(rep, where, item.get("action"), "action")
                check_string(rep, where, item.get("reason"), "reason")

                raw = item.get("verdict")
                if raw is None:
                    rep.warn(where, "verdict is missing or null; renders as -- with the "
                                    "ALLOWED styling")
                elif not isinstance(raw, str):
                    rep.fail(where, "verdict must be a string, got {} ({})".format(
                        typename(raw), short(raw)))
                else:
                    verdict = norm_enum(raw).upper()
                    if verdict not in VERDICTS:
                        rep.fail(where, "verdict {!r} is not ALLOWED/DENIED; the UI cannot "
                                        "map it".format(short(raw)))
                    else:
                        if verdict == "DENIED":
                            denials += 1
                        if raw != verdict:
                            rep.warn(where, "verdict casing drift: {!r} -> {!r}".format(
                                raw, verdict))
            if actions and not denials:
                rep.info("actions_proposed", "no DENIED action in the report body")

    # tests: {passed, failed}
    tests = data.get("tests")
    if tests is not None:
        if not isinstance(tests, dict):
            rep.fail("tests", "must be an object {{passed, failed}} or null, got {} ({})".format(
                typename(tests), short(tests)))
        else:
            for key in ("passed", "failed"):
                if key not in tests or tests[key] is None:
                    rep.info("tests", "{} is missing or null (renders as 0)".format(key))
                else:
                    check_number(rep, "tests", tests[key], key, low=0)
            report_extra_keys(rep, tests, ("passed", "failed"), "tests")

    check_metrics(rep, data, "metrics_before")
    check_metrics(rep, data, "metrics_after")

    # final_status drives the recovery panel
    raw_status = data.get("final_status")
    if raw_status is None:
        rep.warn("final_status", "missing or null; the recovery panel stays on its "
                                 "'Awaiting recovery' placeholder")
    elif not isinstance(raw_status, str):
        rep.fail("final_status", "must be a string, got {} ({})".format(
            typename(raw_status), short(raw_status)))
    else:
        status = norm_enum(raw_status).upper()
        if status not in FINAL_STATUSES:
            rep.fail("final_status", "{!r} is not one of {}; the UI cannot map it and the "
                                     "recovery panel falls back to a placeholder".format(
                                         short(raw_status), "/".join(FINAL_STATUSES)))
        elif raw_status != status:
            rep.warn("final_status", "casing/whitespace drift: {!r} -> {!r} (UI normalizes, "
                                     "but the contract value is uppercase)".format(
                                         raw_status, status))
        else:
            rep.note("final_status {}".format(status))

    # hash
    verify_hash(rep, data)

    empties = [key for key in REPORT_KEYS if key not in data or is_empty(data.get(key))]
    if empties:
        rep.info("fields", "null/empty/absent (UI renders -- or a placeholder): {}".format(
            ", ".join(empties)))
    report_extra_keys(rep, data, REPORT_KEYS)
    return rep


def verify_hash(rep, data):
    raw = data.get(HASH_FIELD)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        rep.info(HASH_FIELD, "absent or empty; report is unsealed, hash not verified")
        return
    if not isinstance(raw, str):
        rep.fail(HASH_FIELD, "must be a string or null, got {}".format(typename(raw)))
        return
    if compute_sha256 is None:
        rep.warn(HASH_FIELD, "cannot import report_renderer.compute_sha256 ({}); hash not "
                             "verified".format(HASHER_ERROR))
        return

    stored = raw.strip()
    computed = compute_sha256(data)
    if stored == computed:
        rep.note("{} MATCH {}".format(HASH_FIELD, computed))
        return
    if stored.lower() == computed:
        rep.warn(HASH_FIELD, "MATCH but not lowercase hex as the frozen scheme requires: "
                             "{}".format(short(stored, 72)))
        return
    if not HEX64.match(stored.lower()):
        rep.warn(HASH_FIELD, "not a 64-character hex digest: {}".format(short(stored, 72)))
    rep.fail(HASH_FIELD, "MISMATCH\n      stored   {}\n      computed {}".format(
        short(stored, 72), computed))


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

RULE = "-" * 72
ORDER = {FAIL: 0, WARN: 1, INFO: 2}


def resolve_dir(requested):
    """Return (directory, note). Defaults to ../state/, falling back to ../fixtures/."""
    if requested:
        return os.path.abspath(requested), None
    if os.path.isdir(STATE_DIR):
        return STATE_DIR, None
    return FIXTURES_DIR, "warrant/state/ is absent; fell back to warrant/fixtures/"


def print_file_report(rep, strict, out):
    verdict = rep.verdict(strict)
    out.write("{}\n{}  [{}]\n".format(RULE, rep.filename, verdict))
    if not rep.present:
        out.write("  not present in this directory; nothing to check\n")
        return
    for line in rep.summary:
        out.write("  . {}\n".format(line))
    for level, where, message in sorted(rep.findings, key=lambda item: ORDER[item[0]]):
        shown = FAIL if (strict and level == WARN) else level
        out.write("  [{}] {}: {}\n".format(shown, where, message))
    if not rep.findings:
        out.write("  no findings\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate WARRANT state files against the frozen dashboard contract."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="directory holding health.json / events.jsonl / report.json "
             "(default: ../state/, falling back to ../fixtures/)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat every WARN as a FAIL",
    )
    args = parser.parse_args(argv)

    directory, note = resolve_dir(args.directory)
    out = sys.stdout

    out.write("WARRANT state contract validator\n")
    out.write("state dir: {}\n".format(directory))
    if note:
        out.write("note:      {}\n".format(note))
    if args.strict:
        out.write("mode:      strict (WARN counts as FAIL)\n")

    if not os.path.isdir(directory):
        out.write("\nFAIL: {} is not a directory\n".format(directory))
        return 1

    reports = [
        validate_health(os.path.join(directory, "health.json")),
        validate_events(os.path.join(directory, "events.jsonl")),
        validate_report(os.path.join(directory, "report.json")),
    ]

    for rep in reports:
        print_file_report(rep, args.strict, out)

    fails = sum(rep.count(FAIL) for rep in reports)
    warns = sum(rep.count(WARN) for rep in reports)
    infos = sum(rep.count(INFO) for rep in reports)
    present = [rep for rep in reports if rep.present]

    out.write(RULE + "\n")
    if not present:
        out.write("overall: FAIL -- none of the three contract files are present\n")
        return 1

    failed = fails > 0 or (args.strict and warns > 0)
    warn_label = "{} WARN{}".format(warns, " (counted as FAIL)" if args.strict and warns else "")
    out.write("overall: {}  ({} file(s) checked, {} FAIL, {}, {} INFO)\n".format(
        FAIL if failed else ("WARN" if warns else "PASS"),
        len(present), fails, warn_label, infos))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
