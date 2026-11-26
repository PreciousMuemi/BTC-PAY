from sqlalchemy import create_engine, inspect, text

from app import db


def test_init_db_migrates_legacy_lightning_invoice_column(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE payments ("
                "id CHAR(32) PRIMARY KEY, "
                "lightning_invoice_id VARCHAR(128)"
                ")"
            )
        )
    monkeypatch.setattr(db, "engine", engine)

    db.init_db()

    columns = {column["name"] for column in inspect(engine).get_columns("payments")}
    assert "invoice_id" in columns
    assert "lightning_invoice_id" not in columns
    assert "payout_status" in columns
    assert "payout_error" in columns
    engine.dispose()
