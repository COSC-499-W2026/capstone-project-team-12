"""StorageService: media files on the local volume (D8), handed out as short-lived signed URLs.

Storage keys are random UUIDs (plus a file extension) made by `save`, so a URL never reveals a
location or an answer; callers never choose a key. A link is only valid until its expiry time
and only for the key it was signed for. The route that serves the files (app.api.v1.media)
calls `verify` (which raises `MediaUrlError`, a 403) and then sends the file at
`existing_path(key)` (which raises `MediaFileNotFoundError`, a 404, once the file is gone, for
example a purged photo).
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from app.config import get_settings
from app.errors import AppError, ErrorCode

MEDIA_URL_PREFIX = "/api/v1/media"
# The formats the spec accepts (MP3 or M4A audio, JPEG or PNG photos), by key suffix. Listed
# here because Python's own table has no ".m4a" on the slim Docker image.
MEDIA_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".jpg": "image/jpeg", ".png": "image/png"}
_SIGNING_PURPOSE = "media-url"


class MediaUrlError(AppError):
    """The signed link was tampered with or has expired (the client gets 403 FORBIDDEN)."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.FORBIDDEN, "This media link is invalid or has expired")


class MediaFileNotFoundError(AppError):
    """No file is stored under the key, e.g. a purged photo (the client gets 404 NOT_FOUND)."""

    def __init__(self) -> None:
        super().__init__(ErrorCode.NOT_FOUND, "This media file does not exist")


class StorageService:
    def __init__(
        self,
        media_dir: str,
        secret_key: str,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(media_dir).resolve()
        self._secret = secret_key.encode()
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def signed_url(self, key: str) -> str:
        """Return a link to the file `key` that stops working after the configured lifetime."""
        expires = int(self._clock()) + self._ttl_seconds
        # Percent-encoded so a "?", "#" or space in the key stays part of the path; the router
        # decodes it back to the key that was signed.
        path = quote(key)
        return f"{MEDIA_URL_PREFIX}/{path}?expires={expires}&sig={self._sign(key, expires)}"

    def verify(self, key: str, expires: int, signature: str) -> None:
        """Raise `MediaUrlError` unless `signature` is genuine for `key` and not yet expired."""
        genuine = hmac.compare_digest(self._sign(key, expires).encode(), signature.encode())
        if not genuine or self._clock() >= expires:
            raise MediaUrlError

    def path(self, key: str) -> Path:
        """The file `key` on disk; `ValueError` if the key leads outside the media directory."""
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError(f"Storage key points outside the media directory: {key!r}")
        if path.is_dir():
            raise ValueError(f"Storage key is a directory, not a file: {key!r}")
        return path

    def existing_path(self, key: str) -> Path:
        """Like `path`, but `MediaFileNotFoundError` unless the file `key` exists."""
        try:
            path = self.path(key)
        except ValueError:
            raise MediaFileNotFoundError from None
        if not path.is_file():
            raise MediaFileNotFoundError
        return path

    def save(self, data: bytes, suffix: str) -> str:
        """Store `data` under a new key, a random UUID plus `suffix` (e.g. ".mp3"); return the key.

        `ValueError` unless `suffix` is one of `MEDIA_TYPES`.
        """
        if suffix not in MEDIA_TYPES:
            raise ValueError(f"Unsupported media file extension: {suffix!r}")
        key = f"{uuid4()}{suffix}"
        self._root.mkdir(parents=True, exist_ok=True)
        self.path(key).write_bytes(data)
        return key

    def delete(self, key: str) -> None:
        """Delete the file `key`. A file that is already gone is not an error."""
        self.path(key).unlink(missing_ok=True)

    def _sign(self, key: str, expires: int) -> str:
        # The purpose label keeps these signatures apart from anything else signed with the
        # same secret key (such as login tokens).
        message = f"{_SIGNING_PURPOSE}:{key}:{expires}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()


@lru_cache
def get_storage_service() -> StorageService:
    settings = get_settings()
    return StorageService(settings.media_dir, settings.secret_key, settings.media_url_ttl_seconds)
