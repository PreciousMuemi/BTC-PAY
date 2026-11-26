from decimal import Decimal

from app.services import rates


def test_kes_to_sats_rounds_up(monkeypatch) -> None:
    monkeypatch.setattr(rates, "get_kes_per_btc", lambda: Decimal("15000000"))

    assert rates.kes_to_sats(1) == 7
    assert rates.kes_to_sats(150) == 1000
