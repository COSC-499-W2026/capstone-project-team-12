from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.storage import StorageService, get_storage_service

NOW = 1_000_000.0
TTL = 900
SECRET = "test-secret"


def make_service(media_dir: Path, now: float = NOW) -> StorageService:
    return StorageService(str(media_dir), SECRET, TTL, clock=lambda: now)


@pytest.fixture
def storage(tmp_path: Path, client: TestClient) -> StorageService:
    """The StorageService the client's requests use, on an empty temporary media directory."""
    service = make_service(tmp_path)
    client.app.dependency_overrides[get_storage_service] = lambda: service
    return service


def error_of(client: TestClient, url: str) -> tuple[int, str]:
    """The status and error code the media route answers `url` with."""
    response = client.get(url)
    return response.status_code, response.json()["error"]["code"]


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


def test_tampered_signature_is_forbidden(client: TestClient, storage: StorageService) -> None:
    url = storage.signed_url(storage.save(b"x", ".mp3"))
    signature = url.rsplit("sig=", 1)[1]
    tampered = url.replace(signature, "0" * len(signature))
    assert error_of(client, tampered) == (HTTPStatus.FORBIDDEN, "FORBIDDEN")


def test_link_for_another_file_is_forbidden(client: TestClient, storage: StorageService) -> None:
    signed_key, other_key = storage.save(b"a", ".mp3"), storage.save(b"b", ".mp3")
    tampered = storage.signed_url(signed_key).replace(signed_key, other_key)
    assert error_of(client, tampered) == (HTTPStatus.FORBIDDEN, "FORBIDDEN")


def test_expired_link_is_forbidden(
    client: TestClient, storage: StorageService, tmp_path: Path
) -> None:
    key = storage.save(b"x", ".mp3")
    expired = make_service(tmp_path, now=NOW - TTL).signed_url(key)
    assert error_of(client, expired) == (HTTPStatus.FORBIDDEN, "FORBIDDEN")


def test_valid_link_to_a_missing_file_is_not_found(
    client: TestClient, storage: StorageService
) -> None:
    key = storage.save(b"x", ".jpg")
    storage.delete(key)  # e.g. a photo purged after 90 days
    assert error_of(client, storage.signed_url(key)) == (HTTPStatus.NOT_FOUND, "NOT_FOUND")


def test_link_without_a_signature_is_a_validation_error(
    client: TestClient, storage: StorageService
) -> None:
    unsigned = storage.signed_url(storage.save(b"x", ".mp3")).split("&sig=")[0]
    assert error_of(client, unsigned) == (HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR")
