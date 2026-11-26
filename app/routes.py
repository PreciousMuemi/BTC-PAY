import hmac
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.db import SessionLocal
from app.models import Payment, PaymentStatus
from app.services.at import normalize_phone
from app.services.lightning import MockLightning, lightning
from app.services.payments import (
    expire_if_due,
    get_payment,
    mark_payout_failed,
    mark_payout_success,
    settle,
)
from app.services.rates import kes_to_sats


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
limiter = Limiter(key_func=get_remote_address)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kes_amount: int = Field(strict=True, ge=50, le=150_000)
    tourist_phone: str


class WebhookRequest(BaseModel):
    payment_id: UUID


@router.post("/checkout")
@limiter.limit("10/minute")
async def checkout(request: Request, body: CheckoutRequest) -> dict[str, object]:
    if not settings.merchant_mpesa_target:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Merchant M-Pesa target is not configured",
        )

    try:
        tourist_phone = normalize_phone(body.tourist_phone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        sats = await run_in_threadpool(kes_to_sats, body.kes_amount)
        invoice_id, bolt11 = await run_in_threadpool(
            lightning.create_invoice,
            sats,
            f"TembeaPay payment KES {body.kes_amount}",
        )
    except Exception as exc:
        logger.exception("Unable to create checkout invoice")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to create Lightning invoice",
        ) from exc

    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.quote_ttl_minutes)
    payment_id = uuid4()
    payment = Payment(
        id=payment_id,
        kes_amount=body.kes_amount,
        sats=sats,
        bolt11=bolt11,
        invoice_id=invoice_id,
        status=PaymentStatus.PENDING,
        tourist_phone=tourist_phone,
        merchant_target=settings.merchant_mpesa_target,
        expires_at=expires_at,
    )
    with SessionLocal() as session:
        session.add(payment)
        session.commit()

    return {
        "payment_id": str(payment_id),
        "sats": sats,
        "bolt11": bolt11,
        "expires_at": expires_at,
    }


@router.get("/payments/{payment_id}")
async def payment_status(payment_id: UUID) -> dict[str, object]:
    payment = get_payment(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    expire_if_due(payment_id)
    payment = get_payment(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    if (
        payment["status"] == PaymentStatus.PENDING.value
        and settings.lightning_provider == "blink"
    ):
        with SessionLocal() as session:
            record = session.get(Payment, payment_id)
            invoice_id = record.invoice_id if record is not None else None
        if invoice_id:
            try:
                is_paid = await run_in_threadpool(lightning.check_paid, invoice_id)
            except Exception:
                logger.exception("Lightning payment poll failed for payment %s", payment_id)
            else:
                if is_paid:
                    payment = await settle(payment_id)
                    if payment is None:
                        raise HTTPException(status_code=404, detail="Payment not found")

    return payment


@router.post("/webhook/lightning-paid")
async def lightning_paid(
    body: WebhookRequest,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> dict[str, object]:
    expected_secret = settings.webhook_secret
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lightning webhook secret is not configured",
        )
    if x_webhook_secret is None or not hmac.compare_digest(
        x_webhook_secret,
        expected_secret,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")

    payment = await settle(body.payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@router.post("/mpesa/result/{token}")
async def b2c_result(token: str, payload: dict[str, object]) -> dict[str, object]:
    callback_token = settings.mpesa_callback_token.get_secret_value()
    if not callback_token or not hmac.compare_digest(token, callback_token):
        raise HTTPException(status_code=404, detail="Not found")

    result = payload.get("Result")
    if not isinstance(result, dict):
        return {"ResultCode": 0, "ResultDesc": "Accepted"}
    originator_id = result.get("OriginatorConversationID")
    try:
        payment_id = UUID(str(originator_id))
    except (ValueError, TypeError, AttributeError):
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    result_code = result.get("ResultCode")
    if str(result_code) == "0":
        transaction_id = result.get("TransactionID")
        if isinstance(transaction_id, str) and transaction_id:
            await mark_payout_success(
                payment_id,
                transaction_id,
                simulated=settings.payout_mode == "daraja_sandbox",
            )
    else:
        description = result.get("ResultDesc")
        if not isinstance(description, str):
            description = "M-Pesa payout failed."
        await mark_payout_failed(payment_id, description)

    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@router.post("/mpesa/timeout/{token}")
async def b2c_timeout(token: str) -> dict[str, object]:
    callback_token = settings.mpesa_callback_token.get_secret_value()
    if not callback_token or not hmac.compare_digest(token, callback_token):
        raise HTTPException(status_code=404, detail="Not found")
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


if settings.lightning_provider == "mock":

    @router.post("/demo/simulate-paid")
    async def simulate_paid(body: WebhookRequest) -> dict[str, object]:
        payment = get_payment(body.payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment["status"] != PaymentStatus.PENDING.value:
            return payment

        expire_if_due(body.payment_id)
        payment = get_payment(body.payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment["status"] != PaymentStatus.PENDING.value:
            return payment

        with SessionLocal() as session:
            record = session.get(Payment, body.payment_id)
            invoice_id = record.invoice_id if record is not None else None
        if not invoice_id or not isinstance(lightning, MockLightning):
            raise HTTPException(status_code=409, detail="Mock invoice is unavailable")
        try:
            lightning.mark_paid(invoice_id)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="Mock invoice is unavailable") from exc

        result = await settle(body.payment_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        return result
