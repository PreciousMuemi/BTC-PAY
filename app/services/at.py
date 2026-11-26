import logging
import re
from functools import lru_cache
from typing import Any

import africastalking
from fastapi.concurrency import run_in_threadpool

from app.config import settings


logger = logging.getLogger(__name__)
_GSM_BASIC = frozenset(
    '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ'
    ' !"#¤%&\'()*+,-./0123456789:;<=>?¡'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿'
    'abcdefghijklmnopqrstuvwxyzäöñüà'
)
_GSM_EXTENSION = frozenset("\f^{}\\[~]|€")
_MAX_SMS_SEPTETS = 159


def normalize_phone(phone: str) -> str:
    if not isinstance(phone, str):
        raise ValueError("Phone number must be a string")
    compact = re.sub(r"[\s().-]", "", phone)

    if re.fullmatch(r"07\d{8}", compact):
        return f"+254{compact[1:]}"
    if re.fullmatch(r"7\d{8}", compact):
        return f"+254{compact}"
    if re.fullmatch(r"2547\d{8}", compact):
        return f"+{compact}"
    if re.fullmatch(r"\+2547\d{8}", compact):
        return compact
    raise ValueError("Invalid Kenyan mobile phone number")


def _fit_gsm_sms(message: str, mpesa_simulated: bool) -> str:
    if not isinstance(message, str) or not message:
        raise ValueError("SMS message must be a non-empty string")
    body = f"SANDBOX: {message}" if mpesa_simulated else message

    septets = 0
    for character in body:
        if character in _GSM_BASIC:
            septets += 1
        elif character in _GSM_EXTENSION:
            septets += 2
        else:
            raise ValueError("SMS message contains characters outside the GSM alphabet")

    if septets <= _MAX_SMS_SEPTETS:
        return body

    suffix = "..."
    suffix_septets = len(suffix)
    truncated: list[str] = []
    used_septets = 0
    for character in body:
        character_septets = 1 if character in _GSM_BASIC else 2
        if used_septets + character_septets + suffix_septets > _MAX_SMS_SEPTETS:
            break
        truncated.append(character)
        used_septets += character_septets
    return "".join(truncated) + suffix


@lru_cache(maxsize=1)
def _at_clients(username: str, api_key: str) -> tuple[Any, Any]:
    if not username or not api_key:
        raise ValueError("AT_USERNAME and AT_API_KEY must be configured")
    africastalking.initialize(username, api_key)
    return africastalking.SMS, africastalking.Airtime


def _sms_succeeded(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    try:
        recipients = response["SMSMessageData"]["Recipients"]
    except (KeyError, TypeError):
        return False
    return bool(recipients) and all(
        isinstance(recipient, dict)
        and recipient.get("status") == "Success"
        for recipient in recipients
    )


def _airtime_succeeded(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    responses = response.get("responses")
    if isinstance(responses, list):
        return len(responses) == 1 and isinstance(responses[0], dict) and (
            str(responses[0].get("status", "")).casefold() in {"sent", "success"}
        )
    return response.get("numSent") == 1


async def send_sms(
    to: str,
    message: str,
    *,
    mpesa_simulated: bool = True,
) -> bool:
    try:
        phone = normalize_phone(to)
        body = _fit_gsm_sms(message, mpesa_simulated)
        sms_client, _ = _at_clients(settings.at_username, settings.at_api_key)
        response = await run_in_threadpool(sms_client.send, body, [phone])
        if not _sms_succeeded(response):
            logger.warning("Africa's Talking did not confirm SMS delivery")
            return False
        return True
    except Exception:
        logger.exception("Africa's Talking SMS send failed")
        return False


async def send_airtime(phone: str, kes: int) -> bool:
    try:
        normalized_phone = normalize_phone(phone)
        if not isinstance(kes, int) or isinstance(kes, bool) or kes <= 0:
            raise ValueError("Airtime amount must be a positive whole number of KES")
        _, airtime_client = _at_clients(settings.at_username, settings.at_api_key)
        response = await run_in_threadpool(
            airtime_client.send,
            phone_number=normalized_phone,
            amount=str(kes),
            currency_code="KES",
        )
        if not _airtime_succeeded(response):
            logger.warning("Africa's Talking did not confirm airtime delivery")
            return False
        return True
    except Exception:
        logger.exception("Africa's Talking airtime send failed")
        return False
