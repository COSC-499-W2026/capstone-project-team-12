"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://hunt:hunt@localhost:5432/hunt"
    secret_key: str = "change-me"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    media_dir: str = "/data/media"
    media_url_ttl_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()
