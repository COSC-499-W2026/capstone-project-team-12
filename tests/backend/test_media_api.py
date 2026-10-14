from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import pytest
from fastapi.testclient import TestClient

from app.services.storage import (
    MEDIA_URL_PREFIX,
    MediaNotFoundError,
    MediaUrlError,
    StorageService,
    get_storage_service,
)

NOW = 1_000_000.0
TTL = 900
SECRET = "test-secret"

SplitUrl = Callable[[str], tuple[str, int, str]]

FORBIDDEN = (HTTPStatus.FORBIDDEN, MediaUrlError().body())
NOT_FOUND = (HTTPStatus.NOT_FOUND, MediaNotFoundError().body())


def make_service(media_dir: Path, now: float = NOW) -> StorageService:
    return StorageService(str(media_dir), SECRET, TTL, clock=lambda: now)


def media_url(key: str, **query: object) -> str:
    """A media link with the given query parameters. Every "/" in the key is percent-encoded, so
    a key such as "../x" reaches the route as it is instead of being resolved by the client."""
    return f"{MEDIA_URL_PREFIX}/{quote(key, safe='')}?{urlencode(query)}"


@pytest.fixture
def storage(tmp_path: Path, client: TestClient) -> StorageService:
    """The StorageService the client's requests use, on an empty temporary media directory."""
    service = make_service(tmp_path)
    client.app.dependency_overrides[get_storage_service] = lambda: service
    return service


def error_of(client: TestClient, url: str) -> tuple[int, dict[str, Any]]:
    """The status and full error body the media route answers `url` with."""
    response = client.get(url)
    return response.status_code, response.json()


def signed(storage: StorageService, split_media_url: SplitUrl, key: str) -> str:
    """A genuine link to `key`, even one `save` would never make."""
    _, expires, signature = split_media_url(storage.signed_url(key))
    return media_url(key, expires=expires, sig=signature)


@pytest.mark.parametrize(
    ("suffix", "content_type"),
    [(".mp3", "audio/mpeg"), (".m4a", "audio/mp4"), (".jpg", "image/jpeg"), (".png", "image/png")],
)
def test_valid_link_sends_the_file(
    client: TestClient, storage: StorageService, suffix: str, content_type: str
) -> None:
    key = storage.save(b"media bytes", suffix)
    response = client.get(storage.signed_url(key))
    assert response.status_code == HTTPStatus.OK
    assert response.content == b"media bytes"
    assert response.headers["content-type"] == content_type
    assert response.headers["cache-control"] == "private, no-store"


def test_range_request_sends_part_of_the_file(client: TestClient, storage: StorageService) -> None:
    """Audio players (Safari and iOS in particular) seek with Range requests."""
    key = storage.save(b"0123456789", ".mp3")
    response = client.get(storage.signed_url(key), headers={"Range": "bytes=2-5"})
    assert response.status_code == HTTPStatus.PARTIAL_CONTENT
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_tampered_signature_is_forbidden(
    client: TestClient, storage: StorageService, split_media_url: SplitUrl
) -> None:
    key, expires, _ = split_media_url(storage.signed_url(storage.save(b"x", ".mp3")))
    tampered = media_url(key, expires=expires, sig="0" * 64)
    assert error_of(client, tampered) == FORBIDDEN


def test_link_for_another_file_is_forbidden(
    client: TestClient, storage: StorageService, split_media_url: SplitUrl
) -> None:
    _, expires, signature = split_media_url(storage.signed_url(storage.save(b"a", ".mp3")))
    tampered = media_url(storage.save(b"b", ".mp3"), expires=expires, sig=signature)
    assert error_of(client, tampered) == FORBIDDEN


def test_expired_link_is_forbidden(
    client: TestClient, storage: StorageService, tmp_path: Path
) -> None:
    key = storage.save(b"x", ".mp3")
    expired = make_service(tmp_path, now=NOW - TTL).signed_url(key)
    assert error_of(client, expired) == FORBIDDEN


def test_valid_link_to_a_missing_file_is_not_found(
    client: TestClient, storage: StorageService
) -> None:
    key = storage.save(b"x", ".jpg")
    storage.delete(key)  # e.g. a photo purged after 90 days
    assert error_of(client, storage.signed_url(key)) == NOT_FOUND


@pytest.mark.parametrize("key", ["../outside.jpg", "album.jpg"], ids=["outside", "directory"])
def test_valid_link_to_something_that_is_not_a_media_file_is_not_found(
    client: TestClient, storage: StorageService, split_media_url: SplitUrl, tmp_path: Path, key: str
) -> None:
    (tmp_path / "album.jpg").mkdir()
    (tmp_path.parent / "outside.jpg").write_bytes(b"x")
    assert error_of(client, signed(storage, split_media_url, key)) == NOT_FOUND


@pytest.mark.parametrize(
    ("query", "field"),
    [({}, "sig"), ({"sig": "0" * 64, "expires": "soon"}, "expires")],
    ids=["no signature", "expires not a number"],
)
def test_malformed_link_is_a_validation_error(
    client: TestClient,
    storage: StorageService,
    split_media_url: SplitUrl,
    query: dict[str, str],
    field: str,
) -> None:
    key, expires, _ = split_media_url(storage.signed_url(storage.save(b"x", ".mp3")))
    status, body = error_of(client, media_url(key, **({"expires": expires} | query)))
    assert status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert [detail["field"] for detail in body["error"]["details"]] == [field]
