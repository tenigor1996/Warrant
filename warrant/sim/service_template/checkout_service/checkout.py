"""Checkout request handling."""

import logging
from dataclasses import dataclass
from typing import Optional

from checkout_service import config as default_config
from checkout_service.gateway import PaymentDeclined, PaymentError
from checkout_service.payment_client import charge

log = logging.getLogger("checkout.api")


class InvalidOrder(ValueError):
    pass


@dataclass
class CheckoutResult:
    order_id: str
    status: str  # completed | declined | failed
    payment_attempts: int
    duration_ms: float
    authorization_id: Optional[str] = None
    error: Optional[str] = None


def validate_order(order, settings=default_config):
    if order.get("amount", 0) <= 0:
        raise InvalidOrder("amount must be positive")
    if order.get("currency") not in settings.SUPPORTED_CURRENCIES:
        raise InvalidOrder(f"unsupported currency: {order.get('currency')!r}")
    if not 1 <= order.get("items", 0) <= settings.MAX_CART_ITEMS:
        raise InvalidOrder("cart must contain between 1 and MAX_CART_ITEMS items")


def process_checkout(order, gateway, clock, settings=default_config):
    """Validate the order and authorize payment. Returns a CheckoutResult."""
    started = clock.now()
    validate_order(order, settings)
    order_id = order["order_id"]

    try:
        authorization_id, attempts = charge(
            gateway,
            order,
            timeout=settings.PAYMENT_TIMEOUT,
            retries=settings.PAYMENT_RETRIES,
            backoff=settings.PAYMENT_RETRY_BACKOFF,
            clock=clock,
        )
    except PaymentDeclined as exc:
        duration_ms = (clock.now() - started) * 1000
        log.info("checkout declined order_id=%s reason=%s duration_ms=%.0f", order_id, exc, duration_ms)
        return CheckoutResult(order_id, "declined", exc.attempts, duration_ms, error=str(exc))
    except PaymentError as exc:
        duration_ms = (clock.now() - started) * 1000
        log.error(
            "checkout failed order_id=%s error=%s payment_attempts=%d duration_ms=%.0f",
            order_id, type(exc).__name__, exc.attempts, duration_ms,
            exc_info=True,
        )
        return CheckoutResult(order_id, "failed", exc.attempts, duration_ms, error=type(exc).__name__)

    duration_ms = (clock.now() - started) * 1000
    log.info(
        "checkout completed order_id=%s amount=%.2f %s payment_attempts=%d duration_ms=%.0f",
        order_id, order["amount"], order["currency"], attempts, duration_ms,
    )
    return CheckoutResult(order_id, "completed", attempts, duration_ms, authorization_id=authorization_id)
