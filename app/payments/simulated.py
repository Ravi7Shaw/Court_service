from __future__ import annotations
from app.payments.base import PaymentResult

class SimulatedPaymentProvider:
    name = "simulated"
    def capture(self, amount_minor: int, currency: str, idempotency_key: str) -> PaymentResult:
        return PaymentResult(provider_reference=f"SIM-{idempotency_key[:20]}", status="paid")
    def refund(self, provider_reference: str, amount_minor: int, idempotency_key: str) -> PaymentResult:
        return PaymentResult(provider_reference=f"{provider_reference}-REFUND", status="refunded")
