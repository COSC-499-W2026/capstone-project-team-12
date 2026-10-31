import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_defaults_point_to_local_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://hunt:hunt@localhost:5432/hunt"
    assert settings.smtp_port == 1025


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/test")
    monkeypatch.setenv("SMTP_PORT", "2525")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/test"
    assert settings.smtp_port == 2525


def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_PORT", "not-a-number")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_gps_radius_accepts_a_positive_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GPS_RADIUS_METERS", "45")
    assert Settings(_env_file=None).gps_radius_meters == 45


@pytest.mark.parametrize("radius", ["0", "-30"])
def test_gps_radius_must_be_positive(monkeypatch: pytest.MonkeyPatch, radius: str) -> None:
    monkeypatch.setenv("GPS_RADIUS_METERS", radius)
    with pytest.raises(ValidationError, match="gps_radius_meters"):
        Settings(_env_file=None)


def test_upload_limits_default_to_5_mb_and_60_seconds() -> None:
    settings = Settings(_env_file=None)
    assert (settings.max_media_file_mb, settings.max_audio_seconds) == (5, 60)


def test_upload_limits_can_be_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_MEDIA_FILE_MB", "8")
    monkeypatch.setenv("MAX_AUDIO_SECONDS", "90")
    settings = Settings(_env_file=None)
    assert (settings.max_media_file_mb, settings.max_audio_seconds) == (8, 90)


def test_import_limits_default_to_1000_rows_and_512_kb(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)
    assert (settings.max_import_rows, settings.max_import_file_kb) == (1000, 512)
    monkeypatch.setenv("MAX_IMPORT_ROWS", "50")
    monkeypatch.setenv("MAX_IMPORT_FILE_KB", "64")
    settings = Settings(_env_file=None)
    assert (settings.max_import_rows, settings.max_import_file_kb) == (50, 64)


@pytest.mark.parametrize(
    "variable", ["MAX_MEDIA_FILE_MB", "MAX_AUDIO_SECONDS", "MAX_IMPORT_ROWS", "MAX_IMPORT_FILE_KB"]
)
def test_upload_limits_must_be_positive(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    monkeypatch.setenv(variable, "0")
    with pytest.raises(ValidationError, match=variable.lower()):
        Settings(_env_file=None)


# a typo, and a directory name (ZoneInfo raises OSError for it)
@pytest.mark.parametrize("name", ["America/Vancover", "America"])
def test_app_timezone_must_exist(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv("APP_TIMEZONE", name)
    with pytest.raises(ValidationError, match="app_timezone"):
        Settings(_env_file=None)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("APP_BASE_URL", "http://localhost:5173", "APP_BASE_URL and MAIL_FROM"),
        ("APP_BASE_URL", "http://127.0.0.1:5173", "APP_BASE_URL and MAIL_FROM"),
        ("SMTP_USERNAME", "apikey", "SMTP_STARTTLS"),  # the password would be sent in the clear
        ("MAIL_FROM", "no-reply", "MAIL_FROM needs an address"),
    ],
)
def test_unsafe_email_settings(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, message: str
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "https://hunt.example.ca")
    monkeypatch.setenv("MAIL_FROM", "Hunt <no-reply@hunt.example.ca>")
    monkeypatch.setenv("ACCESS_TOKEN_SECRET_KEY", "a-valid-secret-key-123")
    assert Settings(_env_file=None).app_env == "production"  # fine until one value is unsafe
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        (
            "ACCESS_TOKEN_SECRET_KEY",
            "change-me",
            "ACCESS_TOKEN_SECRET_KEY must be changed from default value",
        ),
        ("ACCESS_TOKEN_SECRET_KEY", "", "ACCESS_TOKEN_SECRET_KEY not set"),
        (
            "ACCESS_TOKEN_SECRET_KEY",
            "abcd",
            "ACCESS_TOKEN_SECRET_KEY must be at least 16 characters.",
        ),
    ],
)
def test_unsafe_token_settings(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, message: str
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ACCESS_TOKEN_SECRET_KEY", "a-valid-secret-key-123")
    monkeypatch.setenv("APP_BASE_URL", "https://hunt.example.ca")
    monkeypatch.setenv("MAIL_FROM", "Hunt <no-reply@hunt.example.ca>")
    assert Settings(_env_file=None).app_env == "production"  # fine until one value is unsafe
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None)
