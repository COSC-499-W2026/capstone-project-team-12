import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import Settings, get_settings
from app.services.storage import (
    MEDIA_URL_PREFIX,
    MediaUrlError,
    StorageService,
    get_storage_service,
)

NOW = 1_000_000.0
TTL = 900


def make_service(media_dir: Path, clock_value: list[float] | None = None) -> StorageService:
    now = clock_value if clock_value is not None else [NOW]
    return StorageService(str(media_dir), "test-secret", TTL, clock=lambda: now[0])


def split_url(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    key = parts.path.removeprefix(f"{MEDIA_URL_PREFIX}/")
    return key, int(query["expires"][0]), query["sig"][0]


def test_signed_url_verifies(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    key, expires, signature = split_url(service.signed_url("abc"))
    assert key == "abc"
    assert expires == int(NOW) + TTL
    service.verify(key, expires, signature)


@pytest.mark.parametrize("field", ["key", "expires", "signature"])
def test_tampered_url_is_rejected(tmp_path: Path, field: str) -> None:
    service = make_service(tmp_path)
    key, expires, signature = split_url(service.signed_url("abc"))
    changes = {"key": ("other", expires, signature), "expires": (key, expires + 1, signature)}
    args = changes.get(field, (key, expires, "0" * len(signature)))
    with pytest.raises(MediaUrlError):
        service.verify(*args)


def test_non_ascii_signature_is_rejected(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    key, expires, _ = split_url(service.signed_url("abc"))
    with pytest.raises(MediaUrlError):
        service.verify(key, expires, "é")


def test_url_signed_with_another_secret_is_rejected(tmp_path: Path) -> None:
    other = StorageService(str(tmp_path), "another-secret", TTL, clock=lambda: NOW)
    with pytest.raises(MediaUrlError):
        make_service(tmp_path).verify(*split_url(other.signed_url("abc")))


def test_url_expires_after_the_lifetime(tmp_path: Path) -> None:
    clock = [NOW]
    service = make_service(tmp_path, clock)
    key, expires, signature = split_url(service.signed_url("abc"))
    clock[0] = NOW + TTL - 1
    service.verify(key, expires, signature)
    clock[0] = NOW + TTL
    with pytest.raises(MediaUrlError):
        service.verify(key, expires, signature)


def test_delete_removes_the_file(tmp_path: Path) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"x")
    make_service(tmp_path).delete("photo.jpg")
    assert not (tmp_path / "photo.jpg").exists()


def test_delete_of_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.delete("missing.jpg")
    service.delete("missing.jpg")


@pytest.mark.parametrize("key", ["../outside.jpg", "a/../../outside.jpg", ".", ""])
def test_delete_rejects_keys_outside_the_media_directory(tmp_path: Path, key: str) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (tmp_path / "outside.jpg").write_bytes(b"x")
    with pytest.raises(ValueError, match="outside the media directory"):
        make_service(media_dir).delete(key)
    assert (tmp_path / "outside.jpg").exists()


def test_default_lifetime_is_fifteen_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDIA_URL_TTL_SECONDS", raising=False)
    assert Settings(_env_file=None).media_url_ttl_seconds == 15 * 60


def test_get_storage_service_uses_the_configured_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIA_URL_TTL_SECONDS", "60")
    get_settings.cache_clear()
    get_storage_service.cache_clear()
    try:
        _, expires, _ = split_url(get_storage_service().signed_url("abc"))
        assert expires - time.time() <= 60
    finally:
        get_settings.cache_clear()
        get_storage_service.cache_clear()


def test_get_storage_service_is_cached() -> None:
    assert get_storage_service() is get_storage_service()
