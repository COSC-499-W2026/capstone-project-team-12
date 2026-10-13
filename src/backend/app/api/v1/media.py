from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.services.storage import StorageService, get_storage_service

media_router = APIRouter(prefix="/media", tags=["media"])

# Links expire and photos are purged, so no browser or proxy may keep a copy past either.
_NO_CACHE = {"Cache-Control": "private, no-store"}


@media_router.get("/{key:path}", operation_id="getMediaFile", response_class=FileResponse)
def get_media_file(
    key: str,
    expires: int,
    sig: str,
    storage: Annotated[StorageService, Depends(get_storage_service)],
) -> FileResponse:
    """Send a media file through a link made by StorageService.signed_url.

    No access token: audio players and image tags cannot send one, so the signature is the
    permission. 403 FORBIDDEN for a tampered or expired link, 404 NOT_FOUND once the file is gone.
    """
    storage.verify(key, expires, sig)
    file = storage.stored_file(key)
    return FileResponse(
        file.path, media_type=file.media_type, stat_result=file.stat, headers=_NO_CACHE
    )
