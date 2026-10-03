# checkout-service

Handles checkout requests: validates the order and authorizes payment through
the internal payments gateway.

## Layout

- `checkout_service/checkout.py` — request handling
- `checkout_service/payment_client.py` — authorization with retry/backoff
- `checkout_service/gateway.py` — gateway client
- `checkout_service/config.py` — runtime configuration (hot-reloaded)

## Tests

    python -m pytest -q
