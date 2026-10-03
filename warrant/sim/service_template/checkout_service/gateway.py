"""Client for the internal payments gateway (authorization endpoint)."""

from dataclasses import dataclass, field


class PaymentError(Exception):
    """Base class for payment failures."""


class PaymentTimeout(PaymentError):
    """The gateway did not respond within the configured timeout."""


class PaymentDeclined(PaymentError):
    """The gateway declined the payment. Not retryable."""


class PaymentGatewayError(PaymentError):
    """The gateway returned a 5xx. Retryable."""


@dataclass
class GatewayResponse:
    status: int
    latency: float  # seconds until the gateway responds
    body: dict = field(default_factory=dict)


class PaymentGateway:
    """Authorizes payments through a transport.

    `transport.post(path, payload)` returns a GatewayResponse.
    `clock.sleep(seconds)` accounts for time spent waiting on the gateway.
    """

    AUTHORIZE_PATH = "/authorizations"

    def __init__(self, transport, clock):
        self._transport = transport
        self._clock = clock

    def authorize(self, order, timeout):
        payload = {
            "order_id": order["order_id"],
            "amount": order["amount"],
            "currency": order["currency"],
        }
        response = self._transport.post(self.AUTHORIZE_PATH, payload)

        if response.latency > timeout:
            self._clock.sleep(timeout)
            raise PaymentTimeout(f"payment request timed out (POST {self.AUTHORIZE_PATH})")

        self._clock.sleep(response.latency)
        if response.status == 402:
            raise PaymentDeclined(response.body.get("error", "card_declined"))
        if response.status >= 500:
            raise PaymentGatewayError(f"gateway returned HTTP {response.status}")
        return response.body["authorization_id"]
