"""
sim/engine.py — Deterministic traffic generator, payment gateway model, metrics.

Each tick, the simulator runs the checkout requests that fell due since the last
tick (a fixed schedule of REQUEST_RATE per second) through the *real* code in
the checkout-service repo (checkout_service.checkout.process_checkout). Only the
payment gateway on the other end of the wire is simulated, and time spent
waiting on it is accounted on a simulated clock rather than slept.

Nothing here knows about the incident. Behaviour follows from whatever
config.py currently says:

    short PAYMENT_TIMEOUT  -> most gateway calls exceed it -> PaymentTimeout
    more PAYMENT_RETRIES   -> each checkout makes several gateway calls
    retries + backoff      -> checkout latency climbs into seconds
    extra gateway calls    -> gateway load rises -> gateway responses slow down
                              -> even fewer calls beat the timeout

Determinism: request n after config version k uses Random(f"{seed}:{k}:{n}")
and requests are timestamped by schedule, not by wall clock, so the same
config produces the same outcomes on every run.

Outputs (all under the state dir):
    app.log         application log written by the checkout service's loggers
    health.json     latest health snapshot (contracts/health.json shape)
    metrics.jsonl   one metrics snapshot every HISTORY_INTERVAL seconds
"""

import hashlib
import importlib
import json
import logging
import math
import random
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from warrant.sim import workspace

REQUEST_RATE = 4.0           # checkout requests per second
WINDOW_SECONDS = 15.0        # sliding window for rates; short so recovery shows fast
HISTORY_INTERVAL = 5.0       # seconds between metrics.jsonl snapshots
HISTORY_KEEP = 720           # one hour at 5s

CHECKOUT_OVERHEAD = (0.010, 0.025)   # seconds of non-payment work per request

# Simulated payments gateway
DECLINE_RATE = 0.010
GATEWAY_ERROR_RATE = 0.004
FAST_PATH_RATE = 0.10                # cached authorizations answered in a few ms
FAST_PATH_LATENCY = (0.002, 0.009)
GATEWAY_MEDIAN_LATENCY = 0.075
GATEWAY_LATENCY_SIGMA = 0.22
GATEWAY_ERROR_LATENCY = 0.004
GATEWAY_CAPACITY_PER_MIN = 600       # beyond this the gateway slows down

# Health status
DEGRADED_FAILURE_RATE = 0.10
DEGRADED_P95_MS = 1000
RECOVERING_SECONDS = 20


def iso(ts: float, millis: bool = False) -> str:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    if millis:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class SimClock:
    """Simulated time for one request."""

    def __init__(self):
        self.elapsed = 0.0

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class SimulatedGatewayTransport:
    """Stands in for the HTTP transport to the payments gateway."""

    def __init__(self, rng: random.Random, load_factor: float, response_cls):
        self.rng = rng
        self.load_factor = load_factor
        self.response_cls = response_cls
        self.calls = 0

    def post(self, path, payload):
        self.calls += 1
        rng, m = self.rng, self.load_factor
        roll = rng.random()
        if roll < DECLINE_RATE:
            return self.response_cls(402, rng.uniform(*FAST_PATH_LATENCY) * m, {"error": "card_declined"})
        if roll < DECLINE_RATE + GATEWAY_ERROR_RATE:
            return self.response_cls(503, GATEWAY_ERROR_LATENCY * m, {"error": "upstream_unavailable"})
        if rng.random() < FAST_PATH_RATE:
            latency = rng.uniform(*FAST_PATH_LATENCY)
        else:
            latency = rng.lognormvariate(math.log(GATEWAY_MEDIAN_LATENCY), GATEWAY_LATENCY_SIGMA)
        return self.response_cls(200, latency * m, {"authorization_id": f"auth_{rng.getrandbits(40):010x}"})


@dataclass
class Sample:
    t: float
    status: str          # completed | declined | failed
    latency_ms: float
    payment_calls: int


