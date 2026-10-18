"""Classroom access-code management business logic.

Public callers import through app.engagement.interface.
The API layer owns transaction commits.
"""

import secrets
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.engagement import (
    ACCESS_CODE_ALPHABET,
    ACCESS_CODE_LENGTH,
    AccessCode,
)

CODE_UNIQUE_CONSTRAINT = "uq_access_codes_code"
MAX_CODE_ATTEMPTS = 20


def generate_access_code() -> str:
    """Generate a cryptographically secure access code."""
    return "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH))


def _is_code_collision(exc: IntegrityError) -> bool:
    """Identify an access-code uniqueness violation."""
    original = exc.orig
    diagnostics = getattr(original, "diag", None)

    return (
        getattr(original, "sqlstate", None) == "23505"
        and getattr(diagnostics, "constraint_name", None) == CODE_UNIQUE_CONSTRAINT
    )


def save_access_code_with_retry(
    *,
    db: Session,
    class_id: UUID,
    created_by: UUID,
    expires_at: datetime,
    max_uses: int | None = None,
) -> AccessCode:
    """Insert an access code, retrying only code collisions."""
    for _ in range(MAX_CODE_ATTEMPTS):
        code = generate_access_code()

        try:
            with db.begin_nested():
                access_code = AccessCode(
                    class_id=class_id,
                    code=code,
                    expires_at=expires_at,
                    max_uses=max_uses,
                    created_by=created_by,
                )
                db.add(access_code)
                db.flush()

            return access_code

        except IntegrityError as exc:
            if not _is_code_collision(exc):
                raise

    raise RuntimeError("Unable to generate a unique access code")
