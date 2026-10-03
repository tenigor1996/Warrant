"""Run from the repo root:  python -m pytest warrant/watcher/tests -q"""

from warrant.watcher.watcher import Watcher


def health(failure_rate, status="degraded"):
    return {"timestamp": "t", "status": status, "failure_rate": failure_rate,
            "latency_p95_ms": 3400, "requests_last_minute": 240, "errors_last_minute": 160}


def make(readings, send_fails=0):
    readings = list(readings)
    sent, failures = [], [send_fails]

    def fetch():
        value = readings.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def send(event):
        if failures[0] > 0:
            failures[0] -= 1
            raise ConnectionError("agent down")
        sent.append(event)

    return Watcher(fetch=fetch, send=send, clock=lambda: 1_800_000_000.0), sent


def run(watcher, n):
    for _ in range(n):
        watcher.poll_once()


def test_single_bad_reading_does_not_trigger():
    w, sent = make([health(0.01), health(0.60), health(0.01)])
    run(w, 3)
    assert sent == []


def test_two_consecutive_bad_readings_trigger_once():
    w, sent = make([health(0.01)] + [health(0.68)] * 15)
    run(w, 16)
    assert len(sent) == 1
    event = sent[0]
    assert event["event_type"] == "service_degradation"
    assert event["service"] == "checkout"
    assert event["failure_rate"] == 0.68


def test_rearms_after_sustained_recovery_and_triggers_again():
    readings = [health(0.68)] * 2 + [health(0.01, "healthy")] * 3 + [health(0.70)] * 2
    w, sent = make(readings)
    run(w, len(readings))
    assert len(sent) == 2


def test_partial_recovery_does_not_rearm():
    readings = [health(0.68)] * 2 + [health(0.01)] * 2 + [health(0.10)] + [health(0.68)] * 2
    w, sent = make(readings)
    run(w, len(readings))
    assert len(sent) == 1


def test_survives_unreachable_service_and_bad_payloads():
    readings = [ConnectionError("down"), {"status": "weird"}, {"failure_rate": "nan?"},
                health(0.68), health(0.68)]
    w, sent = make(readings)
    run(w, len(readings))
    assert len(sent) == 1


def test_retries_delivery_when_agent_down_without_duplicating():
    w, sent = make([health(0.68)] * 6, send_fails=2)
    run(w, 6)
    assert len(sent) == 1
    assert w.pending_event is None
