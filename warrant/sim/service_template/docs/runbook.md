# Checkout service runbook

## Symptoms that page

- Checkout failure rate above 10% for 5 minutes
- Checkout p95 latency above 1000 ms for 5 minutes

## First checks

1. Service health and recent metrics.
2. Application logs for payment errors.
3. Recent deploys and changes to the repository.

## Escalation

- Primary: #checkout-oncall
- Payments gateway owner: #payments-platform
