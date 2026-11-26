from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


engine = create_engine(
    "sqlite:///./tembeapay.db",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    payment_columns = {column["name"] for column in inspect(engine).get_columns("payments")}
    with engine.begin() as connection:
        if "invoice_id" not in payment_columns:
            if "lightning_invoice_id" in payment_columns:
                connection.execute(
                    text(
                        "ALTER TABLE payments "
                        "RENAME COLUMN lightning_invoice_id TO invoice_id"
                    )
                )
            else:
                connection.execute(
                    text("ALTER TABLE payments ADD COLUMN invoice_id VARCHAR(128)")
                )
        if "payout_status" not in payment_columns:
            connection.execute(
                text(
                    "ALTER TABLE payments ADD COLUMN payout_status "
                    "VARCHAR(7) NOT NULL DEFAULT 'NONE'"
                )
            )
        if "payout_error" not in payment_columns:
            connection.execute(
                text("ALTER TABLE payments ADD COLUMN payout_error VARCHAR(500)")
            )


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
