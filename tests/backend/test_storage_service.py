import hashlib
import hmac
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.services.storage import MediaUrlError, StorageService, get_storage_service

NOW = 1_000_000.0
TTL = 900
SECRET = b"test-secret"

SplitUrl = Callable[[str], tuple[str, int, str]]

TAMPERINGS = {
    "key": lambda genuine: {**genuine, "key": "other"},
    "expires": lambda genuine: {**genuine, "expires": genuine["expires"] + 1},
    "signature": lambda genuine: {**genuine, "signature": "0" * len(genuine["signature"])},
    "non-ascii signature": lambda genuine: {**genuine, "signature": "é"},
}


def make_service(
    media_dir: Path, now: float = NOW, secret_key: str = SECRET.decode()
) -> StorageService:
    return StorageService(str(media_dir), secret_key, TTL, clock=lambda: now)


def test_signed_url_verifies(tmp_path: Path, split_media_url: SplitUrl) -> None:
    service = make_service(tmp_path)
    key, expires, signature = split_media_url(service.signed_url("abc"))
    assert key == "abc"
    assert expires == int(NOW) + TTL
    service.verify(key, expires, signature)


@pytest.mark.parametrize("tamper", TAMPERINGS.values(), ids=TAMPERINGS.keys())
def test_tampered_url_is_rejected(
    tmp_path: Path,
    split_media_url: SplitUrl,
    tamper: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    service = make_service(tmp_path)
    key, expires, signature = split_media_url(service.signed_url("abc"))
    genuine = {"key": key, "expires": expires, "signature": signature}
    with pytest.raises(MediaUrlError):
        service.verify(**tamper(genuine))


def test_url_signed_with_another_secret_is_rejected(
    tmp_path: Path, split_media_url: SplitUrl
) -> None:
    other = make_service(tmp_path, secret_key="another-secret")
    with pytest.raises(MediaUrlError):
        make_service(tmp_path).verify(*split_media_url(other.signed_url("abc")))


def test_signature_made_for_another_purpose_is_rejected(tmp_path: Path) -> None:
    expires = int(NOW) + TTL
    unlabelled = hmac.new(SECRET, f"abc:{expires}".encode(), hashlib.sha256).hexdigest()
    with pytest.raises(MediaUrlError):
        make_service(tmp_path).verify("abc", expires, unlabelled)


def test_url_expires_after_the_lifetime(tmp_path: Path, split_media_url: SplitUrl) -> None:
    url = make_service(tmp_path).signed_url("abc")
    make_service(tmp_path, now=NOW + TTL - 1).verify(*split_media_url(url))
    with pytest.raises(MediaUrlError):
        make_service(tmp_path, now=NOW + TTL).verify(*split_media_url(url))


def test_delete_removes_the_file(tmp_path: Path) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"x")
    make_service(tmp_path).delete("photo.jpg")
    assert not (tmp_path / "photo.jpg").exists()


def test_delete_of_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    make_service(tmp_path).delete("missing.jpg")


@pytest.mark.parametrize("key", ["../outside.jpg", "a/../../outside.jpg", ".", ""])
def test_delete_rejects_keys_outside_the_media_directory(tmp_path: Path, key: str) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (tmp_path / "outside.jpg").write_bytes(b"x")
    with pytest.raises(ValueError, match="outside the media directory"):
        make_service(media_dir).delete(key)
    assert (tmp_path / "outside.jpg").exists()


def test_delete_rejects_a_directory(tmp_path: Path) -> None:
    (tmp_path / "photos").mkdir()
    with pytest.raises(ValueError, match="is a directory"):
        make_service(tmp_path).delete("photos")
    assert (tmp_path / "photos").is_dir()


def test_default_lifetime_is_fifteen_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDIA_URL_TTL_SECONDS", raising=False)
    assert Settings(_env_file=None).media_url_ttl_seconds == 15 * 60


@pytest.mark.parametrize("lifetime", ["0", "-900"])
def test_lifetime_must_be_positive(monkeypatch: pytest.MonkeyPatch, lifetime: str) -> None:
    monkeypatch.setenv("MEDIA_URL_TTL_SECONDS", lifetime)
    with pytest.raises(ValidationError, match="media_url_ttl_seconds"):
        Settings(_env_file=None)


def test_get_storage_service_uses_the_configured_lifetime(
    monkeypatch: pytest.MonkeyPatch, split_media_url: SplitUrl
) -> None:
    monkeypatch.setenv("MEDIA_URL_TTL_SECONDS", "60")
    get_settings.cache_clear()
    get_storage_service.cache_clear()
    try:
        before = int(time.time())
        _, expires, _ = split_media_url(get_storage_service().signed_url("abc"))
        assert before + 60 <= expires <= int(time.time()) + 60
    finally:
        get_settings.cache_clear()
        get_storage_service.cache_clear()


def test_get_storage_service_is_cached() -> None:
    assert get_storage_service() is get_storage_service()
