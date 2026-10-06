"""StorageService: media files on the local volume (D8), handed out as short-lived signed URLs.

Storage keys are random UUIDs, so a URL never reveals a location or an answer. A link is only
valid until its expiry time and only for the key it was signed for. The route that serves the
files is not part of this module: it calls `verify` and answers 403 on `MediaUrlError`.
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

MEDIA_URL_PREFIX = "/api/v1/media"


class MediaUrlError(Exception):
    """The signed link was tampered with or has expired (the router answers 403)."""


class MediaNotFoundError(Exception):
    """No clip exists for the requested feedback code or clue audio asset."""


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
        return f"{MEDIA_URL_PREFIX}/{key}?expires={expires}&sig={self._sign(key, expires)}"

    def verify(self, key: str, expires: int, signature: str) -> None:
        """Raise `MediaUrlError` unless `signature` is genuine for `key` and not yet expired."""
        genuine = hmac.compare_digest(self._sign(key, expires).encode(), signature.encode())
        if not genuine or self._clock() >= expires:
            raise MediaUrlError

    def delete(self, key: str) -> None:
        """Delete the file `key`. A file that is already gone is not an error."""
        self._path_for(key).unlink(missing_ok=True)

    def _sign(self, key: str, expires: int) -> str:
        message = f"{key}:{expires}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _path_for(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError(f"Storage key points outside the media directory: {key!r}")
        return path


@lru_cache
def get_storage_service() -> StorageService:
    settings = get_settings()
    return StorageService(settings.media_dir, settings.secret_key, settings.media_url_ttl_seconds)
