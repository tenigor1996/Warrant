"""Runtime configuration for the checkout service.

The running service reloads these values when this file changes.
"""

# --- Payment gateway ---------------------------------------------------------
PAYMENT_GATEWAY_URL = "https://payments.internal.example/v1"

# Seconds to wait for a single authorization response from the gateway.
PAYMENT_TIMEOUT = 5.0

# Additional attempts after the first one fails with a timeout or a 5xx.
PAYMENT_RETRIES = 1

# Base delay in seconds before a retry; doubles on each subsequent retry.
PAYMENT_RETRY_BACKOFF = 0.1

# --- Checkout ----------------------------------------------------------------
CHECKOUT_SLO_SECONDS = 2.0
MAX_CART_ITEMS = 50
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP")

LOG_LEVEL = "INFO"
