"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import NonNegativeInt, PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://hunt:hunt@localhost:5432/hunt"
    secret_key: str = "change-me"
    access_token_secret_key: str = "change-me"
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
    session_idle_expiry_hours: PositiveInt = 24
    # "today" for day-based rules, e.g. carry-over
    app_timezone: str = "America/Vancouver"

    @field_validator("app_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        # fail at startup, not with a 500 when the first game starts
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError) as error:
            # OSError: a directory name such as "America", not a zone
            raise ValueError(f"unknown time zone {value!r}") from error
        return value

    @model_validator(mode="after")
    def _check_token_settings(self) -> "Settings":
        if self.app_env == "development":
            return self
        if not self.access_token_secret_key:
            raise ValueError("ACCESS_TOKEN_SECRET_KEY not set")
        if self.access_token_secret_key == "change-me":
            raise ValueError("ACCESS_TOKEN_SECRET_KEY must be changed from default value")
        if len(self.access_token_secret_key) < 16:
            raise ValueError("ACCESS_TOKEN_SECRET_KEY must be at least 16 characters.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
