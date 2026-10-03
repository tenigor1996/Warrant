import pytest

from checkout_service import config
from checkout_service.gateway import (
    PaymentDeclined,
    PaymentGateway,
    PaymentGatewayError,
    PaymentTimeout,
)
from checkout_service.payment_client import charge
from fakes import SLOW_GATEWAY_LATENCY, FakeClock, FakeTransport, declined, ok, order, unavailable

# One checkout must never fan out into more than this many gateway calls.
MAX_GATEWAY_CALLS_PER_CHECKOUT = 3


def _charge(transport, clock):
    return charge(
        PaymentGateway(transport, clock),
        order(),
        timeout=config.PAYMENT_TIMEOUT,
        retries=config.PAYMENT_RETRIES,
        backoff=config.PAYMENT_RETRY_BACKOFF,
        clock=clock,
    )


def test_authorizes_at_typical_gateway_latency():
    transport = FakeTransport(ok())
    authorization_id, attempts = _charge(transport, FakeClock())
    assert authorization_id == "auth_test"
    assert attempts == 1


def test_authorizes_at_gateway_p99_latency():
    transport = FakeTransport(ok(SLOW_GATEWAY_LATENCY))
    _, attempts = _charge(transport, FakeClock())
    assert attempts == 1


def test_declined_payment_is_not_retried():
    transport = FakeTransport(declined())
    with pytest.raises(PaymentDeclined):
        _charge(transport, FakeClock())
    assert transport.calls == 1


def test_transient_gateway_error_is_retried():
    transport = FakeTransport(unavailable(), ok())
    _, attempts = _charge(transport, FakeClock())
    assert attempts == 2


def test_persistent_gateway_error_is_raised():
    transport = FakeTransport(unavailable())
    with pytest.raises(PaymentGatewayError):
        _charge(transport, FakeClock())
    assert transport.calls == config.PAYMENT_RETRIES + 1


def test_retry_backoff_doubles():
    clock = FakeClock()
    with pytest.raises(PaymentGatewayError):
        _charge(FakeTransport(unavailable()), clock)
    backoffs = clock.sleeps[1::2]  # sleeps alternate: gateway wait, backoff, ...
    expected = [config.PAYMENT_RETRY_BACKOFF * 2 ** i for i in range(config.PAYMENT_RETRIES)]
    assert backoffs == pytest.approx(expected)


def test_gateway_calls_per_checkout_are_bounded():
    transport = FakeTransport(ok(latency=30.0))  # gateway never answers in time
    with pytest.raises(PaymentTimeout):
        _charge(transport, FakeClock())
    assert transport.calls <= MAX_GATEWAY_CALLS_PER_CHECKOUT, (
        f"retry amplification: {transport.calls} gateway calls for a single checkout"
    )
