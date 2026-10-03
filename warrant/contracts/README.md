# Contracts — FROZEN

Copied from Section 4 of the build plan. Nobody edits these without a 3-way
verbal agreement (Igor, Syed, Mridhul).

| File | Written by | Read by | Runtime location |
|---|---|---|---|
| `health.json` | sim (`GET :8081/health`) | watcher, UI | `warrant/state/health.json` |
| `event.json` | agent (one line per step) | UI timeline | `warrant/state/events.jsonl` |
| `report.json` | agent, at incident close | UI, report renderer | `warrant/state/report.json` |

Values like `"a | b"` list the allowed values for that field.

## Tool signatures

    get_service_health()              -> health.json shape
    search_logs(query, since)         -> {"lines": [...], "count": n}
    get_recent_commits(limit)         -> {"commits": [{sha, msg, author, ts}]}
    get_git_diff(sha)                 -> {"diff": "unified diff text"}
    read_source_file(path)            -> {"path":..., "content":...}
    read_config()                     -> {"path":..., "content":...}
    run_tests()                       -> {"passed": n, "failed": n, "output":...}
    apply_remediation(path, change)   -> {"ok": bool, "detail":...}   [POLICY GATE]
    generate_incident_report()        -> report.json shape

Every tool returns a JSON object and catches its own exceptions, returning
`{"error": "..."}` instead of raising.

## Denial message format

When OpenShell denies, the string returned to the model must name the allowed
alternative:

    "DENIED: write to /app/db/ is outside your permitted write scope.
     Permitted write paths: /app/config/. Choose a different approach."
