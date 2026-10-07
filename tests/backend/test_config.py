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


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
