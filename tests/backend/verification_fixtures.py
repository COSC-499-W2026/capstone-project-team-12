"""Factories for verification tables (photo_attempts). Registered in conftest.py through
`pytest_plugins`, next to content_fixtures, because conftest.py is already over 500 lines."""

from collections.abc import Callable
from hashlib import sha256
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.game import SessionLocation
from app.models.verification import PhotoAttempt


@pytest.fixture
def make_photo_attempt(db: Session) -> Callable[..., PhotoAttempt]:
    """Attempt 1 of the session location, still `processing`, for a new file without camera EXIF.

    Pass any column to override a default, e.g. status=PhotoAttemptStatus.DONE with a result.
    """

    def make(location: SessionLocation, **fields: Any) -> PhotoAttempt:
        values: dict[str, Any] = {
            "session_location_id": location.id,
            "attempt_no": 1,
            "photo_path": f"{uuid4().hex}.jpg",
            "content_sha256": sha256(uuid4().bytes).hexdigest(),
            "job_id": uuid4(),
            "has_camera_exif": False,
        }
        attempt = PhotoAttempt(**(values | fields))
        db.add(attempt)
        db.flush()
        return attempt

    return make
