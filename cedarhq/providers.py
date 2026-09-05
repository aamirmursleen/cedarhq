from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .db import utcnow
from .security import random_token


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    service: str
    event_type: str
    external_id: str
    receipt_id: str
    status: str
    payload: dict[str, Any]
    is_simulated: bool = True


class SandboxCheckoutProvider:
    provider = "sandbox_checkout"

    def create_paid_checkout(self, amount_cents: int, currency: str, customer_email: str) -> ProviderResult:
        receipt = f"SBOX-PAY-{random_token(8).upper()}"
        return ProviderResult(
            provider=self.provider,
            service="billing",
            event_type="checkout.paid",
            external_id=f"chk_{random_token(10)}",
            receipt_id=receipt,
            status="sandbox_paid",
            payload={
                "amount_cents": amount_cents,
                "currency": currency,
                "customer_email": customer_email,
                "notice": "Sandbox checkout only. No card was charged.",
                "created_at": utcnow(),
            },
        )


class SandboxFormationProvider:
    provider = "sandbox_formation"

    def receipt(self, service: str, event_type: str, order_id: str, payload: dict[str, Any] | None = None) -> ProviderResult:
        receipt = f"SBOX-{service.upper()}-{random_token(8).upper()}"
        return ProviderResult(
            provider=self.provider,
            service=service,
            event_type=event_type,
            external_id=f"{service}_{random_token(10)}",
            receipt_id=receipt,
            status="recorded",
            payload={
                "order_id": order_id,
                "notice": "Sandbox provider event. This is not a government filing.",
                **(payload or {}),
                "created_at": utcnow(),
            },
        )


class LocalOutboxEmailProvider:
    provider = "local_outbox"

    def send(self, to_email: str, subject: str, body: str) -> dict[str, str]:
        return {
            "provider": self.provider,
            "external_id": f"email_{random_token(10)}",
            "to_email": to_email,
            "subject": subject,
            "body": body,
            "created_at": utcnow(),
        }


def serialize_provider_payload(result: ProviderResult) -> str:
    return json.dumps(result.payload, sort_keys=True)

