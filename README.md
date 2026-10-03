# Warrant — Local Autonomous Incident Engineer

Dell x NVIDIA Hackathon. Igor, Syed, Mridhul.

A watcher notices the checkout service degrading. With no human prompt, an
agent on the local model investigates with tools (metrics, logs, git, config),
finds the root cause, proposes a fix, has an unsafe action DENIED by OpenShell,
applies a safe fix, runs tests, confirms recovery and writes a hashed incident
report. One dashboard page shows it all.

**Autonomy without unlimited authority.**

## Model

Local inference on the GB10 via NemoClaw-managed vLLM:
`nvidia/Qwen3.6-35B-A3B-NVFP4` (installer selection, per project doc).
**Syed: confirm this is what the installer actually picked.** Backup only:
Nemotron-3.5-Lightning-30B on the external SSD.

## Ports

| Port | Service | Owner |
|---|---|---|
| 8000 | vLLM / local model | Syed |
| 8081 | checkout service (sim) | Igor |
| 8082 | agent API | Syed |
| 8083 | dashboard | Mridhul |

Nothing else binds a port without announcing it.

## Ownership — edit only your own directories

| Path | Owner | Contents |
|---|---|---|
| `warrant/agent/` | Syed | agent loop, OpenClaw wiring, tools |
| `warrant/policy/` | Syed | OpenShell policy files |
| `warrant/sim/` | Igor | checkout service, incident trigger, evidence |
| `warrant/watcher/` | Igor | health poller / auto-trigger |
| `warrant/scripts/` | Igor | run_all.sh, reset_demo.sh |
| `warrant/ui/` | Mridhul | dashboard (single HTML page) |
| `warrant/report/` | Mridhul | report renderer + SHA-256 |
| `warrant/fixtures/` | Mridhul | sample state files for UI dev |
| `warrant/contracts/` | FROZEN | schemas; 3-way agreement to change |
| `warrant/state/` | gitignored | runtime artifacts, never committed |
| `README.md`, `.gitignore` | Igor | |

Each lane keeps its own `requirements.txt`. There is no root requirements file.

## Git protocol

- Work on `main`. Always `git pull --rebase origin main` before pushing.
- Small commits every 15–20 minutes. Never force-push `main`.
- A conflict means ownership was broken: `git rebase --abort` and say so in chat.

## Running (from the repo root)

One-time setup:

    python3 -m venv .venv
    .venv/bin/pip install -r warrant/sim/requirements.txt

Demo:

    warrant/scripts/reset_demo.sh                # clean, healthy state (run before every rehearsal)
    warrant/scripts/run_all.sh                   # starts sim, agent, watcher, dashboard; Ctrl+C stops all
    curl -X POST localhost:8081/demo/trigger     # start the incident; the watcher takes it from there
    warrant/scripts/proof.sh                     # "is this real?" — git history, diff, live tests, metrics, logs

Logs: `warrant/state/logs/`. Model endpoint: `LLM_BASE_URL` (default
`http://localhost:8000/v1`), model name: `LLM_MODEL` (default: first model the
server lists).

Individual processes, if needed:

    .venv/bin/python -m warrant.sim.app          # :8081 checkout service
    .venv/bin/python -m warrant.agent.server     # :8082 agent
    .venv/bin/python -m warrant.watcher.watcher  # polls :8081, triggers :8082

Tests:

    .venv/bin/python -m pytest warrant/sim/tests warrant/watcher/tests warrant/agent/tests -q

## Where the incident lives

The simulated service has its own git repo, built by `reset_demo.sh` at
`warrant/state/checkout-service/` (gitignored, so trigger commits never touch
this repo). Its config file — the remediation target and the OpenShell write
scope — is:

    warrant/state/checkout-service/checkout_service/config.py

(The Build Plan's `warrant/sim/config.py` and `/app/config/` are placeholders
for this path.)

## Status

| Component | State |
|---|---|
| sim: checkout service, deterministic retry-storm incident, evidence, reset | done |
| watcher: 2s polling, 2-reading trigger, one event per incident, re-arm | done |
| agent: read-only investigation loop + event stream | done, **not yet run on a real model** |
| contracts + fixtures | done |
| scripts: reset_demo.sh, run_all.sh, proof.sh | done |
| OpenShell policy, remediation, verification, report | not started |
| dashboard | not started |
