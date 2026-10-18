"""Classroom access-code management business logic.

Public callers import through app.engagement.interface.
The API layer owns transaction commits.
"""

import secrets
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import violated_constraint
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


def save_access_code_with_retry(
    class_id: UUID,
    created_by: UUID,
    expires_at: datetime,
    db: Session,
    *,
    max_uses: int | None = None,
) -> AccessCode:
    """Insert an access code, retrying only code collisions.

    Uses a nested transaction to roll back a failed insertion without
    rolling back the caller's entire transaction.

    The API layer remains responsible for committing the transaction.
    """
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
            if violated_constraint(exc) != CODE_UNIQUE_CONSTRAINT:
                raise

    raise RuntimeError("Unable to generate a unique access code")
