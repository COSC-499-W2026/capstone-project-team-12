"""Teacher classroom and access-code management endpoints."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user, require_class_role
from app.db import get_db
from app.engagement.interface import (
    create_access_code_record,
    create_class_record,
    deactivate_access_code_record,
    list_staff_classes,
    require_class_owner,
    save_access_code_with_retry,
    semester_expiration,
    update_class_record,
)
from app.errors import AppError, ErrorCode, error_responses
from app.models.accounts import User
from app.models.engagement import AccessCode
from app.models.enums import ClassStaffRole, Language, UserRole

teacher_classes_router = APIRouter(
    prefix="/teacher/classes",
    tags=["teacher-classes"],
)

access_code_router = APIRouter(
    prefix="/teacher/access-codes",
    tags=["teacher-classes"],
)


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
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
def create_class(
    body: CreateClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CreateClassResponse:
    """Create a classroom and its initial access code."""
    school_class = create_class_record(
        name=body.name,
        semester=body.semester,
        target_language=body.target_language,
        creator_id=user.id,
        creator_role=UserRole(user.role),
        db=db,
    )

    access_code = save_access_code_with_retry(
        db=db,
        class_id=school_class.id,
        created_by=user.id,
        expires_at=semester_expiration(body.semester),
    )

    response = CreateClassResponse(
        id=school_class.id,
        name=school_class.name,
        semester=school_class.semester,
        target_language=Language(school_class.target_language),
        access_code=access_code.code,
    )

    db.commit()
    return response


@teacher_classes_router.get(
    "",
    response_model=list[ClassListItem],
    operation_id="listTeacherClasses",
    summary="List classrooms for a teacher or TA",
    description="Return classrooms owned or assisted by the authenticated user.",
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ),
)
def list_teacher_classes(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ClassListItem]:
    """List classrooms assigned to the authenticated user."""
    rows = list_staff_classes(user.id, db)

    return [
        ClassListItem(
            id=school_class.id,
            name=school_class.name,
            semester=school_class.semester,
            target_language=Language(school_class.target_language),
            leaderboard_enabled=school_class.leaderboard_enabled,
            archived_at=school_class.archived_at,
            staff_role=role,
        )
        for school_class, role in rows
    ]


@teacher_classes_router.patch(
    "/{class_id}",
    response_model=UpdateClassResponse,
    operation_id="updateClass",
    summary="Update classroom settings",
    description=(
        "Allow the classroom owner to update the name and leaderboard setting. "
        "The target language cannot be changed."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
def update_class(
    class_id: UUID,
    body: UpdateClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UpdateClassResponse:
    """Update mutable classroom settings without changing the language."""
    require_class_owner(
        class_id=class_id,
        user_id=user.id,
        db=db,
    )

    school_class = update_class_record(
        class_id=class_id,
        name=body.name,
        leaderboard_enabled=body.leaderboard_enabled,
        db=db,
    )

    db.commit()
    db.refresh(school_class)

    return UpdateClassResponse(
        id=school_class.id,
        name=school_class.name,
        semester=school_class.semester,
        target_language=Language(school_class.target_language),
        leaderboard_enabled=school_class.leaderboard_enabled,
    )


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
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
def create_access_code(
    class_id: UUID,
    body: CreateAccessCodeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _authorized: Annotated[
        object,
        Depends(
            require_class_role(
                ClassStaffRole.OWNER,
                ClassStaffRole.ASSISTANT,
            )
        ),
    ],
) -> AccessCodeResponse:
    """Generate an additional classroom access code."""
    access_code = create_access_code_record(
        class_id=class_id,
        created_by=user.id,
        expires_at=body.expires_at,
        max_uses=body.max_uses,
        db=db,
    )

    db.commit()
    db.refresh(access_code)

    return AccessCodeResponse.model_validate(
        access_code,
        from_attributes=True,
    )


class UpdateAccessCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: Literal[False]


@access_code_router.patch(
    "/{code_id}",
    response_model=AccessCodeResponse,
    operation_id="deactivateAccessCode",
    summary="Deactivate a classroom access code",
    description=(
        "Allow the classroom owner to deactivate an access code "
        "without deleting its usage history. Repeated deactivation is allowed."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
def deactivate_access_code(
    code_id: UUID,
    body: UpdateAccessCodeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AccessCodeResponse:
    """Deactivate an access code belonging to a classroom owned by the user."""
    access_code = db.get(AccessCode, code_id)

    if access_code is None:
        raise AppError(ErrorCode.NOT_FOUND)

    require_class_owner(
        class_id=access_code.class_id,
        user_id=user.id,
        db=db,
    )

    updated_code = deactivate_access_code_record(
        code_id=code_id,
        db=db,
    )

    db.commit()
    db.refresh(updated_code)

    return AccessCodeResponse.model_validate(
        updated_code,
        from_attributes=True,
    )
