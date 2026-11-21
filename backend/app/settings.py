from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = Field(default="development", validation_alias="APP_ENV")
    app_secret: str = Field(min_length=32, validation_alias="APP_SECRET")
    admin_username: str = Field(default="admin", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="", validation_alias="ADMIN_PASSWORD")
    session_ttl_seconds: int = Field(default=1800, ge=300, le=86400, validation_alias="SESSION_TTL_SECONDS")\n    market_data_provider: str = Field(default="binance_spot", validation_alias="MARKET_DATA_PROVIDER")

    model_config = SettingsConfigDict(extra="ignore")


settings = Settings()
