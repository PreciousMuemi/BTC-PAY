import logging
import threading
import time
from decimal import Decimal, InvalidOperation

import httpx

from app.config import settings


logger = logging.getLogger(__name__)
_CACHE_TTL_SECONDS = 60
_SATOSHIS_PER_BTC = 100_000_000
_cached_kes_per_btc: Decimal | None = None
_cache_expires_at = 0.0
_cache_lock = threading.Lock()


def _positive_decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Rate API returned a non-numeric exchange rate") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("Rate API returned a non-positive exchange rate")
    return result


def get_kes_per_btc() -> Decimal:
    """Return a cached KES/BTC rate, falling back to configuration on API failure."""
    global _cache_expires_at, _cached_kes_per_btc

    with _cache_lock:
        now = time.monotonic()
        if _cached_kes_per_btc is not None and now < _cache_expires_at:
            return _cached_kes_per_btc

        try:
            btc_response = httpx.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=5.0,
            )
            btc_response.raise_for_status()
            btc_usd = _positive_decimal(btc_response.json()["bitcoin"]["usd"])

            fx_response = httpx.get(
                "https://open.er-api.com/v6/latest/USD",
                timeout=5.0,
            )
            fx_response.raise_for_status()
            usd_kes = _positive_decimal(fx_response.json()["rates"]["KES"])

            rate = btc_usd * usd_kes
            _cached_kes_per_btc = rate
            _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
            return rate
        except Exception:
            logger.warning(
                "Unable to fetch BTC/USD and USD/KES rates; using configured fallback",
                exc_info=True,
            )
            return _positive_decimal(settings.kes_per_btc_fallback)


def kes_to_sats(kes: int) -> int:
    """Convert whole KES into satoshis, rounding up to avoid underpayment."""
    if kes < 0:
        raise ValueError("KES amount cannot be negative")
    rate_numerator, rate_denominator = get_kes_per_btc().as_integer_ratio()
    satoshi_numerator = kes * _SATOSHIS_PER_BTC * rate_denominator
    return (satoshi_numerator + rate_numerator - 1) // rate_numerator
