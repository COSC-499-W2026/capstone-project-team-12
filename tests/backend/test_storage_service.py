import hashlib
import hmac
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.services.storage import (
    MediaNotFoundError,
    MediaUrlError,
    StorageService,
    get_storage_service,
)

NOW = 1_000_000.0
TTL = 900
SECRET = b"test-secret"

SplitUrl = Callable[[str], tuple[str, int, str]]

TAMPERINGS = {
    "key": {"key": "other"},
    "expires": {"expires": int(NOW) + TTL + 1},
    "signature": {"signature": "0" * 64},  # a SHA-256 hex digest is 64 characters
    "non-ascii signature": {"signature": "é"},
}


def make_service(
    media_dir: Path, now: float = NOW, secret_key: str = SECRET.decode()
) -> StorageService:
    return StorageService(str(media_dir), secret_key, TTL, clock=lambda: now)


@pytest.mark.parametrize("key", ["abc", "clips/a b#1?.mp3", "café/100%.mp3"])
def test_signed_url_verifies(tmp_path: Path, split_media_url: SplitUrl, key: str) -> None:
    service = make_service(tmp_path)
    url_key, expires, signature = split_media_url(service.signed_url(key))
    assert url_key == key
    assert expires == int(NOW) + TTL
    service.verify(url_key, expires, signature)


@pytest.mark.parametrize("override", TAMPERINGS.values(), ids=TAMPERINGS.keys())
def test_tampered_url_is_rejected(
    tmp_path: Path, split_media_url: SplitUrl, override: dict[str, object]
) -> None:
    service = make_service(tmp_path)
    key, expires, signature = split_media_url(service.signed_url("abc"))
    genuine = {"key": key, "expires": expires, "signature": signature}
    with pytest.raises(MediaUrlError) as rejected:
        service.verify(**(genuine | override))
    assert rejected.value.status == HTTPStatus.FORBIDDEN


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


def test_save_writes_the_file_under_a_random_uuid_key(tmp_path: Path) -> None:
    service = make_service(tmp_path / "media")
    key = service.save(b"clip", ".mp3")
    assert key.endswith(".mp3")
    stem = key.removesuffix(".mp3")
    assert str(UUID(stem)) == stem and UUID(stem).version == 4
    assert (tmp_path / "media" / key).read_bytes() == b"clip"


def test_save_never_reuses_a_key(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    assert service.save(b"a", ".jpg") != service.save(b"a", ".jpg")


@pytest.mark.parametrize("suffix", ["", "mp3", ".MP3", ".html", "/../x.mp3"])
def test_save_rejects_an_unsupported_suffix(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(ValueError, match="Unsupported media file extension"):
        make_service(tmp_path).save(b"x", suffix)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_save_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def write_half_then_fail(path: Path, data: bytes) -> int:
        path.write_text("half")
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", write_half_then_fail)
    with pytest.raises(OSError, match="disk full"):
        make_service(tmp_path).save(b"clip", ".mp3")
    assert list(tmp_path.iterdir()) == []


def test_stored_file_is_the_saved_file_with_its_media_type(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    key = service.save(b"png bytes", ".png")
    stored = service.stored_file(key)
    assert stored.path == tmp_path.resolve() / key
    assert stored.media_type == "image/png"
    assert stored.stat.st_size == len(b"png bytes")


def test_stored_file_without_a_known_extension_is_binary(tmp_path: Path) -> None:
    (tmp_path / "clip").write_bytes(b"x")
    assert make_service(tmp_path).stored_file("clip").media_type == "application/octet-stream"


@pytest.mark.parametrize("key", ["missing.jpg", "photos", "../outside.jpg", "", "a.jpg/b"])
def test_stored_file_is_not_found_without_a_file(tmp_path: Path, key: str) -> None:
    media_dir = tmp_path / "media"
    (media_dir / "photos").mkdir(parents=True)
    (media_dir / "a.jpg").write_bytes(b"x")
    (tmp_path / "outside.jpg").write_bytes(b"x")
    with pytest.raises(MediaNotFoundError) as missing:
        make_service(media_dir).stored_file(key)
    assert missing.value.status == HTTPStatus.NOT_FOUND


def test_path_is_the_file_in_the_media_directory(tmp_path: Path) -> None:
    assert make_service(tmp_path).path("clips/a.mp3") == tmp_path.resolve() / "clips" / "a.mp3"


@pytest.mark.parametrize("method", ["path", "delete"])
@pytest.mark.parametrize("key", ["../outside.jpg", "a/../../outside.jpg", ".", ""])
def test_keys_outside_the_media_directory_are_rejected(
    tmp_path: Path, method: str, key: str
) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (tmp_path / "outside.jpg").write_bytes(b"x")
    with pytest.raises(ValueError, match="outside the media directory"):
        getattr(make_service(media_dir), method)(key)
    assert (tmp_path / "outside.jpg").exists()


@pytest.mark.parametrize("method", ["path", "delete"])
def test_a_directory_is_rejected(tmp_path: Path, method: str) -> None:
    (tmp_path / "photos").mkdir()
    with pytest.raises(ValueError, match="is a directory"):
        getattr(make_service(tmp_path), method)("photos")
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
