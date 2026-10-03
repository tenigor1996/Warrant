"""Payment authorization with retry and exponential backoff."""

import logging

from checkout_service.gateway import PaymentDeclined, PaymentGatewayError, PaymentTimeout

log = logging.getLogger("checkout.payment")

RETRYABLE = (PaymentTimeout, PaymentGatewayError)


def charge(gateway, order, *, timeout, retries, backoff, clock):
    """Authorize `order`, retrying timeouts and gateway 5xx up to `retries` times.

    Returns (authorization_id, attempts). On failure the raised exception
    carries an `attempts` attribute.
    """
    attempt = 0
    while True:
        attempt += 1
        started = clock.now()
        try:
            return gateway.authorize(order, timeout=timeout), attempt
        except PaymentDeclined as exc:
            exc.attempts = attempt
            raise
        except RETRYABLE as exc:
            elapsed_ms = (clock.now() - started) * 1000
            if isinstance(exc, PaymentTimeout):
                log.warning(
                    "PaymentTimeout: payment request timed out order_id=%s attempt=%d elapsed_ms=%.0f",
                    order["order_id"], attempt, elapsed_ms,
                )
            else:
                log.warning(
                    "payment gateway error order_id=%s attempt=%d error=%r",
                    order["order_id"], attempt, str(exc),
                )
            if attempt > retries:
                exc.attempts = attempt
                raise
            delay = backoff * 2 ** (attempt - 1)
            log.warning(
                "retry attempt %d/%d order_id=%s backoff_ms=%.0f",
                attempt, retries, order["order_id"], delay * 1000,
            )
            clock.sleep(delay)
