from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    environment: str = "development"
    app_secret: str
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

settings = Settings()
