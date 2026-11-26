from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    at_username: str = ""
    at_api_key: SecretStr = SecretStr("")
    lightning_provider: Literal["mock", "blink"] = "mock"
    blink_api_url: str = "https://api.blink.sv/graphql"
    blink_api_key: SecretStr = SecretStr("")
    blink_wallet_id: str | None = None
    payout_mode: Literal["simulated", "daraja_sandbox", "daraja_live"] = "simulated"
    daraja_env: Literal["sandbox", "production", "live"] = "sandbox"
    daraja_consumer_key: str = ""
    daraja_consumer_secret: SecretStr = SecretStr("")
    daraja_initiator_name: str = ""
    daraja_security_credential: SecretStr = SecretStr("")
    daraja_shortcode: str = ""
    public_base_url: str = ""
    mpesa_callback_token: SecretStr = SecretStr("")
    webhook_secret: str = ""
    merchant_name: str = "TembeaPay Merchant"
    merchant_mpesa_target: str = ""
    merchant_phone: str = ""
    airtime_reward_kes: int = Field(default=20, gt=0)
    quote_ttl_minutes: int = Field(default=15, gt=0)
    kes_per_btc_fallback: Decimal = Field(gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "Settings":
        if (
            self.lightning_provider == "blink"
            and not self.blink_api_key.get_secret_value().strip()
        ):
            raise ValueError("BLINK_API_KEY is required when LIGHTNING_PROVIDER=blink")

        if self.payout_mode.startswith("daraja_"):
            required_values = {
                "DARAJA_CONSUMER_KEY": self.daraja_consumer_key,
                "DARAJA_CONSUMER_SECRET": self.daraja_consumer_secret.get_secret_value(),
                "DARAJA_INITIATOR_NAME": self.daraja_initiator_name,
                "DARAJA_SECURITY_CREDENTIAL": (
                    self.daraja_security_credential.get_secret_value()
                ),
                "DARAJA_SHORTCODE": self.daraja_shortcode,
                "PUBLIC_BASE_URL": self.public_base_url,
                "MPESA_CALLBACK_TOKEN": self.mpesa_callback_token.get_secret_value(),
            }
            missing = [name for name, value in required_values.items() if not value.strip()]
            if missing:
                raise ValueError(
                    "Required Daraja settings missing for PAYOUT_MODE: "
                    + ", ".join(missing)
                )
            if self.payout_mode == "daraja_sandbox" and self.daraja_env != "sandbox":
                raise ValueError("DARAJA_ENV must be sandbox for PAYOUT_MODE=daraja_sandbox")
            if self.payout_mode == "daraja_live" and self.daraja_env not in {
                "production",
                "live",
            }:
                raise ValueError("DARAJA_ENV must be production for PAYOUT_MODE=daraja_live")
        return self


settings = Settings()
