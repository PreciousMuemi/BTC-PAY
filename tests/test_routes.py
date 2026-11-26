import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.main import app
from app.models import Payment, PaymentStatus
from app.services import payments
from app.services.lightning import MockLightning


@pytest.fixture
def api(monkeypatch):
    app.state.limiter.reset()
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(database)
    test_sessions = sessionmaker(bind=database, autoflush=False, autocommit=False)
    monkeypatch.setattr("app.routes.SessionLocal", test_sessions)
    monkeypatch.setattr("app.services.payments.SessionLocal", test_sessions)
    monkeypatch.setattr(settings, "mock_lightning", True)
    monkeypatch.setattr(settings, "merchant_mpesa_target", "254700000000")
    monkeypatch.setattr(settings, "merchant_phone", "254700000001")
    monkeypatch.setattr(settings, "webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "quote_ttl_minutes", 15)
    monkeypatch.setattr(settings, "airtime_reward_kes", 20)
    mock_lightning = MockLightning()
    monkeypatch.setattr("app.routes.lightning", mock_lightning)
    monkeypatch.setattr("app.routes.kes_to_sats", lambda _: 1000)
    monkeypatch.setattr(payments, "send_sms", _async_true)
    monkeypatch.setattr(payments, "send_airtime", _async_true)
    with TestClient(app) as client:
        yield client, test_sessions, mock_lightning
    database.dispose()


async def _async_true(*args, **kwargs) -> bool:
    return True


def _create_payment(session_factory, tourist_phone: str, expires_at: datetime):
    invoice_id = str(uuid4())
    bolt11 = f"lnbc1000n{uuid4().hex}"
    payment = Payment(
        kes_amount=100,
        sats=1000,
        bolt11=bolt11,
        lightning_invoice_id=invoice_id,
        tourist_phone=tourist_phone,
        merchant_target="254700000000",
        expires_at=expires_at,
    )
    with session_factory() as session:
        session.add(payment)
        session.commit()
        return payment.id, invoice_id


def test_checkout_and_demo_settlement_are_idempotent(api) -> None:
    client, _, mock_lightning = api
    response = client.post(
        "/api/checkout",
        json={"kes_amount": 500, "tourist_phone": "0712345678"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sats"] == 1000

    settled = client.post(
        "/api/demo/simulate-paid",
        json={"payment_id": payload["payment_id"]},
    )
    again = client.post(
        "/api/demo/simulate-paid",
        json={"payment_id": payload["payment_id"]},
    )

    assert settled.status_code == 200
    assert settled.json()["status"] == PaymentStatus.SETTLED.value
    assert settled.json()["mpesa_ref"].startswith("SIM")
    assert settled.json()["sms_sent"] is True
    assert settled.json()["merchant_sms_sent"] is True
    assert settled.json()["airtime_sent"] is True
    assert again.json()["mpesa_ref"] == settled.json()["mpesa_ref"]
    with api[1]() as session:
        payment = session.get(Payment, UUID(payload["payment_id"]))
        assert payment is not None
        assert payment.merchant_target == "254700000000"
        assert mock_lightning.check_paid(payment.lightning_invoice_id)


def test_webhook_secret_is_required_and_valid_webhook_settles(api) -> None:
    client, session_factory, _ = api
    payment_id, _ = _create_payment(
        session_factory,
        "+254712345678",
        datetime.now(UTC) + timedelta(minutes=5),
    )

    denied = client.post(
        "/api/webhook/lightning-paid",
        json={"payment_id": str(payment_id)},
        headers={"X-Webhook-Secret": "wrong"},
    )
    settled = client.post(
        "/api/webhook/lightning-paid",
        json={"payment_id": str(payment_id)},
        headers={"X-Webhook-Secret": "test-secret"},
    )

    assert denied.status_code == 401
    assert settled.status_code == 200
    assert settled.json()["status"] == PaymentStatus.SETTLED.value


def test_expired_payment_is_not_settled(api) -> None:
    client, session_factory, _ = api
    payment_id, _ = _create_payment(
        session_factory,
        "+254712345678",
        datetime.now(UTC) - timedelta(seconds=1),
    )

    response = client.get(f"/api/payments/{payment_id}")

    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.EXPIRED.value


def test_live_lightning_poll_settles_paid_payment(api, monkeypatch) -> None:
    client, session_factory, _ = api
    payment_id, _ = _create_payment(
        session_factory,
        "+254712345678",
        datetime.now(UTC) + timedelta(minutes=5),
    )

    class PaidLightning:
        def check_paid(self, invoice_id: str) -> bool:
            return True

    monkeypatch.setattr(settings, "mock_lightning", False)
    monkeypatch.setattr("app.routes.lightning", PaidLightning())

    response = client.get(f"/api/payments/{payment_id}")

    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.SETTLED.value


def test_airtime_reward_is_capped_per_phone_per_24_hours(api, monkeypatch) -> None:
    client, session_factory, _ = api
    sends: list[tuple[str, int]] = []

    async def record_airtime(phone: str, kes: int) -> bool:
        sends.append((phone, kes))
        return True

    monkeypatch.setattr(payments, "send_airtime", record_airtime)
    payment_ids = [
        _create_payment(
            session_factory,
            "+254712345678",
            datetime.now(UTC) + timedelta(minutes=5),
        )[0]
        for _ in range(2)
    ]

    for payment_id in payment_ids:
        response = client.post(
            "/api/webhook/lightning-paid",
            json={"payment_id": str(payment_id)},
            headers={"X-Webhook-Secret": "test-secret"},
        )
        assert response.status_code == 200

    assert sends == [("+254712345678", 20)]


@pytest.mark.parametrize("kes_amount", [49, 150_001])
def test_checkout_rejects_out_of_range_amount(api, kes_amount: int) -> None:
    client, _, _ = api

    response = client.post(
        "/api/checkout",
        json={"kes_amount": kes_amount, "tourist_phone": "0712345678"},
    )

    assert response.status_code == 422


def test_checkout_is_rate_limited(api) -> None:
    client, _, _ = api
    body = {"kes_amount": 500, "tourist_phone": "0712345678"}

    responses = [client.post("/api/checkout", json=body) for _ in range(11)]

    assert all(response.status_code == 200 for response in responses[:10])
    assert responses[10].status_code == 429


def test_concurrent_settlement_fulfills_once(api, monkeypatch, tmp_path) -> None:
    database = create_engine(
        URL.create("sqlite", database=str(tmp_path / "concurrent.db")),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    concurrent_sessions = sessionmaker(bind=database, autoflush=False, autocommit=False)
    monkeypatch.setattr("app.routes.SessionLocal", concurrent_sessions)
    monkeypatch.setattr(payments, "SessionLocal", concurrent_sessions)
    payment_id, _ = _create_payment(
        concurrent_sessions,
        "+254712345678",
        datetime.now(UTC) + timedelta(minutes=5),
    )
    sends = {"sms": 0, "airtime": 0}

    async def record_sms(*args, **kwargs) -> bool:
        sends["sms"] += 1
        return True

    async def record_airtime(*args, **kwargs) -> bool:
        sends["airtime"] += 1
        return True

    monkeypatch.setattr(payments, "send_sms", record_sms)
    monkeypatch.setattr(payments, "send_airtime", record_airtime)

    def settle_concurrently():
        return asyncio.run(payments.settle(payment_id))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: settle_concurrently(), range(2)))

        assert all(result["status"] == PaymentStatus.SETTLED.value for result in results)
        assert sends == {"sms": 2, "airtime": 1}
    finally:
        database.dispose()
