"""Teacher classroom and access-code management endpoints."""

import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import (
    ACCESS_CODE_ALPHABET,
    ACCESS_CODE_LENGTH,
    AccessCode,
    Class,
)
from app.models.enums import ClassStaffRole, Language, UserRole

teacher_classes_router = APIRouter(
    prefix="/teacher/classes",
    tags=["teacher-classes"],
)

access_code_router = APIRouter(
    prefix="/teacher/access-codes",
    tags=["teacher-access-codes"],
)


# ---------------------------------------------------------
# Request and response schemas
# ---------------------------------------------------------


class CreateClassRequest(BaseModel):
    name: str = Field(min_length=1)
    semester: str = Field(pattern=r"^[0-9]{4}(W1|W2|S1|S2)$")
    target_language: Language


class CreateClassResponse(BaseModel):
    id: UUID
    name: str
    semester: str
    target_language: Language
    access_code: str


class ClassListItem(BaseModel):
    id: UUID
    name: str
    semester: str
    target_language: Language
    leaderboard_enabled: bool
    archived_at: datetime | None
    staff_role: ClassStaffRole


class UpdateClassRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    leaderboard_enabled: bool | None = None


class UpdateClassResponse(BaseModel):
    id: UUID
    name: str
    semester: str
    target_language: Language
    leaderboard_enabled: bool


class CreateAccessCodeRequest(BaseModel):
    max_uses: int | None = Field(default=None, gt=0)
    expires_at: datetime | None = None


class AccessCodeResponse(BaseModel):
    id: UUID
    class_id: UUID
    code: str
    expires_at: datetime
    is_active: bool
    max_uses: int | None
    uses_count: int


class UpdateAccessCodeRequest(BaseModel):
    is_active: bool = False


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def semester_expiration(semester: str) -> datetime:
    """Return the exclusive UTC expiration boundary for a UBC semester."""
    year = int(semester[:4])
    term = semester[4:]

    if term == "W1":
        return datetime(year + 1, 1, 1, tzinfo=UTC)

    if term == "W2":
        return datetime(year + 1, 5, 1, tzinfo=UTC)

    if term == "S1":
        return datetime(year, 7, 1, tzinfo=UTC)

    if term == "S2":
        return datetime(year, 9, 1, tzinfo=UTC)

    raise ValueError("Invalid semester")


def generate_access_code(db: Session) -> str:
    """Generate a secure eight-character code not currently in the database."""
    for _ in range(20):
        code = "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH))

        existing = db.scalar(select(AccessCode.id).where(AccessCode.code == code))

        if existing is None:
            return code

    raise RuntimeError("Unable to generate a unique access code")


def save_access_code_with_retry(
    db: Session,
    *,
    class_id: UUID,
    created_by: UUID,
    expires_at: datetime,
    max_uses: int | None = None,
) -> AccessCode:
    """Insert an access code, retrying unique-code collisions."""
    for _ in range(20):
        code = generate_access_code(db)

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
            # Only retry collisions on the access-code unique constraint.
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise

            constraint = getattr(
                getattr(exc.orig, "diag", None),
                "constraint_name",
                None,
            )

            if constraint != "access_codes_code_key":
                raise

    raise RuntimeError("Unable to generate a unique access code")


def require_code_manager(
    class_id: UUID,
    user: User,
    db: Session,
) -> Class:
    """Require classroom owner or teaching-assistant privileges."""
    school_class = db.get(Class, class_id)

    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    staff_role = db.scalar(
        select(ClassStaff.role).where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == user.id,
        )
    )

    if staff_role not in (
        ClassStaffRole.OWNER,
        ClassStaffRole.ASSISTANT,
    ):
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    return school_class


# ---------------------------------------------------------
# POST /api/v1/teacher/classes
# ---------------------------------------------------------


@teacher_classes_router.post(
    "",
    response_model=CreateClassResponse,
    status_code=201,
    operation_id="createClass",
    summary="Create a classroom",
    description=(
        "Create a classroom with a fixed target language, "
        "assign the creator as owner, and generate an initial access code."
    ),
)
def create_class(
    body: CreateClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CreateClassResponse:
    """Create a classroom and assign its creator as owner."""
    if user.role != UserRole.STAFF:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    # Prevent creating classrooms with already-expired initial access codes.
    expires_at = semester_expiration(body.semester)

    if expires_at <= datetime.now(UTC):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Cannot create a classroom for a semester that has already ended.",
        )

    school_class = Class(
        name=body.name,
        semester=body.semester,
        target_language=body.target_language,
    )

    db.add(school_class)
    db.flush()

    db.add(
        ClassStaff(
            class_id=school_class.id,
            user_id=user.id,
            role=ClassStaffRole.OWNER,
        )
    )

    access_code = save_access_code_with_retry(
        db,
        class_id=school_class.id,
        created_by=user.id,
        expires_at=expires_at,
    )
    code = access_code.code

    response = CreateClassResponse(
        id=school_class.id,
        name=school_class.name,
        semester=school_class.semester,
        target_language=Language(school_class.target_language),
        access_code=code,
    )

    db.commit()
    return response


