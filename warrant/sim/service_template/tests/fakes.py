"""Test doubles for the payment gateway transport and the clock."""

from checkout_service.gateway import GatewayResponse

TYPICAL_GATEWAY_LATENCY = 0.120  # seconds, gateway p50
SLOW_GATEWAY_LATENCY = 0.900     # seconds, gateway p99


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


class FakeTransport:
    """Returns scripted responses in order, repeating the last one."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, path, payload):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def ok(latency=TYPICAL_GATEWAY_LATENCY):
    return GatewayResponse(200, latency, {"authorization_id": "auth_test"})


def declined():
    return GatewayResponse(402, 0.005, {"error": "card_declined"})


def unavailable():
    return GatewayResponse(503, 0.005, {"error": "upstream_unavailable"})


def order(**overrides):
    base = {"order_id": "ord_test", "amount": 49.99, "currency": "USD", "items": 2}
    base.update(overrides)
    return base
