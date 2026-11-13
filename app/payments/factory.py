from __future__ import annotations

from app.errors import ApiError
from app.payments.base import PaymentProvider
from app.payments.simulated import SimulatedPaymentProvider


def build_payment_provider(name: str) -> PaymentProvider:
    """Resolve the configured adapter; add real gateway adapters here, never in routes."""
    if name == "simulated":
        return SimulatedPaymentProvider()
    raise ApiError("payment_provider_unavailable", 503, f"payment provider '{name}' is not configured")
