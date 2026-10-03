"""
sim/app.py — Simulated checkout service HTTP API (port 8081).

Run from the repo root:
    python -m warrant.sim.app

Endpoints
    GET  /health             contracts/health.json shape (read by watcher + UI)
    GET  /metrics            health plus payment_requests_per_minute, retry_count
    GET  /metrics/history    metrics snapshots every 5s (?since=ISO timestamp)
    GET  /logs               search app.log (?query=&since=&limit=) -> {"lines", "count"}
    POST /checkout           run one checkout through the service
    POST /demo/trigger       introduce the incident (config change + git commits)
    POST /demo/reset         back to a clean healthy state with fresh history
    GET  /demo/info          where the evidence lives on disk

The service only produces evidence. Nothing here diagnoses anything.
"""

import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from warrant.sim import workspace
from warrant.sim.engine import Simulator

STATE_DIR = Path(os.environ.get("WARRANT_STATE_DIR", Path(__file__).resolve().parents[1] / "state"))
WORKSPACE = STATE_DIR / "checkout-service"
PORT = int(os.environ.get("SIM_PORT", "8081"))
TICK_SECONDS = 0.25

sim: Simulator = None
_stop = threading.Event()


def _run_loop():
    while not _stop.is_set():
        try:
            sim.advance(time.time())
        except Exception as exc:  # keep generating traffic no matter what
            print(f"[SIM] tick failed: {exc!r}")
        _stop.wait(TICK_SECONDS)


@asynccontextmanager
async def lifespan(app):
    global sim
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not (WORKSPACE / ".git").exists():
        workspace.build(WORKSPACE)
    sim = Simulator(WORKSPACE, STATE_DIR)
    sim.log_deploy(workspace.head(WORKSPACE))
    thread = threading.Thread(target=_run_loop, daemon=True)
    thread.start()
    yield
    _stop.set()
    thread.join(timeout=2)


app = FastAPI(title="checkout-service (simulated)", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return sim.health(time.time())


@app.get("/metrics")
def metrics():
    return sim.metrics(time.time())


@app.get("/metrics/history")
def metrics_history(since: str = None):
    return {"snapshots": sim.history_since(since)}


@app.get("/logs")
def logs(query: str = "", since: str = None, limit: int = 200):
    """Log entries (a line plus any traceback lines) containing `query`, newest last."""
    entries = []
    if sim.app_log.exists():
        for line in sim.app_log.read_text().splitlines():
            if line[:4].isdigit() and line[4:5] == "-":
                entries.append(line)
            elif entries:
                entries[-1] += "\n" + line
    matches = [e for e in entries
               if query.lower() in e.lower() and (since is None or e[:24] >= since)]
    return {"lines": matches[-limit:], "count": len(matches)}


@app.post("/checkout")
def checkout(order: dict):
    order.setdefault("order_id", f"ord_api_{int(time.time() * 1000) % 10**8:08d}")
    try:
        return sim.checkout_once(order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/demo/trigger")
def demo_trigger():
    with sim.lock:
        try:
            commits = workspace.trigger_incident(WORKSPACE)
        except workspace.IncidentAlreadyActive as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        sim.log_deploy(commits["head"])
    return {"triggered": True, "head": commits["head"]}


@app.post("/demo/reset")
def demo_reset():
    head = sim.reset()
    return {"reset": True, "head": head}


@app.get("/demo/info")
def demo_info():
    return {
        "repo": str(WORKSPACE),
        "config": str(WORKSPACE / workspace.CONFIG_RELPATH),
        "app_log": str(sim.app_log),
        "health_file": str(sim.health_file),
        "metrics_file": str(sim.metrics_file),
        "incident_active": workspace.is_incident(WORKSPACE),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
