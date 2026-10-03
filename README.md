# Warrant — Local Autonomous Incident Engineer

**Autonomy without unlimited authority.**

Warrant is an always-on AI incident engineer that runs entirely on a Dell GB10.
A watcher monitors a service in the background. When the service degrades, an
agent driven by a local LLM investigates on its own — metrics, logs, stack
traces, git history, configuration, source code — finds the root cause and
fixes it. Every action it takes passes a policy gate: unsafe actions are
denied with a reason, and the agent replans to a permitted fix. It then proves
the fix worked (tests + live health) and writes a tamper-evident incident
report. A live dashboard shows the whole loop.

Built in one day at the **Dell x NVIDIA AI Hackathon (Boston)** by Igor, Syed
and Mridhul.

---

## Contents

1. [The idea](#the-idea)
2. [The demo incident](#the-demo-incident-payment-retry-storm)
3. [What a real run looks like](#what-a-real-run-looks-like)
4. [How it works](#how-it-works)
5. [Architecture](#architecture)
6. [Safety model](#safety-model-policy-gate)
7. [Running it](#running-it)
8. [Configuration](#configuration)
9. [APIs and ports](#apis-and-ports)
10. [State files and contracts](#state-files-and-contracts)
11. [Testing and proving it is real](#testing-and-proving-it-is-real)
12. [Repository layout](#repository-layout)
13. [Team and ownership](#team-and-ownership)
14. [Status and known limitations](#status-and-known-limitations)
15. [Troubleshooting](#troubleshooting)

---

## The idea

Production incidents are expensive: every minute of a broken checkout is lost
revenue, and an engineer can spend hours digging through dashboards, logs and
git history to find a single bad line. AI could do that investigation — but
two things stop companies from letting it:

1. **Data.** Incident response needs the most sensitive material a company
   has: source code, logs, configuration, infrastructure details. Many
   organizations cannot send that to a cloud model.
2. **Trust.** An AI that can fix production can also break it. Nobody wants
   to give an autonomous agent unrestricted access.

Warrant answers both:

- **Local-first.** Model inference, the agent, its tools, the monitored
  service and the dashboard all run on the GB10. Nothing leaves the machine.
- **Bounded autonomy.** The agent decides what to investigate and what to do,
  but a policy layer decides what it is *allowed* to do. Denials are explained
  to the agent so it can choose a safe alternative.
- **Verified, auditable outcomes.** The agent does not declare victory because
  it changed a file. It runs the service's tests, watches live health recover,
  and seals the whole incident in a SHA-256-hashed report.

The investigation is genuinely agentic: the model is given tools and the
incident, not a script. Nothing tells it "read logs, then git, then config".

---

## The demo incident: payment retry storm

For the demo we built a **simulation of one specific, realistic incident** — a
small checkout service (not a real store) whose behavior is fully determined
by its configuration.

**Healthy configuration**

```
PAYMENT_TIMEOUT = 5.0     # seconds to wait for the payment gateway
PAYMENT_RETRIES = 1
```

**The trigger** (`POST /demo/trigger`, or the dashboard's *Trigger Demo
Incident* button) commits a well-intentioned change to the service's own git
repository, authored as a developer and titled *"Tune payment client for peak
sale traffic"*:

```diff
-PAYMENT_TIMEOUT = 5.0
+PAYMENT_TIMEOUT = 0.01
-PAYMENT_RETRIES = 1
+PAYMENT_RETRIES = 5
```

A harmless runbook commit lands on top of it, so the culprit is **not** the
latest commit, and an older, unrelated config commit acts as a decoy.

**The causal chain**

```
timeout 500x too short → almost every gateway call times out
→ each checkout retries 5 times with exponential backoff
→ ~5x more calls hit the payment gateway → the gateway slows down
→ even fewer calls succeed → checkout latency ~3 s, ~60–70% of checkouts fail
```

**Evidence the agent has to correlate** — no single log line states the cause:

| Source | What it shows |
|---|---|
| Metrics | failure rate ~1% → ~65%, p95 latency ~125 ms → ~3,200 ms, payment calls ~240 → ~1,200/min, retries ~0 → ~900/min |
| Logs | `PaymentTimeout: payment request timed out`, `retry attempt 1/5 … 5/5`, `checkout failed … payment_attempts=6`, tracebacks, a deploy line at the moment it started |
| Git | 7 commits; the bad one is second from the top |
| Config | the current values `0.01` / `5` |
| Tests | the service's own 12-test suite: 6 fail during the incident, all pass after the fix |

The simulation is **deterministic**: the same trigger produces the same
symptoms every time, and `reset_demo.sh` restores a clean, healthy state with
a fresh git history.

---

## What a real run looks like

Real runs on the GB10 with the local model (`nvidia/Qwen3.6-35B-A3B-NVFP4`)
through OpenClaw, start to finish with no human input:

| Step | What the agent did |
|---|---|
| Detect | The watcher saw failure rate cross 15% twice in a row and handed the incident to the agent |
| Investigate | Chose its own tools: service health, metrics history, log search, stack trace, git history, config, source files, commit diff |
| Diagnose | Identified the exact commit, the 5.0 → 0.01 / 1 → 5 change, explained the retry storm, and matched the failure onset to the commit time |
| Policy | Tried `revert_commit_and_push` → **DENIED** (pushing to main is outside the permitted scope) |
| Replan & act | Restored the two settings with `apply_remediation` on `checkout_service/config.py` → **ALLOWED** |
| Verify | Tests **12/12** passed; live health recovered |
| Report | `final_status: RECOVERED`, failure rate ~60% → 1.7%, p95 3,184 ms → ~150 ms, sealed with SHA-256 |

Detection to verified recovery took **about a minute**. (Whether the model
tries the denied action first is the model's own choice — nothing scripts the
order; in some runs it goes straight to the safe fix.)

---

## How it works

```mermaid
flowchart LR
    S[Checkout service<br/>:8081] -->|health every 2s| W[Watcher]
    W -->|incident event| A[Agent API<br/>:8082]
    A --> M[Local LLM<br/>vLLM on GB10]
    M -->|tool calls| T[Read-only tools<br/>health · metrics · logs · git · config · source]
    T --> M
    M -->|proposed action| P{Policy gate}
    P -->|DENIED + reason| M
    P -->|ALLOWED| F[Apply fix<br/>config.py]
    F --> V[Verify<br/>tests + live health]
    V --> R[Sealed report<br/>report.json]
    A -. events.jsonl / report.json .-> D[Dashboard<br/>:8083]
```

1. **Watch.** `warrant/watcher` polls `GET :8081/health` every 2 seconds.
   When `failure_rate > 0.15` for two consecutive polls it sends one incident
   event to `POST :8082/investigate`. It triggers once per incident and
   re-arms after three healthy readings.
2. **Investigate.** The agent gives the model the incident and its tools. The
   model decides which tool to call next based on what it has seen so far.
3. **Diagnose.** The model submits a structured diagnosis: root cause,
   evidence by source, offending commit, recommended next step.
4. **Act under policy.** Remediation tools go through the policy gate. A
   denial returns an instructive message naming the permitted alternative, so
   the model can replan.
5. **Verify.** `run_tests` runs the service's pytest suite; `verify_recovery`
   waits for live health to return under the thresholds (failure rate < 5%,
   p95 < 1,000 ms).
6. **Report.** `report.json` is written in the frozen contract format and
   sealed with SHA-256. Every step is streamed to `events.jsonl`, which the
   dashboard renders live.

---

## Architecture

Everything runs on one machine.

| Component | Path | What it does |
|---|---|---|
| Simulated service | `warrant/sim/` | FastAPI checkout service on :8081. Runs the real code of a separate "checkout-service" git repo against a simulated payment gateway; deterministic traffic, metrics, logs, trigger and reset |
| Watcher | `warrant/watcher/` | Always-on health poller; turns sustained degradation into one incident event |
| Agent | `warrant/agent/` | HTTP intake on :8082, the investigation/remediation loop, tools, MCP server, event stream, outcome and report generation |
| Policy | `warrant/policy/` | Deny-by-default remediation rules (app level) and an OpenShell sandbox policy template (OS level) |
| Dashboard | `warrant/ui/` | Single static page on :8083: service status, live timeline, policy decisions, root cause, recovery, report |
| Report renderer | `warrant/report/` | Owns the report hash canonicalization; renders a report to standalone HTML |
| Contracts | `warrant/contracts/` | Frozen JSON shapes shared by all components |
| Fixtures | `warrant/fixtures/` | Sample state files so the dashboard can be built and shown without a live run |
| Scripts | `warrant/scripts/` | Start icon, start/reset/run-all scripts, proof script |

**Model and runtime**

- Model: **`nvidia/Qwen3.6-35B-A3B-NVFP4`** (35B mixture-of-experts, ~3B
  active, NVFP4), served locally by **vLLM** under **NemoClaw** on port 8000.
- Agent backends (`WARRANT_AGENT_BACKEND`):
  - `openclaw` (default) — **OpenClaw** runs the tool loop inside the
    **NemoClaw / OpenShell sandbox** and calls Warrant's tools through an MCP
    server (`python -m warrant.agent.mcp_server`).
  - `direct` — `warrant/agent/agent.py` runs the loop itself and calls the
    vLLM OpenAI-compatible API (`LLM_BASE_URL`). Same tools, policy, timeline
    and report; used for development and as a fallback.

**Agent tools**

| Read-only investigation | Remediation and verification |
|---|---|
| `get_service_health`, `get_metrics_history`, `search_logs`, `inspect_stack_trace`, `get_recent_commits`, `get_git_diff`, `read_config`, `read_source_file`, `list_repository_files` | `revert_commit_and_push` (always denied), `apply_remediation` (policy-gated), `run_tests`, `verify_recovery`, plus `submit_diagnosis` |

Every tool returns a JSON object and catches its own errors
(`{"error": "..."}`), so the agent never dies on a tool failure.

**GB10 note.** On the GB10 the agent runs inside the NemoClaw sandbox while
the service, watcher and dashboard run on the host. The host's
`warrant/state/` folder is shared into the sandbox so the agent investigates
and fixes the real service and the dashboard sees its timeline and report.

---

## Safety model (policy gate)

Warrant has two layers; they are different things.

| | Layer 1: app-level policy | Layer 2: OpenShell sandbox |
|---|---|---|
| Where | `warrant/policy/policy.py` + `remediation_policy.json` | `warrant/policy/openshell/warrant-sandbox-policy.yaml` |
| Enforced by | Warrant, on every remediation tool call | OpenShell (Landlock + network proxy) on every process in the sandbox |
| Status | **Active** — produces the DENIED/ALLOWED decisions in the demo | Agent runs inside the sandbox; the Warrant-specific policy file is a template, not yet applied |

**Rules (deny by default)** — `warrant/policy/remediation_policy.json`:

- `revert_commit_and_push` — always denied (pushing to main).
- `apply_remediation(path, change)` — allowed only when `path` is
  `checkout_service/config.py` and every changed setting is listed and within
  range: `PAYMENT_TIMEOUT` 1.0–30.0, `PAYMENT_RETRIES` 0–2,
  `PAYMENT_RETRY_BACKOFF` 0.05–2.0.
- Anything else — denied.

Denials name the permitted alternative, so replanning is easy for the model:

```
DENIED: pushing to main is outside the permitted remediation scope.
Permitted write target: checkout configuration only
(checkout_service/config.py, via apply_remediation). Choose a different approach.
```

See `warrant/policy/README.md` for the full design and the steps to apply the
OpenShell layer.

---

## Running it

### Requirements

- Python 3.10+ and git
- A local model endpoint: on the GB10, NemoClaw's vLLM (port 8000). For the
  `openclaw` backend, the NemoClaw/OpenClaw setup with the `warrant` MCP
  server registered (see `warrant/agent/README.md`).

### On the GB10 — start icon (recommended)

Either install the icon once from the repo:

```bash
warrant/scripts/install_icon.sh
```

or copy `warrant/scripts/Warrant-Demo.desktop` to the desktop (it finds the
repo by itself: it searches your home folder, never `/sandbox`, preferring the
copy that has `demo.env`, then `~/Warrant`, then the newest). Right-click →
**Allow Launching** the first time.

Double-click **Warrant Demo**. It:

1. sets up `.venv` and installs packages on the first run,
2. loads `warrant/scripts/demo.env`,
3. resets to a clean, healthy state,
4. starts the service, agent, watcher and dashboard,
5. opens the dashboard.

Then press **Trigger Demo Incident** on the dashboard. Close the terminal
window to stop everything. Double-clicking again while the demo runs does not
reset it — it just reopens the dashboard.

### From a terminal

```bash
warrant/scripts/start_demo.sh     # same as the icon
```

or step by step:

```bash
python3 -m venv .venv
.venv/bin/pip install -r warrant/sim/requirements.txt -r warrant/agent/requirements.txt

warrant/scripts/reset_demo.sh               # clean, healthy state; waits until the watcher is armed
warrant/scripts/run_all.sh                  # sim, agent, watcher, dashboard; Ctrl+C stops all
curl -X POST localhost:8081/demo/trigger    # start the incident; the watcher takes it from there
warrant/scripts/proof.sh                    # show the evidence is real
```

Individual processes (from the repo root):

```bash
.venv/bin/python -m warrant.sim.app          # :8081 checkout service
.venv/bin/python -m warrant.agent.server     # :8082 agent
.venv/bin/python -m warrant.watcher.watcher  # polls :8081, triggers :8082
```

### Showing the dashboard on another screen

The dashboard is a web page served by the GB10. Open
`http://localhost:8083/ui/` in any browser on the GB10, or from a laptop with
SSH port forwarding:

```bash
ssh -L 8083:localhost:8083 -L 8081:localhost:8081 <user>@<gb10-address>
```

(8081 is needed so the *Trigger* button reaches the service.) To open the
dashboard on the GB10's own screen when the demo is started over SSH, set
`DASHBOARD_OPEN_CMD="env DISPLAY=:0 xdg-open"` in `demo.env`.

### Without OpenClaw (development or fallback)

```bash
WARRANT_AGENT_BACKEND=direct LLM_BASE_URL=http://<model-host>:8000/v1 warrant/scripts/start_demo.sh
```

Any OpenAI-compatible endpoint with tool calling works (vLLM on the GB10, or
Ollama for small local tests).

---

## Configuration

Machine-specific settings go in **`warrant/scripts/demo.env`** (copy
`demo.env.example`; the file is gitignored). Both the start icon and
`run_all.sh` read it — a desktop icon does not see variables from `~/.bashrc`.

| Variable | Default | Meaning |
|---|---|---|
| `WARRANT_AGENT_BACKEND` | `openclaw` | `openclaw` or `direct` |
| `WARRANT_OPENCLAW_CMD` | `openclaw agent --agent {agent} --local --session-id {session_id} --message {message}` | How to reach the NemoClaw-managed OpenClaw agent |
| `WARRANT_OPENCLAW_AGENT` | `main` | OpenClaw agent name |
| `WARRANT_OPENCLAW_CWD` | unset | Working directory for the OpenClaw command |
| `WARRANT_OPENCLAW_TIMEOUT` | `900` | Seconds allowed for one whole incident |
| `LLM_BASE_URL` | `http://localhost:8000/v1` | Model endpoint (direct backend) |
| `LLM_MODEL` | first model the server lists | Model name (direct backend) |
| `WARRANT_STATE_DIR` | `warrant/state` | Where runtime state lives (must be the same for host and sandbox) |
| `CHECKOUT_URL` | `http://localhost:8081` | Service address as seen by the agent |
| `WARRANT_VERIFY_TIMEOUT` | `45` | Seconds `verify_recovery` waits for recovery |
| `DASHBOARD_OPEN_CMD` | `xdg-open` / `open` | Command that opens the dashboard |

Watcher thresholds (`warrant/watcher/config.py`): poll every 2 s, incident at
failure rate > 0.15 for 2 polls, re-arm at < 0.05 for 3 polls. The full list
of agent settings is in `warrant/agent/README.md`.

---

## APIs and ports

| Port | Process | Owner |
|---|---|---|
| 8000 | vLLM / local model (NemoClaw) | Syed |
| 8081 | checkout service (sim) | Igor |
| 8082 | agent API | Syed |
| 8083 | dashboard | Mridhul |

**Checkout service (:8081)**

| Endpoint | |
|---|---|
| `GET /health` | health in the `contracts/health.json` shape |
| `GET /metrics` | health plus `payment_requests_per_minute` and `retry_count` |
| `GET /metrics/history?since=` | a snapshot every 5 s |
| `GET /logs?query=&since=&limit=` | log search → `{"lines": [...], "count": n}` |
| `POST /checkout` | run one checkout through the service |
| `POST /demo/trigger` | start the incident (409 if already active) |
| `POST /demo/reset` | clean, healthy state with a fresh git history |
| `GET /demo/info` | where the evidence lives on disk |

Rates are per-minute values computed over a 15-second window, so recovery shows
on the dashboard within ~15 s.

**Agent (:8082)**

| Endpoint | |
|---|---|
| `POST /investigate` | incident event from the watcher; returns 202 and runs in the background (409 if one is already running) |
| `GET /status` | `idle` / `investigating` / `diagnosed` / `resolved` / `inconclusive` / `failed`, plus `stage` while running |
| `GET /diagnosis` | latest diagnosis |
| `GET /outcome` | actions proposed, policy verdicts, tests, verification |

---

## State files and contracts

Runtime state lives in `warrant/state/` (gitignored). Formats are frozen in
`warrant/contracts/`; changing a field requires agreement from all three
owners.

| File | Written by | Read by |
|---|---|---|
| `health.json` | service | watcher, dashboard |
| `events.jsonl` | agent (one JSON object per line) | dashboard timeline |
| `diagnosis.json`, `outcome.json` | agent | agent API |
| `report.json` | agent, at incident close | dashboard, report renderer |
| `checkout-service/` | service (its own git repo) | agent tools, tests |
| `app.log`, `metrics.jsonl`, `logs/` | service and scripts | agent tools, humans |

- **Event fields:** `seq`, `timestamp`, `phase`
  (`detect|investigate|diagnose|plan|policy|act|verify|report`), `kind`
  (`tool_call|tool_result|reasoning|policy_decision|status`), `title`,
  `detail`, `policy_verdict` (`ALLOWED|DENIED|null`), `policy_reason`,
  `duration_ms`.
- **`final_status`** is exactly one of `RECOVERED` (verification ran and
  passed), `NOT_RECOVERED` (verification ran and failed) or `UNVERIFIED`
  (verification never ran — e.g. a run blocked by policy, with the DENIED
  action kept in `actions_proposed` and `action_executed: null`).
- **Report hash:** remove `report_sha256`, serialize with
  `json.dumps(report, sort_keys=True, separators=(",", ":"))`, UTF-8 encode,
  SHA-256, store the lowercase hex digest in `report_sha256`. The single
  implementation is `compute_sha256()` in `warrant/report/report_renderer.py`.

The dashboard labels each data feed **LIVE**, **FIXTURE** (fell back to
`warrant/fixtures/`) or **NO DATA**, so sample data can never be mistaken for
a live run.

---

## Testing and proving it is real

```bash
.venv/bin/python -m pytest warrant/sim/tests warrant/watcher/tests warrant/agent/tests warrant/policy/tests -q
```

Covers the simulation (determinism, symptoms, recovery, evidence, git history),
the watcher (debounce, re-arm, failure handling), the agent (backends, tools,
remediation, report, MCP server) and the policy rules.

`warrant/scripts/proof.sh` answers *"is this real or staged?"* live, straight
from the source: the service's git history, the latest config diff, the
running config, the service's own test suite (failing during the incident,
passing after the fix), live metrics and raw log lines.

---

## Repository layout

```
warrant/
├── agent/          agent API, tool loop, backends (openclaw/direct), tools, MCP server,
│                   remediation, events, outcome, report
├── policy/         remediation rules (app level) + OpenShell sandbox policy template
├── sim/            simulated checkout service, incident trigger, evidence
│   └── service_template/   the checkout-service repo: code, config, 12 tests, runbook
├── watcher/        health poller → incident events
├── ui/             dashboard (index.html, app.js, styles.css)
├── report/         report renderer + hash canonicalization
├── contracts/      frozen JSON contracts
├── fixtures/       sample state for the dashboard
├── scripts/        start icon, start/reset/run-all, proof, demo.env.example
└── state/          runtime artifacts (gitignored)
```

Each component keeps its own `requirements.txt`; there is no root requirements
file.

---

## Team and ownership

| Area | Owner |
|---|---|
| Agent, OpenClaw wiring, policy (`agent/`, `policy/`) | Syed |
| Simulation, watcher, scripts, README (`sim/`, `watcher/`, `scripts/`) | Igor |
| Dashboard, report renderer, fixtures (`ui/`, `report/`, `fixtures/`) | Mridhul |
| Contracts (`contracts/`) | frozen — all three |

Rule: edit only your own directories; ask the owner for changes elsewhere.

---

## Status and known limitations

| Area | State |
|---|---|
| Simulation, deterministic incident, evidence, reset | done |
| Watcher (always-on detection, debounce, re-arm) | done |
| Agent: investigation, diagnosis, policy-gated remediation, verification, sealed report | done — verified end to end on the GB10 with the local model through OpenClaw |
| Dashboard and report renderer | done |
| Start icon and demo scripts | done |
| OpenShell OS-level policy | template written, not yet applied — demo denials come from the app-level policy gate |

Known limitations:

- One incident type (by design for the hackathon scope).
- Whether the model attempts the denied action first is up to the model.
- Demo ports (8081–8083) are fixed; one demo per machine at a time.
- The simulated service is a simulation of this incident, not a production
  system.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard shows **FIXTURE** badges | No live file yet (e.g. right after a reset, before an incident). Trigger an incident; badges switch to LIVE |
| Trigger pressed but nothing investigates | The watcher was still in "incident active" mode. `reset_demo.sh` waits for it to re-arm — trigger after it says *ready* |
| `port 8081 already in use` | A demo is already running. The icon detects this and only reopens the dashboard; stop the other run to restart |
| Agent says `OpenClaw command 'openclaw' not found` | Set `WARRANT_OPENCLAW_CMD` in `demo.env`, or use `WARRANT_AGENT_BACKEND=direct` |
| Agent ends `inconclusive` | Model unreachable — check `LLM_BASE_URL` / OpenClaw, and `warrant/state/logs/agent.log` |
| First start is slow | The start script is creating `.venv` and installing packages (once) |
| Icon on Ubuntu does nothing | Right-click → **Allow Launching** |

Logs for every process: `warrant/state/logs/`.
