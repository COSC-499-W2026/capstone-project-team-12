"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import NonNegativeInt, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://hunt:hunt@localhost:5432/hunt"
    secret_key: str = "change-me"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    media_dir: str = "/data/media"
    media_url_ttl_seconds: PositiveInt = 900
    gps_radius_meters: PositiveInt = 30  # default for a new location; each location stores its own
    # game rules from the spec (Configuration). The services check these upper limits;
    # the database only checks the lower bounds, so changing a setting needs no migration.
    locations_per_game: PositiveInt = 5
    max_shuffles_per_session: NonNegativeInt = 2
    max_photo_attempts: PositiveInt = 6
    # "today" for day-based rules, e.g. carry-over
    app_timezone: str = "America/Vancouver"


@lru_cache
def get_settings() -> Settings:
    return Settings()
