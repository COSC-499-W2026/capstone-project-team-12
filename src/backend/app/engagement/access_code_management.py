"""Classroom access-code management business logic.

Public callers import through app.engagement.interface.
The API layer owns transaction commits.
"""

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import violated_constraint
from app.engagement.class_management import semester_expiration
from app.errors import AppError, ErrorCode
from app.models.engagement import (
    ACCESS_CODE_ALPHABET,
    ACCESS_CODE_LENGTH,
    AccessCode,
    Class,
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


def create_access_code_record(
    *,
    class_id: UUID,
    created_by: UUID,
    expires_at: datetime | None,
    max_uses: int | None,
    db: Session,
) -> AccessCode:
    """Create an additional access code for an active classroom."""
    school_class = db.get(Class, class_id)

    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    semester_end = semester_expiration(school_class.semester)
    now = datetime.now(UTC)

    if semester_end <= now:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Cannot create an access code after the classroom semester has ended.",
        )

    expiration = expires_at if expires_at is not None else semester_end

    if expiration.tzinfo is None or expiration.utcoffset() is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must include a timezone.",
        )

    if expiration <= now:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must be in the future.",
        )

    if expiration > semester_end:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration cannot be later than the end of the classroom semester.",
        )

    return save_access_code_with_retry(
        db=db,
        class_id=class_id,
        created_by=created_by,
        expires_at=expiration,
        max_uses=max_uses,
    )
