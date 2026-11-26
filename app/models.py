import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SqlEnum, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    EXPIRED = "EXPIRED"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kes_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    sats: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bolt11: Mapped[str | None] = mapped_column(String, nullable=True)
    lightning_invoice_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[PaymentStatus] = mapped_column(
        SqlEnum(PaymentStatus, name="payment_status", native_enum=False),
        nullable=False,
        default=PaymentStatus.PENDING,
    )
    tourist_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    merchant_target: Mapped[str] = mapped_column(String(64), nullable=False)
    mpesa_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sms_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    merchant_sms_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    airtime_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AirtimeReward(Base):
    __tablename__ = "airtime_rewards"

    tourist_phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_reward_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
