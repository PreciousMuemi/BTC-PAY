from typing import Protocol
from uuid import uuid4

import httpx

from app.config import settings


class LightningBackend(Protocol):
    def create_invoice(self, sats: int, memo: str) -> tuple[str, str]:
        """Create an invoice and return its payment hash and BOLT11 string."""
        ...

    def check_paid(self, payment_hash: str) -> bool:
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


class BlinkLightning:
    _WALLET_QUERY = """
        query Me {
          me {
            defaultAccount {
              wallets {
                id
                walletCurrency
              }
            }
          }
        }
    """
    _CREATE_INVOICE_MUTATION = """
        mutation LnInvoiceCreate($input: LnInvoiceCreateInput!) {
          lnInvoiceCreate(input: $input) {
            invoice {
              paymentRequest
              paymentHash
            }
            errors {
              message
            }
          }
        }
    """
    _PAYMENT_STATUS_QUERY = """
        query LnInvoicePaymentStatusByHash(
          $input: LnInvoicePaymentStatusByHashInput!
        ) {
          lnInvoicePaymentStatusByHash(input: $input) {
            paymentHash
            status
          }
        }
    """

    def __init__(self, api_url: str, api_key: str, wallet_id: str | None = None) -> None:
        self._api_url = api_url
        self._api_key = api_key
        self._wallet_id = wallet_id or None

    def _execute(self, query: str, variables: dict[str, object] | None = None) -> dict:
        response = httpx.post(
            self._api_url,
            headers={"X-API-KEY": self._api_key},
            json={"query": query, "variables": variables or {}},
            timeout=10.0,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("Blink returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Blink returned an invalid GraphQL response")
        if payload.get("errors"):
            raise ValueError("Blink GraphQL request failed")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("Blink returned an invalid GraphQL response")
        return data

    def _resolve_wallet_id(self) -> str:
        if self._wallet_id:
            return self._wallet_id

        data = self._execute(self._WALLET_QUERY)
        try:
            wallets = data["me"]["defaultAccount"]["wallets"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Blink did not return account wallets") from exc
        if not isinstance(wallets, list):
            raise ValueError("Blink returned an invalid wallet list")
        for wallet in wallets:
            if (
                isinstance(wallet, dict)
                and wallet.get("walletCurrency") == "BTC"
                and isinstance(wallet.get("id"), str)
                and wallet["id"]
            ):
                self._wallet_id = wallet["id"]
                return self._wallet_id
        raise ValueError("No BTC wallet is available in the Blink account")

    def create_invoice(self, sats: int, memo: str) -> tuple[str, str]:
        if sats <= 0:
            raise ValueError("Invoice amount must be positive")
        data = self._execute(
            self._CREATE_INVOICE_MUTATION,
            {
                "input": {
                    "walletId": self._resolve_wallet_id(),
                    "amount": sats,
                    "memo": memo,
                },
            },
        )
        result = data.get("lnInvoiceCreate")
        if not isinstance(result, dict):
            raise ValueError("Blink returned an invalid invoice response")
        if result.get("errors"):
            raise ValueError("Blink invoice creation failed")
        invoice = result.get("invoice")
        if not isinstance(invoice, dict):
            raise ValueError("Blink returned an invalid invoice response")
        payment_hash = invoice.get("paymentHash")
        payment_request = invoice.get("paymentRequest")
        if not isinstance(payment_hash, str) or not payment_hash:
            raise ValueError("Blink returned an invalid invoice response")
        if not isinstance(payment_request, str) or not payment_request:
            raise ValueError("Blink returned an invalid invoice response")
        return payment_hash, payment_request

    def check_paid(self, payment_hash: str) -> bool:
        data = self._execute(
            self._PAYMENT_STATUS_QUERY,
            {"input": {"paymentHash": payment_hash}},
        )
        result = data.get("lnInvoicePaymentStatusByHash")
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            raise ValueError("Blink returned an invalid payment status response")
        return result["status"] == "PAID"


lightning: LightningBackend = (
    MockLightning()
    if settings.lightning_provider == "mock"
    else BlinkLightning(
        settings.blink_api_url,
        settings.blink_api_key.get_secret_value(),
        settings.blink_wallet_id,
    )
)
