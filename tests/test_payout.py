import httpx
from pydantic import SecretStr

from app.config import settings
from app.services.payout import DarajaB2C


def test_daraja_b2c_uses_payment_id_and_returns_ack(monkeypatch) -> None:
    calls = []

    def get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        return httpx.Response(
            200,
            json={"access_token": "access-token"},
            request=httpx.Request("GET", url),
        )

    def post(url, **kwargs):
        calls.append(("POST", url, kwargs))
        return httpx.Response(
            200,
            json={"ResponseCode": "0", "ConversationID": "conversation-id"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.payout.httpx.get", get)
    monkeypatch.setattr("app.services.payout.httpx.post", post)
    monkeypatch.setattr(settings, "daraja_env", "sandbox")
    monkeypatch.setattr(settings, "daraja_consumer_key", "consumer-key")
    monkeypatch.setattr(settings, "daraja_consumer_secret", SecretStr("consumer-secret"))
    monkeypatch.setattr(settings, "daraja_initiator_name", "test-initiator")
    monkeypatch.setattr(settings, "daraja_security_credential", SecretStr("credential"))
    monkeypatch.setattr(settings, "daraja_shortcode", "600000")
    monkeypatch.setattr(settings, "public_base_url", "https://tembeapay.test/")
    monkeypatch.setattr(settings, "mpesa_callback_token", SecretStr("callback-secret"))

    response = DarajaB2C().send_kes("+254712345678", 500, "payment-123")

    assert response["ResponseCode"] == "0"
    assert calls[0][1] == (
        "https://sandbox.safaricom.co.ke/oauth/v1/generate"
    )
    assert calls[1][1] == (
        "https://sandbox.safaricom.co.ke/mpesa/b2c/v3/paymentrequest"
    )
    assert calls[1][2]["headers"]["Authorization"] == "Bearer access-token"
    body = calls[1][2]["json"]
    assert body["OriginatorConversationID"] == "payment-123"
    assert body["PartyA"] == "600000"
    assert body["PartyB"] == "254712345678"
    assert body["QueueTimeOutURL"] == (
        "https://tembeapay.test/api/mpesa/timeout/callback-secret"
    )
    assert body["ResultURL"] == (
        "https://tembeapay.test/api/mpesa/result/callback-secret"
    )
