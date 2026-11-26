from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    at_username: str = ""
    at_api_key: str = ""
    mock_lightning: bool = True
    webhook_secret: str = ""
    merchant_name: str = "TembeaPay Merchant"
    merchant_mpesa_target: str = ""
    merchant_phone: str = ""
    lnbits_url: str = "http://127.0.0.1:5000"
    lnbits_api_key: str = ""
    airtime_reward_kes: Decimal = Field(default=Decimal("20"), gt=0)
    quote_ttl_minutes: int = Field(default=15, gt=0)
    kes_per_btc_fallback: Decimal = Field(gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
