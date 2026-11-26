from app.services.lightning import MockLightning


def test_mock_invoice_stays_pending_until_marked_paid() -> None:
    lightning = MockLightning()

    invoice_id, bolt11 = lightning.create_invoice(1200, "Tourist payment")

    assert bolt11.startswith("lnbc")
    assert lightning.check_paid(invoice_id) is False

    lightning.mark_paid(invoice_id)

    assert lightning.check_paid(invoice_id) is True
