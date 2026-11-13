from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class PaymentResult:
    provider_reference: str
    status: str

class PaymentProvider(Protocol):
    name: str
    def capture(self, amount_minor: int, currency: str, idempotency_key: str) -> PaymentResult: ...
    def refund(self, provider_reference: str, amount_minor: int, idempotency_key: str) -> PaymentResult: ...
