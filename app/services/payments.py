import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db import SessionLocal
from app.models import AirtimeReward, Payment, PaymentStatus, PayoutStatus
from app.services.at import send_airtime, send_sms
from app.services.payout import DarajaB2C


logger = logging.getLogger(__name__)
daraja_b2c = DarajaB2C()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _serialize_payment(payment: Payment) -> dict[str, object]:
    return {
        "id": str(payment.id),
        "kes_amount": payment.kes_amount,
        "sats": payment.sats,
        "bolt11": payment.bolt11,
        "invoice_id": payment.invoice_id,
        "status": payment.status.value,
        "payout_status": payment.payout_status.value,
        "payout_error": payment.payout_error,
        "tourist_phone": payment.tourist_phone,
        "merchant_target": payment.merchant_target,
        "mpesa_ref": payment.mpesa_ref,
        "sms_sent": payment.sms_sent,
        "merchant_sms_sent": payment.merchant_sms_sent,
        "airtime_sent": payment.airtime_sent,
        "created_at": payment.created_at,
        "expires_at": payment.expires_at,
        "settled_at": payment.settled_at,
    }


def get_payment(payment_id: UUID) -> dict[str, object] | None:
    with SessionLocal() as session:
        payment = session.get(Payment, payment_id)
        return _serialize_payment(payment) if payment is not None else None


def expire_if_due(payment_id: UUID) -> None:
    now = _utc_now()
    with SessionLocal() as session:
        session.execute(
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status == PaymentStatus.PENDING,
                Payment.expires_at <= now,
            )
            .values(status=PaymentStatus.EXPIRED)
        )
        session.commit()


def _claim_airtime_reward(session, tourist_phone: str, now: datetime) -> bool:
    cutoff = now - timedelta(hours=24)
    statement = (
        sqlite_insert(AirtimeReward)
        .values(tourist_phone=tourist_phone, last_reward_at=now)
        .on_conflict_do_update(
            index_elements=[AirtimeReward.tourist_phone],
            set_={"last_reward_at": now},
            where=AirtimeReward.last_reward_at <= cutoff,
        )
    )
    return session.execute(statement).rowcount == 1


async def _deliver_successful_payout(
    payment_id: UUID,
    tourist_phone: str | None,
    kes_amount: int,
    mpesa_ref: str,
    reward_claimed: bool,
    simulated: bool,
) -> None:
    tourist_sent = False
    merchant_sent = False
    airtime_sent = False

    if tourist_phone:
        try:
            tourist_sent = await send_sms(
                tourist_phone,
                f"TembeaPay: KES {kes_amount} paid to merchant. Ref {mpesa_ref}.",
                mpesa_simulated=simulated,
            )
        except Exception:
            logger.exception("Tourist receipt delivery failed for payment %s", payment_id)

    if settings.merchant_phone:
        try:
            merchant_sent = await send_sms(
                settings.merchant_phone,
                f"TembeaPay: Received KES {kes_amount}. Ref {mpesa_ref}.",
                mpesa_simulated=simulated,
            )
        except Exception:
            logger.exception("Merchant receipt delivery failed for payment %s", payment_id)

    if tourist_phone and reward_claimed:
        try:
            airtime_sent = await send_airtime(
                tourist_phone,
                settings.airtime_reward_kes,
            )
        except Exception:
            logger.exception("Airtime delivery failed for payment %s", payment_id)

    with SessionLocal() as session:
        session.execute(
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status == PaymentStatus.SETTLED,
                Payment.payout_status == PayoutStatus.SUCCESS,
            )
            .values(
                sms_sent=tourist_sent,
                merchant_sms_sent=merchant_sent,
                airtime_sent=airtime_sent,
            )
        )
        session.commit()


async def mark_payout_success(
    payment_id: UUID,
    mpesa_ref: str,
    *,
    simulated: bool = False,
) -> bool:
    now = _utc_now()
    with SessionLocal() as session:
        result = session.execute(
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status == PaymentStatus.SETTLED,
                Payment.payout_status == PayoutStatus.PENDING,
            )
            .values(
                payout_status=PayoutStatus.SUCCESS,
                payout_error=None,
                mpesa_ref=mpesa_ref,
            )
        )
        if result.rowcount != 1:
            session.commit()
            return False

        payment = session.get(Payment, payment_id)
        if payment is None:
            session.rollback()
            return False
        tourist_phone = payment.tourist_phone
        kes_amount = payment.kes_amount
        reward_claimed = (
            _claim_airtime_reward(session, tourist_phone, now)
            if tourist_phone
            else False
        )
        session.commit()

    await _deliver_successful_payout(
        payment_id,
        tourist_phone,
        kes_amount,
        mpesa_ref,
        reward_claimed,
        simulated,
    )
    return True


async def mark_payout_failed(payment_id: UUID, description: str) -> bool:
    safe_description = description.strip()[:500] or "M-Pesa payout failed."
    with SessionLocal() as session:
        result = session.execute(
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status == PaymentStatus.SETTLED,
                Payment.payout_status == PayoutStatus.PENDING,
            )
            .values(
                payout_status=PayoutStatus.FAILED,
                payout_error=safe_description,
            )
        )
        if result.rowcount != 1:
            session.commit()
            return False
        payment = session.get(Payment, payment_id)
        tourist_phone = payment.tourist_phone if payment is not None else None
        session.commit()

    if tourist_phone:
        try:
            await send_sms(
                tourist_phone,
                "TembeaPay: Your Bitcoin payment was received, but M-Pesa payout failed. "
                "Please contact the merchant.",
                mpesa_simulated=False,
            )
        except Exception:
            logger.exception("Payout failure notice failed for payment %s", payment_id)
    return True


async def settle(payment_id: UUID) -> dict[str, object] | None:
    now = _utc_now()

    with SessionLocal() as session:
        result = session.execute(
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status == PaymentStatus.PENDING,
                Payment.expires_at > now,
            )
            .values(
                status=PaymentStatus.SETTLED,
                payout_status=PayoutStatus.PENDING,
                settled_at=now,
                payout_error=None,
            )
        )
        if result.rowcount != 1:
            session.execute(
                update(Payment)
                .where(
                    Payment.id == payment_id,
                    Payment.status == PaymentStatus.PENDING,
                    Payment.expires_at <= now,
                )
                .values(status=PaymentStatus.EXPIRED)
            )
            session.commit()
            payment = session.get(Payment, payment_id)
            return _serialize_payment(payment) if payment is not None else None

        payment = session.get(Payment, payment_id)
        if payment is None:
            session.rollback()
            return None
        merchant_target = payment.merchant_target
        kes_amount = payment.kes_amount
        session.commit()

    if settings.payout_mode == "simulated":
        await mark_payout_success(
            payment_id,
            f"SIM{secrets.token_hex(4).upper()}",
            simulated=True,
        )
    else:
        try:
            await run_in_threadpool(
                daraja_b2c.send_kes,
                merchant_target,
                kes_amount,
                str(payment_id),
            )
        except Exception:
            logger.exception("Daraja B2C request failed for payment %s", payment_id)
            await mark_payout_failed(
                payment_id,
                "M-Pesa payout could not be initiated. Please contact the merchant.",
            )

    return get_payment(payment_id)
