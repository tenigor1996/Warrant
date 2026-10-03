# Agent: integration notes

    watcher -> POST :8082/investigate -> backend -> local Qwen -> Warrant tools
                                            |
                 openclaw (default): OpenClaw agent -> MCP -> mcp_server.py
                 direct            : agent.py -> llm_client.py -> vLLM

The watcher and the :8082 API are unchanged (`POST /investigate`, `GET /status`,
`GET /diagnosis`; `GET /outcome` is new). Investigations still run in a
background thread.

## Backends

| `WARRANT_AGENT_BACKEND` | Who runs the tool loop | Who talks to the model | Needs |
|---|---|---|---|
| `openclaw` (default) | OpenClaw | OpenClaw, via NemoClaw's inference route | OpenClaw + the `warrant` MCP server registered |
| `direct` | `agent.py` | `llm_client.py` -> `LLM_BASE_URL` | a vLLM endpoint |

The default is `openclaw`. On a machine without OpenClaw the server still
starts, prints a warning, and each incident ends `inconclusive` with the reason
on the timeline; it never falls back to `direct` on its own. For local
development: `WARRANT_AGENT_BACKEND=direct`. The tests need neither.

Both backends execute tools through `toolbox.execute()`, so the timeline, the
policy gate and `state/outcome.json` are the same either way.

## Environment

| Variable | Default | |
|---|---|---|
| `WARRANT_AGENT_BACKEND` | `openclaw` | `openclaw` or `direct` |
| `WARRANT_OPENCLAW_CMD` | `openclaw agent --agent {agent} --local --session-id {session_id} --message {message}` | run without a shell; placeholders filled per argument |
| `WARRANT_OPENCLAW_AGENT` | `main` | value of `{agent}` |
| `WARRANT_OPENCLAW_TIMEOUT` | `900` | seconds for the whole incident |
| `WARRANT_OPENCLAW_CWD` | unset | working directory for the command |
| `WARRANT_AUTO_REMEDIATE` | `1` | direct backend: continue from diagnosis to remediation |
| `WARRANT_STATE_DIR`, `SERVICE_REPO`, `CHECKOUT_URL` | as before | must resolve to the same files/service for the :8082 server **and** the MCP server |
| `WARRANT_POLICY_FILE` | `warrant/policy/remediation_policy.json` | app-level rules |
| `WARRANT_TEST_PYTHON` | the running interpreter | interpreter with pytest, for `run_tests` |
| `WARRANT_VERIFY_TIMEOUT` | `45` | seconds `verify_recovery` waits for health |
| `WARRANT_MCP_TRANSPORT` | `stdio` | `sse` / `streamable-http` to serve the MCP server over HTTP |
| `LLM_BASE_URL`, `LLM_MODEL`, ... | as before | direct backend only |

## MCP server

    pip install -r warrant/agent/requirements.txt      # mcp==1.30.0
    python -m warrant.agent.mcp_server                 # stdio, from the repo root

Tools: the nine read-only tools from `tools.py`, `submit_diagnosis`, and from
`remediation.py`: `revert_commit_and_push` (always denied),
`apply_remediation` (policy-gated), `run_tests`, `verify_recovery`. All return
a JSON object; failures are `{"error": "..."}`.

The MCP server and the :8082 server are separate processes that meet in the
state directory: the MCP server appends to `events.jsonl` and writes
`diagnosis.json` / `outcome.json`; the :8082 server reads them.

## Timeline (`state/events.jsonl`, contracts/event.json)

| Stage | phase / kind | Written by |
|---|---|---|
| detection | `detect` / `status` | backend |
| investigation start | `investigate` / `status` | backend |
| tool call, tool result | `investigate` / `tool_call`, `tool_result` | toolbox |
| diagnosis | `diagnose` / `reasoning` (the submitted root cause) | submit_diagnosis |
| policy decision, denied | `policy` / `policy_decision`, `DENIED` + the denial text | toolbox |
| remediation | `act` / `policy_decision` `ALLOWED`, then `act` / `status` | toolbox |
| verification | `verify` / `tool_result` (tests), `verify` / `status` (recovered or not) | toolbox |

No chain-of-thought is recorded. With OpenClaw the only model-authored text on
the timeline is the root cause it submitted. The direct backend also records the
model's visible message (after `<think>` blocks are stripped) as "Agent note".

## Report (`state/report.json`, contracts/report.json)

Written by `report.py` when the :8082 server finishes an incident, after the
last verification, for both backends. It is not an MCP tool: the model cannot
produce it early. Every field comes from `state/outcome.json`, the timeline or
the service's `/metrics/history`; a field with no source is `null` and is named
in the "Incident report generated" timeline entry. `final_status` is
`RECOVERED`, `NOT_RECOVERED` (verification failed) or `UNVERIFIED` (no
verification ran). `report_sha256` is computed by
`warrant.report.report_renderer.compute_sha256`. The file is replaced
atomically.

## Not done here

- Whether the model tries the denied action first is up to the model. Nothing
  scripts the order.
- OpenShell enforcement is a template; see `warrant/policy/README.md`.

## Tests

    python -m pytest warrant/agent/tests warrant/policy/tests -q
