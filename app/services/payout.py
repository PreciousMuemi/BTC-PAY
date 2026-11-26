import httpx

from app.config import settings


class DarajaB2C:
    @property
    def base(self) -> str:
        return (
            "https://sandbox.safaricom.co.ke"
            if settings.daraja_env == "sandbox"
            else "https://api.safaricom.co.ke"
        )

    def _token(self) -> str:
        response = httpx.get(
            f"{self.base}/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(
                settings.daraja_consumer_key,
                settings.daraja_consumer_secret.get_secret_value(),
            ),
            timeout=10,
        )
        response.raise_for_status()
        try:
            access_token = response.json()["access_token"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Daraja returned an invalid OAuth response") from exc
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Daraja returned an invalid OAuth response")
        return access_token

    def send_kes(self, phone: str, kes: int, payment_id: str) -> dict[str, object]:
        if kes <= 0:
            raise ValueError("Payout amount must be positive")
        callback_token = settings.mpesa_callback_token.get_secret_value()
        callback_base = settings.public_base_url.rstrip("/")
        body = {
            "OriginatorConversationID": payment_id,
            "InitiatorName": settings.daraja_initiator_name,
            "SecurityCredential": settings.daraja_security_credential.get_secret_value(),
            "CommandID": "BusinessPayment",
            "Amount": str(kes),
            "PartyA": settings.daraja_shortcode,
            "PartyB": phone.lstrip("+"),
            "Remarks": "TembeaPay settlement",
            "QueueTimeOutURL": (
                f"{callback_base}/api/mpesa/timeout/{callback_token}"
            ),
            "ResultURL": f"{callback_base}/api/mpesa/result/{callback_token}",
            "Occasion": "Tourism payment",
        }
        response = httpx.post(
            f"{self.base}/mpesa/b2c/v3/paymentrequest",
            json=body,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Daraja returned an invalid B2C acknowledgement")
        return result
