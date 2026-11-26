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
    if "lightning_invoice_id" not in payment_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE payments ADD COLUMN lightning_invoice_id VARCHAR(128)")
            )


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
