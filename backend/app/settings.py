from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = Field(default="development", validation_alias="APP_ENV")
    app_secret: str = Field(min_length=32, validation_alias="APP_SECRET")

    model_config = SettingsConfigDict(extra="ignore")


settings = Settings()
