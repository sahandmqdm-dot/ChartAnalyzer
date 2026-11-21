from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = Field(default="development", validation_alias="APP_ENV")
    app_secret: str = Field(min_length=32, validation_alias="APP_SECRET")
    admin_username: str = Field(default="admin", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="", validation_alias="ADMIN_PASSWORD")
    session_ttl_seconds: int = Field(default=1800, ge=300, le=86400, validation_alias="SESSION_TTL_SECONDS")
    market_data_provider: Literal["binance_spot", "bybit_spot"] = Field(default="binance_spot", validation_alias="MARKET_DATA_PROVIDER")

    @model_validator(mode="after")
    def validate_production_credentials(self):
        if self.environment == "production" and len(self.admin_password) < 16:
            raise ValueError("production ADMIN_PASSWORD must be at least 16 characters")
        return self

    model_config = SettingsConfigDict(extra="ignore")


settings = Settings()
