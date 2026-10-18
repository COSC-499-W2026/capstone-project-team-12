"""Classroom access-code management business logic.

Public callers import through app.engagement.interface.
The API layer owns transaction commits.
"""

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


def create_access_code_record(
    *,
    class_id: UUID,
    created_by: UUID,
    expires_at: datetime | None,
    max_uses: int | None,
    db: Session,
) -> AccessCode:
    """Create an additional code for an existing, active classroom."""
    school_class = db.get(Class, class_id)

    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    expiration = (
        expires_at if expires_at is not None else semester_expiration(school_class.semester)
    )

    if expiration.tzinfo is None or expiration.utcoffset() is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must include a timezone.",
        )

    if expiration <= datetime.now(UTC):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must be in the future.",
        )

    return save_access_code_with_retry(
        db=db,
        class_id=class_id,
        created_by=created_by,
        expires_at=expiration,
        max_uses=max_uses,
    )


def deactivate_access_code_record(
    *,
    code_id: UUID,
    db: Session,
) -> AccessCode:
    """Deactivate an access code without deleting its history.

    Repeated deactivation is allowed. The API layer is responsible
    for checking the requesting user's classroom permissions
    and committing the transaction.
    """
    access_code = db.get(AccessCode, code_id)

    if access_code is None:
        raise AppError(ErrorCode.NOT_FOUND)

    access_code.is_active = False
    db.flush()

    return access_code
