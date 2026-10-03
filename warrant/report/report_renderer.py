#!/usr/bin/env python3
"""Authoritative incident-report hasher and HTML renderer.

This script owns the report hash. The canonicalization is fixed by team
agreement and must match the generator byte-for-byte:

    1. remove the ``report_sha256`` key from the report entirely
    2. json.dumps(report, sort_keys=True, separators=(",", ":"))
    3. encode UTF-8
    4. SHA-256
    5. store the lowercase hex digest back into ``report_sha256``

The dashboard only ever *displays* ``report.report_sha256``; it never
recomputes it.

Usage:
    python report_renderer.py                      # reads ../fixtures/report.json
    python report_renderer.py <input.json>
    python report_renderer.py <input.json> <output.html>

Stdlib only -- no dependencies.
"""

import argparse
import hashlib
import html
import json
import os
import sys

HASH_FIELD = "report_sha256"

DEFAULT_INPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, "fixtures", "report.json"
)


# --------------------------------------------------------------------------- #
# hashing
# --------------------------------------------------------------------------- #


def canonical_bytes(report):
    """Canonical serialization used for hashing.

    The hash field is removed, not zeroed: an unhashed report and a sealed one
    canonicalize identically, so a digest can be re-verified from the stored
    report. Any stored ``report_sha256`` is therefore excluded from its own
    digest.
    """
    payload = dict(report)
    payload.pop(HASH_FIELD, None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_sha256(report):
    """Return the lowercase hex digest for ``report`` (its stored hash is ignored)."""
    return hashlib.sha256(canonical_bytes(report)).hexdigest()


def seal(report):
    """Return (sealed_report, digest, previous_digest) with the hash written in."""
    previous = report.get(HASH_FIELD, "")
    digest = compute_sha256(report)
    sealed = dict(report)
    sealed[HASH_FIELD] = digest
    return sealed, digest, previous


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

CSS = """
:root {
  --bg: #0b0d10; --text: #e6e9ef; --panel: #14181f; --border: #232833;
  --muted: #8b94a7; --allowed: #4ade80; --denied: #ff3838;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 20px; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, sans-serif;
  font-size: 16px; line-height: 1.5;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.card {
  max-width: 960px; margin: 0 auto; background: var(--panel);
  border: 1px solid var(--border); border-radius: 12px; overflow: hidden;
}
header { padding: 24px 28px; border-bottom: 1px solid var(--border); }
h1 {
  margin: 0; font-size: 22px; font-weight: 800; letter-spacing: 0.14em;
  text-transform: uppercase;
}
.sub { margin-top: 6px; color: var(--muted); font-size: 14px; }
main { padding: 26px 28px; }
.facts {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 16px; margin-bottom: 28px;
}
.fact {
  background: #0f131a; border: 1px solid var(--border); border-radius: 8px;
  padding: 12px 14px;
}
.fact-label {
  font-size: 12px; font-weight: 700; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 6px;
}
.fact-value { font-size: 17px; font-weight: 700; }
section { margin-bottom: 28px; }
h2 {
  font-size: 13px; font-weight: 800; letter-spacing: 0.18em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 14px;
  padding-bottom: 8px; border-bottom: 1px solid var(--border);
}
ul { margin: 0; padding-left: 22px; font-size: 17px; }
.ev { display: grid; grid-template-columns: 110px 1fr; gap: 14px;
      padding: 10px 0; border-bottom: 1px solid #1b2028; }
.ev:last-child { border-bottom: none; }
.ev-source {
  font-size: 12px; font-weight: 800; letter-spacing: 0.14em;
  text-transform: uppercase; color: #35d6e8;
}
.rootcause { font-size: 20px; font-weight: 600; }
.chip {
  display: inline-block; margin-top: 14px; padding: 8px 14px;
  background: #221440; border: 1px solid #3b2a66; border-radius: 8px;
  font-weight: 700;
}
.act {
  display: grid; grid-template-columns: 120px 1fr; gap: 16px; padding: 14px 16px;
  margin-bottom: 12px; border-radius: 8px; background: #0f131a;
  border: 1px solid var(--border);
}
.verdict { font-weight: 800; letter-spacing: 0.14em; text-transform: uppercase; }
.allowed { border-left: 5px solid var(--allowed); }
.allowed .verdict { color: var(--allowed); }
.denied { border-left: 5px solid var(--denied); background: #1e1012; }
.denied .verdict { color: var(--denied); }
.reason { color: var(--muted); margin-top: 6px; }
.metrics { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }
.before { font-size: 30px; font-weight: 800; color: #ff7a7a; }
.after { font-size: 34px; font-weight: 800; color: var(--allowed); }
.arrow { font-size: 26px; color: var(--muted); }
.metric-row { width: 100%; padding: 14px 0; border-bottom: 1px solid #1b2028; }
.metric-row:last-child { border-bottom: none; }
.metric-label {
  font-size: 12px; font-weight: 700; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 8px;
}
.tests { display: flex; gap: 16px; }
.pass, .fail { padding: 10px 18px; border-radius: 8px; font-size: 20px;
               font-weight: 800; }
.pass { background: #0f3b1f; color: var(--allowed); }
.fail { background: #232833; color: var(--muted); }
.sha {
  padding: 18px 20px; background: #0d1016; border: 1px solid #2b3444;
  border-radius: 10px;
}
.sha-label {
  font-size: 12px; font-weight: 800; letter-spacing: 0.2em;
  text-transform: uppercase; color: var(--muted); margin-bottom: 10px;
}
.sha-value { font-size: 17px; font-weight: 700; color: #d7defa;
             overflow-wrap: anywhere; }
"""


def esc(value):
    return html.escape("" if value is None else str(value))


def fmt_percent(value):
    try:
        return "{:.1f}%".format(float(value) * 100)
    except (TypeError, ValueError):
        return "&mdash;"


def fmt_ms(value):
    try:
        return "{:d} ms".format(int(round(float(value))))
    except (TypeError, ValueError):
        return "&mdash;"


def _fact(label, value):
    return (
        '<div class="fact"><div class="fact-label">{}</div>'
        '<div class="fact-value">{}</div></div>'.format(esc(label), esc(value))
    )


def _metric_row(label, before, after, formatter):
    return (
        '<div class="metric-row"><div class="metric-label">{}</div>'
        '<div class="metrics"><span class="before">{}</span>'
        '<span class="arrow">&rarr;</span>'
        '<span class="after">{}</span></div></div>'.format(
            esc(label), formatter(before), formatter(after)
        )
    )


def render_html(report):
    """Render ``report`` (already sealed) as a standalone HTML document."""
    out = []
    add = out.append

    add("<!doctype html>")
    add('<html lang="en"><head><meta charset="utf-8" />')
    add('<meta name="viewport" content="width=device-width, initial-scale=1" />')
    add("<title>WARRANT &mdash; {}</title>".format(esc(report.get("incident_id", "Incident Report"))))
    add("<style>{}</style></head><body>".format(CSS))
    add('<div class="card"><header><h1>Incident Report</h1>')
    add('<div class="sub">WARRANT autonomous incident engineer</div></header><main>')

    add('<div class="facts">')
    add(_fact("Incident", report.get("incident_id", "—")))
    add(_fact("Detected", report.get("detected_at", "—")))
    add(_fact("Resolved", report.get("resolved_at", "—")))
    add(_fact("Final status", report.get("final_status", "—")))
    add("</div>")

    symptoms = report.get("symptoms") or []
    if symptoms:
        add("<section><h2>Symptoms</h2><ul>")
        for item in symptoms:
            add("<li>{}</li>".format(esc(item)))
        add("</ul></section>")

    evidence = report.get("evidence") or []
    if evidence:
        add("<section><h2>Evidence</h2>")
        for item in evidence:
            add(
                '<div class="ev"><div class="ev-source">{}</div>'
                "<div>{}</div></div>".format(
                    esc(item.get("source", "—")), esc(item.get("finding", ""))
                )
            )
        add("</section>")

    if report.get("root_cause"):
        add('<section><h2>Root cause</h2>')
        add('<div class="rootcause">{}</div>'.format(esc(report["root_cause"])))
        if report.get("offending_commit"):
            add('<div class="chip mono">{}</div>'.format(esc(report["offending_commit"])))
        add("</section>")

    actions = report.get("actions_proposed") or []
    if actions:
        add("<section><h2>Actions proposed</h2>")
        for action in actions:
            verdict = action.get("verdict", "")
            css_class = "denied" if verdict == "DENIED" else "allowed"
            add('<div class="act {}">'.format(css_class))
            add('<div class="verdict">{}</div><div>'.format(esc(verdict or "—")))
            add('<div class="mono">{}</div>'.format(esc(action.get("action", ""))))
            if action.get("reason"):
                add('<div class="reason">{}</div>'.format(esc(action["reason"])))
            add("</div></div>")
        add("</section>")

    if report.get("action_executed"):
        add("<section><h2>Action executed</h2>")
        add('<div class="rootcause">{}</div></section>'.format(esc(report["action_executed"])))

    tests = report.get("tests") or {}
    if tests:
        add('<section><h2>Verification</h2><div class="tests">')
        add('<div class="pass">{} passed</div>'.format(esc(tests.get("passed", 0))))
        add('<div class="fail">{} failed</div>'.format(esc(tests.get("failed", 0))))
        add("</div></section>")

    before = report.get("metrics_before") or {}
    after = report.get("metrics_after") or {}
    if before or after:
        add('<section><h2>Metrics</h2><div class="metrics">')
        add(_metric_row("Failure rate", before.get("failure_rate"), after.get("failure_rate"), fmt_percent))
        add(_metric_row("P95 latency", before.get("latency_p95_ms"), after.get("latency_p95_ms"), fmt_ms))
        add("</div></section>")

    add('<section><h2>Integrity</h2><div class="sha">')
    add('<div class="sha-label">Report SHA-256 &middot; tamper-evident</div>')
    add('<div class="sha-value mono">{}</div></div></section>'.format(esc(report.get(HASH_FIELD, "—"))))

    add("</main></div></body></html>")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Hash and render a WARRANT incident report."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=DEFAULT_INPUT,
        help="report JSON to read (default: ../fixtures/report.json)",
    )
    parser.add_argument(
        "output",
        nargs="?",
        help="HTML file to write (default: stdout)",
    )
    parser.add_argument(
        "--write-json",
        action="store_true",
        help="also write the computed hash back into the input JSON in place",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except (IOError, OSError) as err:
        sys.stderr.write("cannot read {}: {}\n".format(args.input, err))
        return 1
    except ValueError as err:
        sys.stderr.write("{} is not valid JSON: {}\n".format(args.input, err))
        return 1

    if not isinstance(report, dict):
        sys.stderr.write("{}: expected a JSON object\n".format(args.input))
        return 1

    sealed, digest, previous = seal(report)

    sys.stderr.write("report_sha256 {}\n".format(digest))
    if previous and previous != digest:
        sys.stderr.write(
            "warning: stored hash differs from computed hash\n"
            "  stored   {}\n  computed {}\n".format(previous, digest)
        )

    if args.write_json:
        with open(args.input, "w", encoding="utf-8") as fh:
            json.dump(sealed, fh, indent=2, sort_keys=False)
            fh.write("\n")
        sys.stderr.write("wrote hash back into {}\n".format(args.input))

    document = render_html(sealed)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(document)
        sys.stderr.write("wrote {}\n".format(args.output))
    else:
        sys.stdout.write(document)

    return 0


if __name__ == "__main__":
    sys.exit(main())
