import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services import lightning as lightning_module
from app.services.lightning import BlinkLightning


def _mock_post(monkeypatch, responses, requests=None):
    remaining = iter(responses)

    def post(url, **kwargs):
        if requests is not None:
            requests.append((url, kwargs))
        return httpx.Response(
            200,
            json=next(remaining),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(lightning_module.httpx, "post", post)


def test_create_invoice_parses_blink_invoice_fields(monkeypatch) -> None:
    requests = []
    _mock_post(
        monkeypatch,
        [
            {
                "data": {
                    "lnInvoiceCreate": {
                        "invoice": {
                            "paymentHash": "payment-hash",
                            "paymentRequest": "lnbc-invoice",
                        },
                        "errors": [],
                    },
                },
            },
        ],
        requests,
    )
    blink = BlinkLightning("https://blink.test/graphql", "test-api-key", "btc-wallet")

    assert blink.create_invoice(1200, "Tourist payment") == (
        "payment-hash",
        "lnbc-invoice",
    )
    url, request = requests[0]
    assert url == "https://blink.test/graphql"
    assert request["headers"]["X-API-KEY"] == "test-api-key"
    assert request["json"]["variables"]["input"] == {
        "walletId": "btc-wallet",
        "amount": 1200,
        "memo": "Tourist payment",
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [("PAID", True), ("PENDING", False), ("EXPIRED", False)],
)
def test_check_paid_maps_blink_status(monkeypatch, status: str, expected: bool) -> None:
    _mock_post(
        monkeypatch,
        [
            {
                "data": {
                    "lnInvoicePaymentStatusByHash": {
                        "paymentHash": "payment-hash",
                        "status": status,
                    },
                },
            },
        ],
    )
    blink = BlinkLightning("https://blink.test/graphql", "test-api-key", "btc-wallet")

    assert blink.check_paid("payment-hash") is expected


def test_btc_wallet_is_auto_detected_and_cached(monkeypatch) -> None:
    requests = []
    _mock_post(
        monkeypatch,
        [
            {
                "data": {
                    "me": {
                        "defaultAccount": {
                            "wallets": [
                                {"id": "usd-wallet", "walletCurrency": "USD"},
                                {"id": "btc-wallet", "walletCurrency": "BTC"},
                            ],
                        },
                    },
                },
            },
            {
                "data": {
                    "lnInvoiceCreate": {
                        "invoice": {
                            "paymentHash": "payment-hash",
                            "paymentRequest": "lnbc-invoice",
                        },
                        "errors": [],
                    },
                },
            },
            {
                "data": {
                    "lnInvoiceCreate": {
                        "invoice": {
                            "paymentHash": "second-hash",
                            "paymentRequest": "lnbc-second-invoice",
                        },
                        "errors": [],
                    },
                },
            },
        ],
        requests,
    )
    blink = BlinkLightning("https://blink.test/graphql", "test-api-key")

    blink.create_invoice(1000, "first")
    blink.create_invoice(2000, "second")

    assert len(requests) == 3
    assert requests[0][1]["json"]["query"].find("walletCurrency") >= 0
    assert requests[1][1]["json"]["variables"]["input"]["walletId"] == "btc-wallet"
    assert requests[2][1]["json"]["variables"]["input"]["walletId"] == "btc-wallet"


def test_graphql_top_level_errors_raise_without_echoing_key(monkeypatch) -> None:
    _mock_post(monkeypatch, [{"errors": [{"message": "request rejected"}]}])
    blink = BlinkLightning("https://blink.test/graphql", "sensitive-api-key", "btc-wallet")

    with pytest.raises(ValueError, match="Blink GraphQL request failed") as error:
        blink.check_paid("payment-hash")

    assert "sensitive-api-key" not in str(error.value)


def test_invoice_mutation_errors_raise(monkeypatch) -> None:
    _mock_post(
        monkeypatch,
        [
            {
                "data": {
                    "lnInvoiceCreate": {
                        "invoice": None,
                        "errors": [{"message": "invoice denied"}],
                    },
                },
            },
        ],
    )
    blink = BlinkLightning("https://blink.test/graphql", "test-api-key", "btc-wallet")

    with pytest.raises(ValueError, match="Blink invoice creation failed"):
        blink.create_invoice(1000, "payment")


def test_blink_provider_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="BLINK_API_KEY is required"):
        Settings(
            _env_file=None,
            kes_per_btc_fallback=15_000_000,
            lightning_provider="blink",
            blink_api_key="",
        )
