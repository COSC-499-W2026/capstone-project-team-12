"""Application settings, read from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import NonNegativeInt, PositiveInt, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://hunt:hunt@localhost:5432/hunt"
    secret_key: str = "change-me"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    # Mailpit needs neither; a real provider needs STARTTLS and a login
    smtp_starttls: bool = False
    smtp_username: str = ""
    smtp_password: str = ""
    mail_from: str = "Hunt <no-reply@localhost>"
    # where the links in emails point: the web app, whose pages read the token from the link
    app_base_url: str = "http://localhost:5173"
    media_dir: str = "/data/media"
    media_url_ttl_seconds: PositiveInt = 900
    gps_radius_meters: PositiveInt = 30  # default for a new location; each location stores its own
    # game rules from the spec (Configuration). The services check these upper limits;
    # the database only checks the lower bounds, so changing a setting needs no migration.
    locations_per_game: PositiveInt = 5
    max_shuffles_per_session: NonNegativeInt = 2
    max_photo_attempts: PositiveInt = 6

    @model_validator(mode="after")
    def _email_settings_outside_development(self) -> "Settings":
        # links to localhost or a @localhost sender would reach no one outside development
        if self.app_env != "development" and (
            "localhost" in self.app_base_url or "@localhost" in self.mail_from
        ):
            raise ValueError("set APP_BASE_URL and MAIL_FROM outside development")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
