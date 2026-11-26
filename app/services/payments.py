import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db import SessionLocal
from app.models import AirtimeReward, Payment, PaymentStatus
from app.services.at import send_airtime, send_sms


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _serialize_payment(payment: Payment) -> dict[str, object]:
    return {
        "id": str(payment.id),
        "kes_amount": payment.kes_amount,
        "sats": payment.sats,
        "bolt11": payment.bolt11,
        "status": payment.status.value,
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


async def _deliver_settlement(
    payment_id: UUID,
    tourist_phone: str | None,
    kes_amount: int,
    mpesa_ref: str,
    reward_claimed: bool,
) -> None:
    tourist_sent = False
    merchant_sent = False
    airtime_sent = False

    if tourist_phone:
        try:
            tourist_sent = await send_sms(
                tourist_phone,
                f"TembeaPay: KES {kes_amount} paid to merchant. Ref {mpesa_ref}.",
                mpesa_simulated=True,
            )
        except Exception:
            logger.exception("Tourist receipt delivery failed for payment %s", payment_id)

    if settings.merchant_phone:
        try:
            merchant_sent = await send_sms(
                settings.merchant_phone,
                f"TembeaPay: Received KES {kes_amount}. Ref {mpesa_ref}.",
                mpesa_simulated=True,
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
            .where(Payment.id == payment_id, Payment.status == PaymentStatus.SETTLED)
            .values(
                sms_sent=tourist_sent,
                merchant_sms_sent=merchant_sent,
                airtime_sent=airtime_sent,
            )
        )
        session.commit()


async def settle(payment_id: UUID) -> dict[str, object] | None:
    now = _utc_now()
    mpesa_ref = f"SIM{secrets.token_hex(4).upper()}"

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
                settled_at=now,
                mpesa_ref=mpesa_ref,
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

        tourist_phone = payment.tourist_phone
        kes_amount = payment.kes_amount
        reward_claimed = (
            _claim_airtime_reward(session, tourist_phone, now)
            if tourist_phone
            else False
        )
        session.commit()

    await _deliver_settlement(
        payment_id,
        tourist_phone,
        kes_amount,
        mpesa_ref,
        reward_claimed,
    )
    return get_payment(payment_id)