# ---------------------------------------------------------
# GET /api/v1/teacher/classes
# ---------------------------------------------------------


@teacher_classes_router.get(
    "",
    response_model=list[ClassListItem],
    operation_id="listTeacherClasses",
    summary="List classrooms for a teacher or TA",
    description="Return classrooms owned or assisted by the authenticated user.",
)
def list_teacher_classes(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ClassListItem]:
    """List only classrooms assigned to the current user."""
    rows = db.execute(
        select(Class, ClassStaff.role)
        .join(ClassStaff, ClassStaff.class_id == Class.id)
        .where(ClassStaff.user_id == user.id)
        .order_by(Class.created_at.desc(), Class.id)
    ).all()

    return [
        ClassListItem(
            id=school_class.id,
            name=school_class.name,
            semester=school_class.semester,
            target_language=Language(school_class.target_language),
            leaderboard_enabled=school_class.leaderboard_enabled,
            archived_at=school_class.archived_at,
            staff_role=ClassStaffRole(role),
        )
        for school_class, role in rows
    ]


# ---------------------------------------------------------
# PATCH /api/v1/teacher/classes/{class_id}
# ---------------------------------------------------------


@teacher_classes_router.patch(
    "/{class_id}",
    response_model=UpdateClassResponse,
    operation_id="updateClass",
    summary="Update classroom settings",
    description=(
        "Allow the classroom owner to update the name and leaderboard setting. "
        "The target language cannot be changed."
    ),
)
def update_class(
    class_id: UUID,
    body: UpdateClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UpdateClassResponse:
    """Update mutable classroom settings without changing the target language."""
    school_class = db.get(Class, class_id)

    if school_class is None:
        raise AppError(ErrorCode.NOT_FOUND)

    owner = db.scalar(
        select(ClassStaff.role).where(
            ClassStaff.class_id == class_id,
            ClassStaff.user_id == user.id,
        )
    )

    if owner != ClassStaffRole.OWNER:
        raise AppError(ErrorCode.FORBIDDEN_ROLE)

    if school_class.archived_at is not None:
        raise AppError(ErrorCode.CLASS_ARCHIVED)

    if body.name is not None:
        school_class.name = body.name

    if body.leaderboard_enabled is not None:
        school_class.leaderboard_enabled = body.leaderboard_enabled

    db.commit()
    db.refresh(school_class)

    return UpdateClassResponse(
        id=school_class.id,
        name=school_class.name,
        semester=school_class.semester,
        target_language=Language(school_class.target_language),
        leaderboard_enabled=school_class.leaderboard_enabled,
    )


# ---------------------------------------------------------
# POST /api/v1/teacher/classes/{class_id}/access-codes
# ---------------------------------------------------------


@teacher_classes_router.post(
    "/{class_id}/access-codes",
    response_model=AccessCodeResponse,
    status_code=201,
    operation_id="createAccessCode",
    summary="Create a classroom access code",
    description=(
        "Allow a classroom owner or teaching assistant to generate "
        "an additional access code with optional expiration and usage limits."
    ),
)
def create_access_code(
    class_id: UUID,
    body: CreateAccessCodeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AccessCodeResponse:
    """Generate an additional access code for a classroom."""
    school_class = require_code_manager(class_id, user, db)

    expires_at = (
        body.expires_at
        if body.expires_at is not None
        else semester_expiration(school_class.semester)
    )

    if expires_at.tzinfo is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must include a timezone.",
        )

    if expires_at <= datetime.now(UTC):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Expiration must be in the future.",
        )

    access_code = save_access_code_with_retry(
        db,
        class_id=school_class.id,
        created_by=user.id,
        expires_at=expires_at,
        max_uses=body.max_uses,
    )
    db.commit()
    db.refresh(access_code)

    return AccessCodeResponse.model_validate(
        access_code,
        from_attributes=True,
    )


# ---------------------------------------------------------
# PATCH /api/v1/teacher/access-codes/{code_id}
# ---------------------------------------------------------


@access_code_router.patch(
    "/{code_id}",
    response_model=AccessCodeResponse,
    operation_id="updateAccessCode",
    summary="Deactivate an access code",
    description=(
        "Allow a classroom owner or teaching assistant to deactivate an existing access code."
    ),
)
def update_access_code(
    code_id: UUID,
    body: UpdateAccessCodeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AccessCodeResponse:
    """Deactivate an access code."""
    access_code = db.get(AccessCode, code_id)

    if access_code is None:
        raise AppError(ErrorCode.NOT_FOUND)

    require_code_manager(access_code.class_id, user, db)

    if body.is_active:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "This endpoint only supports deactivating access codes.",
        )

    access_code.is_active = False

    db.commit()
    db.refresh(access_code)

    return AccessCodeResponse.model_validate(
        access_code,
        from_attributes=True,
    )
