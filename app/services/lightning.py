from typing import Protocol
from uuid import uuid4

import httpx

from app.config import settings


class LightningBackend(Protocol):
    def create_invoice(self, sats: int, memo: str) -> tuple[str, str]:
        """Create an invoice and return its provider ID and BOLT11 string."""
        ...

    def check_paid(self, invoice_id: str) -> bool:
        """Return whether the invoice has been paid."""
        ...


class MockLightning:
    def __init__(self) -> None:
        self._payments: dict[str, bool] = {}

    def create_invoice(self, sats: int, memo: str) -> tuple[str, str]:
        if sats <= 0:
            raise ValueError("Invoice amount must be positive")
        invoice_id = str(uuid4())
        bolt11 = f"lnbc{sats}n1{uuid4().hex}"
        self._payments[invoice_id] = False
        return invoice_id, bolt11

    def check_paid(self, invoice_id: str) -> bool:
        return self._payments.get(invoice_id, False)

    def mark_paid(self, invoice_id: str) -> None:
        if invoice_id not in self._payments:
            raise KeyError(f"Unknown mock invoice: {invoice_id}")
        self._payments[invoice_id] = True


class LNbitsLightning:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise ValueError("LNBITS_API_KEY must be configured for LNbits")
        return {"X-Api-Key": self._api_key}

    def create_invoice(self, sats: int, memo: str) -> tuple[str, str]:
        if sats <= 0:
            raise ValueError("Invoice amount must be positive")
        with httpx.Client(timeout=5.0) as client:
            response = client.post(
                f"{self._base_url}/api/v1/payments",
                headers=self._headers(),
                json={"out": False, "amount": sats, "memo": memo},
            )
            response.raise_for_status()
            payload = response.json()

        try:
            invoice_id = payload["payment_hash"]
            bolt11 = payload["payment_request"]
        except (KeyError, TypeError) as exc:
            raise ValueError("LNbits returned an invalid invoice response") from exc
        if not isinstance(invoice_id, str) or not isinstance(bolt11, str):
            raise ValueError("LNbits returned an invalid invoice response")
        return invoice_id, bolt11

    def check_paid(self, invoice_id: str) -> bool:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{self._base_url}/api/v1/payments/{invoice_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("paid"), bool):
            raise ValueError("LNbits returned an invalid payment status response")
        return payload["paid"]


lightning: LightningBackend = (
    MockLightning()
    if settings.mock_lightning
    else LNbitsLightning(settings.lnbits_url, settings.lnbits_api_key)
)
