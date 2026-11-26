import asyncio

import pytest

from app.services import at


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("0712345678", "+254712345678"),
        ("712345678", "+254712345678"),
        ("254712345678", "+254712345678"),
        ("+254712345678", "+254712345678"),
    ],
)
def test_normalize_phone(phone: str, expected: str) -> None:
    assert at.normalize_phone(phone) == expected


def test_normalize_phone_rejects_invalid_number() -> None:
    with pytest.raises(ValueError):
        at.normalize_phone("0112345678")


def test_sms_limit_counts_gsm_extension_characters_as_two_septets() -> None:
    euro = "\u20ac"

    message = at._fit_gsm_sms(euro * 80, mpesa_simulated=False)

    assert message == euro * 78 + "..."


def test_send_sms_adds_sandbox_and_limits_body(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class SmsClient:
        def send(self, message: str, recipients: list[str]) -> dict[str, object]:
            captured["message"] = message
            captured["recipients"] = recipients
            return {
                "SMSMessageData": {
                    "Recipients": [{"status": "Success"}],
                },
            }

    async def run_in_threadpool(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(at, "_at_clients", lambda *_: (SmsClient(), None))
    monkeypatch.setattr(at, "run_in_threadpool", run_in_threadpool)

    assert asyncio.run(at.send_sms("0712345678", "Payment received " + "x" * 200))
    message = captured["message"]
    assert isinstance(message, str)
    assert message.startswith("SANDBOX: ")
    assert len(message) < 160
    assert captured["recipients"] == ["+254712345678"]


def test_at_failure_returns_false(monkeypatch) -> None:
    async def run_in_threadpool(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(at, "_at_clients", lambda *_: (None, object()))
    monkeypatch.setattr(at, "run_in_threadpool", run_in_threadpool)

    assert asyncio.run(at.send_airtime("0712345678", 20)) is False
