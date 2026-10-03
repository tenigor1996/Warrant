import pytest

from checkout_service import config
from checkout_service.checkout import InvalidOrder, process_checkout, validate_order
from checkout_service.gateway import PaymentGateway
from fakes import SLOW_GATEWAY_LATENCY, FakeClock, FakeTransport, declined, ok, order


def _checkout(transport, clock=None):
    clock = clock or FakeClock()
    return process_checkout(order(), PaymentGateway(transport, clock), clock)


def test_checkout_completes_at_typical_gateway_latency():
    result = _checkout(FakeTransport(ok()))
    assert result.status == "completed"
    assert result.authorization_id == "auth_test"


def test_checkout_meets_slo_at_gateway_p99_latency():
    result = _checkout(FakeTransport(ok(SLOW_GATEWAY_LATENCY)))
    assert result.status == "completed"
    assert result.duration_ms <= config.CHECKOUT_SLO_SECONDS * 1000


def test_declined_checkout_reports_declined():
    result = _checkout(FakeTransport(declined()))
    assert result.status == "declined"


def test_rejects_non_positive_amount():
    with pytest.raises(InvalidOrder):
        validate_order(order(amount=0))


def test_rejects_unsupported_currency():
    with pytest.raises(InvalidOrder):
        validate_order(order(currency="XYZ"))
