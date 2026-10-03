# Fixtures

Sample state files so the dashboard can be built before anything else runs.
The UI polls `warrant/state/*` and falls back to these when state is absent.

- `health.json` — mid-incident health reading (contracts/health.json)
- `events.jsonl` — 15-event timeline covering every phase, including one
  DENIED and one ALLOWED policy decision (contracts/event.json)
- `report.json` — complete incident report (contracts/report.json)

Numbers, commit message and log wording match what `warrant/sim` actually
produces. `report_sha256` is SHA-256 of the report JSON without that field,
serialized with sorted keys and no whitespace: `json.dumps(r, sort_keys=True,
separators=(",", ":"))`. Mridhul owns `warrant/report/` and may change the
hashing scheme; regenerate this fixture if so.
