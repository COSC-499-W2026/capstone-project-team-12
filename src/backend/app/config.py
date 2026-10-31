"""Application settings, read from environment variables (see .env.example)."""

from email.utils import parseaddr
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import NonNegativeInt, PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# AudioService implementations (spec, AudioService); "hybrid" and "ai_generated" are Future Work.
AudioServiceType = Literal["uploaded"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://hunt:hunt@localhost:5432/hunt"
    secret_key: str = "change-me"
    access_token_secret_key: str = "change-me"
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
    # upload limits from the spec (Audio, Error Codes): photos and audio at most 5 MB (MiB here),
    # clue and feedback recordings at most 60 s
    max_media_file_mb: PositiveInt = 5
    max_audio_seconds: PositiveInt = 60
    # proposed, not in the spec (team and client to confirm): rows in one locations CSV, far
    # above the campus's few dozen locations, so one import cannot hold its transaction (and its
    # room number locks) for long; 1000 rows take about 1.5 s
    max_import_rows: PositiveInt = 1000
    # proposed, not in the spec: size of one locations CSV. 1000 rows are about 35 KB; parsing
    # holds about 23 times the file in memory at worst (one row of commas)
    max_import_file_kb: PositiveInt = 512
    # proposed too: files in one audio zip. One import is one transaction that locks each clue it
    # touches until it commits, and each file is re-encoded (0.08 s for a 10 s clip, 0.41 s for
    # 60 s), so 100 files take under 45 s, inside a 60 s proxy timeout: a zip per language (90
    # clues for 30 locations), and every feedback phrase (78) in one
    max_import_files: PositiveInt = 100
    audio_service_type: AudioServiceType = "uploaded"
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
    def _check_email_settings(self) -> "Settings":
        if "@" not in parseaddr(self.mail_from)[1]:
            raise ValueError("MAIL_FROM needs an address with an @")
        if self.app_env == "development":
            return self
        # links to this machine would reach no one outside development
        if any(host in self.app_base_url + self.mail_from for host in ("localhost", "127.0.0.1")):
            raise ValueError("set APP_BASE_URL and MAIL_FROM outside development")
        if self.smtp_username and not self.smtp_starttls:  # the password would go in the clear
            raise ValueError("set SMTP_STARTTLS when SMTP_USERNAME is set outside development")
        return self

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