class Simulator:
    def __init__(self, ws: Path, state_dir: Path, seed: int = 1337):
        self.ws = Path(ws)
        self.state_dir = Path(state_dir)
        self.seed = seed
        self.lock = threading.RLock()
        self.app_log = self.state_dir / "app.log"
        self.health_file = self.state_dir / "health.json"
        self.metrics_file = self.state_dir / "metrics.jsonl"
        self.log = logging.getLogger("checkout")
        self._handler = None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._open_log()
        self._load_service_code()
        self._reset_counters(time.time())

    # ---- lifecycle -----------------------------------------------------------

    def reset(self, now: float = None) -> str:
        """Rebuild the repo, wipe logs and metrics, start healthy. Returns HEAD sha."""
        now = time.time() if now is None else now
        with self.lock:
            head = workspace.build(self.ws, now)
            for path in (self.app_log, self.metrics_file, self.health_file):
                path.unlink(missing_ok=True)
            self._open_log()
            self._load_service_code()
            self._reset_counters(now)
            self.log_deploy(head)
            return head

    def log_deploy(self, sha: str) -> None:
        logging.getLogger("checkout.deploy").info("deploy completed service=checkout revision=%s", sha[:7])

    def advance(self, now: float) -> None:
        """Run every request scheduled up to `now`, then refresh health/metrics files."""
        with self.lock:
            self._refresh_config()
            while self.next_request_t <= now:
                rng = random.Random(f"{self.seed}:{self.config_version}:{self.seq}")
                self._run_request(self.next_request_t, rng)
                self.seq += 1
                self.next_request_t += 1.0 / REQUEST_RATE
            while self.next_history_t <= now:
                snap = self.metrics(self.next_history_t)
                self.history.append(snap)
                with self.metrics_file.open("a") as f:
                    f.write(json.dumps(snap) + "\n")
                self.next_history_t += HISTORY_INTERVAL
            self._write_health(now)

    # ---- metrics ---------------------------------------------------------------

    def metrics(self, now: float) -> dict:
        with self.lock:
            window = [s for s in self.samples if now - WINDOW_SECONDS < s.t <= now]
            n = len(window)
            failures = sum(1 for s in window if s.status != "completed")
            calls = sum(s.payment_calls for s in window)
            failure_rate = failures / n if n else 0.0
            p95 = _percentile([s.latency_ms for s in window], 0.95)
            per_min = 60.0 / WINDOW_SECONDS

            if failure_rate >= DEGRADED_FAILURE_RATE or p95 >= DEGRADED_P95_MS:
                status = "degraded"
                self.last_degraded_t = now
            elif self.last_degraded_t is not None and now - self.last_degraded_t < RECOVERING_SECONDS:
                status = "recovering"
            else:
                status = "healthy"

            return {
                "timestamp": iso(now),
                "status": status,
                "failure_rate": round(failure_rate, 3),
                "latency_p95_ms": round(p95),
                "requests_last_minute": round(n * per_min),
                "errors_last_minute": round(failures * per_min),
                "payment_requests_per_minute": round(calls * per_min),
                "retry_count": round((calls - n) * per_min),
            }

    def health(self, now: float) -> dict:
        """contracts/health.json shape."""
        m = self.metrics(now)
        keys = ("timestamp", "status", "failure_rate", "latency_p95_ms",
                "requests_last_minute", "errors_last_minute")
        return {k: m[k] for k in keys}

    def history_since(self, since: str = None) -> list:
        with self.lock:
            return [h for h in self.history if since is None or h["timestamp"] >= since]

    # ---- ad-hoc requests -------------------------------------------------------

    def checkout_once(self, order: dict, now: float = None) -> dict:
        """Run one externally submitted checkout (POST /checkout)."""
        now = time.time() if now is None else now
        with self.lock:
            self._refresh_config()
            self.adhoc += 1
            rng = random.Random(f"{self.seed}:adhoc:{self.adhoc}")
            result = self._run_request(now, rng, order=order)
            return result.__dict__

    # ---- internals -------------------------------------------------------------

    def _run_request(self, t: float, rng: random.Random, order: dict = None):
        if order is None:
            order = {
                "order_id": f"ord_{rng.getrandbits(32):08x}",
                "amount": round(rng.uniform(12, 240), 2),
                "currency": "USD",
                "items": rng.randint(1, 6),
            }
        transport = SimulatedGatewayTransport(rng, self._load_factor(t), self._gateway.GatewayResponse)
        clock = SimClock()
        clock.sleep(rng.uniform(*CHECKOUT_OVERHEAD))
        gateway = self._gateway.PaymentGateway(transport, clock)
        result = self._checkout.process_checkout(order, gateway, clock, settings=self.settings)
        self.samples.append(Sample(t, result.status, clock.now() * 1000, transport.calls))
        while self.samples and self.samples[0].t < t - 60:
            self.samples.popleft()
        return result

    def _load_factor(self, t: float) -> float:
        calls = sum(s.payment_calls for s in self.samples if s.t > t - WINDOW_SECONDS)
        rate = calls * 60.0 / WINDOW_SECONDS
        return max(1.0, rate / GATEWAY_CAPACITY_PER_MIN) ** 0.5

    def _refresh_config(self) -> None:
        text = (self.ws / workspace.CONFIG_RELPATH).read_text()
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest == self.config_digest:
            return
        values = workspace.read_config(self.ws)
        self.settings = SimpleNamespace(**values)
        if self.config_digest is not None:
            self.config_version += 1
            self.seq = 0
            logging.getLogger("checkout.config").info("configuration reloaded file=%s", workspace.CONFIG_RELPATH)
        self.config_digest = digest

    def _reset_counters(self, now: float) -> None:
        self.samples = deque()
        self.history = deque(maxlen=HISTORY_KEEP)
        self.config_version = 0
        self.config_digest = None
        self.settings = None
        self.seq = 0
        self.adhoc = 0
        self.next_request_t = now
        self.next_history_t = now
        self.last_degraded_t = None

    def _load_service_code(self) -> None:
        """Import checkout_service from the repo (fresh, in case it was rebuilt)."""
        for name in [m for m in sys.modules if m == "checkout_service" or m.startswith("checkout_service.")]:
            del sys.modules[name]
        path = str(self.ws)
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
        importlib.invalidate_caches()
        self._checkout = importlib.import_module("checkout_service.checkout")
        self._gateway = importlib.import_module("checkout_service.gateway")

    def _open_log(self) -> None:
        if self._handler is not None:
            self.log.removeHandler(self._handler)
            self._handler.close()
        handler = logging.FileHandler(self.app_log)
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03dZ %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        self.log.addHandler(handler)
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self._handler = handler

    def _write_health(self, now: float) -> None:
        tmp = self.health_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.health(now), indent=2))
        tmp.replace(self.health_file)


def _percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]
