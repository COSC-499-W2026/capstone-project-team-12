"""Classroom endpoints for joining and managing classes."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.engagement.interface import (
    create_class,
    deactivate_access_code,
    generate_access_code,
    get_class_language,
    leave_class,
    list_staff_classes,
    update_class_settings,
    validate_code_and_enrol,
)
from app.errors import AppError, ErrorCode, error_responses
from app.game.interface import end_open_sessions
from app.models.accounts import User
from app.models.enums import ClassStaffRole, Language

classes_router = APIRouter(prefix="/me/classes", tags=["classes"])
teacher_classes_router = APIRouter(
    prefix="/teacher/classes",
    tags=["classes"],
)
teacher_access_codes_router = APIRouter(
    prefix="/teacher/access-codes",
    tags=["classes"],
)


class JoinClassRequest(BaseModel):
    """Access code provided by a student joining a classroom."""

    access_code: str = Field(
        min_length=1,
        max_length=32,
        description="Eight-character classroom access code. Spaces and hyphens are accepted.",
        examples=["K7PM-3QXD"],
    )


class JoinClassResponse(BaseModel):
    """Classroom selected after successful enrolment."""

    class_id: UUID
    language: Language


class CreateClassRequest(BaseModel):
    """Details for a new classroom and its initial access code."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Classroom name.",
        examples=["JAPN 101 - Fall 2026"],
    )
    semester: str = Field(
        pattern=r"^[0-9]{4}(W1|W2|S1|S2)$",
        description="Academic semester identifier.",
        examples=["2026W1"],
    )
    language: Language = Field(
        description="Fixed target language for the classroom.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "Optional timezone-aware expiration for the initial access code. "
            "Defaults to the end of the classroom's semester."
        ),
    )
    max_uses: int | None = Field(
        default=None,
        gt=0,
        description="Optional maximum number of uses for the initial access code.",
    )


class CreateClassResponse(BaseModel):
    """Created classroom and its initial enrolment code."""

    class_id: UUID
    name: str
    semester: str
    language: Language
    access_code: str


class UpdateClassRequest(BaseModel):
    """Owner-editable classroom settings.

    Reject unsupported fields, including target language and semester.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        max_length=255,
        description="Optional new classroom name.",
        examples=["JAPN 101 - Updated"],
    )
    leaderboard_enabled: bool | None = Field(
        default=None,
        description="Whether the classroom leaderboard is enabled.",
    )


class UpdateClassResponse(BaseModel):
    """Classroom settings after an update."""

    class_id: UUID
    name: str
    semester: str
    target_language: Language
    leaderboard_enabled: bool


class CreateAccessCodeRequest(BaseModel):
    """Settings for an additional classroom access code."""

    expires_at: datetime | None = Field(
        default=None,
        description="Timezone-aware expiration; defaults to semester end.",
    )
    max_uses: int | None = Field(
        default=None,
        gt=0,
        description="Optional maximum number of uses.",
    )


class CreateAccessCodeResponse(BaseModel):
    """Newly generated classroom access code."""

    id: UUID
    class_id: UUID
    code: str
    expires_at: datetime
    max_uses: int | None


@teacher_classes_router.post(
    "/{class_id}/access-codes",
    response_model=CreateAccessCodeResponse,
    status_code=HTTPStatus.CREATED,
    operation_id="createClassAccessCode",
    summary="Generate an additional classroom access code",
    description=(
        "Class owners and teaching assistants may generate additional "
        "access codes. Archived classrooms cannot be modified. "
        "Expiration defaults to the end of the semester."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
def create_class_access_code(
    class_id: UUID,
    request: CreateAccessCodeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CreateAccessCodeResponse:
    """Generate and persist an additional classroom access code."""
    try:
        access_code = generate_access_code(
            class_id=class_id,
            creator=user,
            expires_at=request.expires_at,
            db=db,
            max_uses=request.max_uses,
        )

        response = CreateAccessCodeResponse(
            id=access_code.id,
            class_id=access_code.class_id,
            code=access_code.code,
            expires_at=access_code.expires_at,
            max_uses=access_code.max_uses,
        )

        db.commit()
        return response

    except Exception:
        db.rollback()
        raise


class ClassListItem(BaseModel):
    """Classroom visible to its owner or teaching assistant."""

    class_id: UUID
    name: str
    semester: str
    target_language: Language
    leaderboard_enabled: bool
    archived_at: datetime | None
    staff_role: ClassStaffRole


@teacher_classes_router.get(
    "",
    response_model=list[ClassListItem],
    operation_id="listTeacherClasses",
    summary="List classrooms for a teacher or teaching assistant",
    description=(
        "Return classrooms owned or assisted by the authenticated user, "
        "including archived classrooms. Other classrooms are not visible."
    ),
    responses=error_responses(HTTPStatus.UNAUTHORIZED),
)
def list_teacher_classes(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ClassListItem]:
    """List classrooms without modifying them."""
    rows = list_staff_classes(user.id, db)
    return [
        ClassListItem(
            class_id=school_class.id,
            name=school_class.name,
            semester=school_class.semester,
            target_language=Language(school_class.target_language),
            leaderboard_enabled=school_class.leaderboard_enabled,
            archived_at=school_class.archived_at,
            staff_role=role,
        )
        for school_class, role in rows
    ]


@teacher_classes_router.post(
    "",
    response_model=CreateClassResponse,
    status_code=HTTPStatus.CREATED,
    operation_id="createClass",
    summary="Create a classroom",
    description=(
        "Create a classroom with a fixed target language and generate "
        "its initial eight-character access code. Only active staff "
        "accounts may create classrooms. The classroom, owner assignment, "
        "and initial access code are saved in one transaction. "
        "If no expiration is provided, the access code expires at the "
        "end of the classroom's semester."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    ),
)
def create_class_endpoint(
    request: CreateClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CreateClassResponse:
    """Create a classroom and its initial access code atomically."""
    try:
        school_class = create_class(
            teacher=user,
            name=request.name,
            semester=request.semester,
            language=request.language,
            db=db,
        )

        access_code = generate_access_code(
            class_id=school_class.id,
            creator=user,
            expires_at=request.expires_at,
            db=db,
            max_uses=request.max_uses,
        )

        response = CreateClassResponse(
            class_id=school_class.id,
            name=school_class.name,
            semester=school_class.semester,
            language=Language(school_class.target_language),
            access_code=access_code.code,
        )

        db.commit()
        return response

    except Exception:
        db.rollback()
        raise


@teacher_classes_router.patch(
    "/{class_id}",
    response_model=UpdateClassResponse,
    operation_id="updateClass",
    summary="Update classroom settings",
    description=(
        "Allow the classroom owner to update its name and leaderboard "
        "setting. The target language and semester are fixed and cannot "
        "be changed. Teaching assistants and students cannot update "
        "classroom settings. Archived classrooms are read-only."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
)
def update_class_endpoint(
    class_id: UUID,
    request: UpdateClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UpdateClassResponse:
    """Update owner-editable classroom settings in one transaction."""
    try:
        school_class = update_class_settings(
            class_id=class_id,
            actor=user,
            db=db,
            name=request.name,
            leaderboard_enabled=request.leaderboard_enabled,
        )

        response = UpdateClassResponse(
            class_id=school_class.id,
            name=school_class.name,
            semester=school_class.semester,
            target_language=Language(school_class.target_language),
            leaderboard_enabled=school_class.leaderboard_enabled,
        )

        db.commit()
        return response

    except Exception:
        db.rollback()
        raise


@teacher_access_codes_router.patch(
    "/{code_id}",
    status_code=HTTPStatus.NO_CONTENT,
    operation_id="deactivateClassAccessCode",
    summary="Deactivate a classroom access code",
    description=(
        "Allow classroom owners and teaching assistants to deactivate "
        "an access code without deleting it. Existing enrolments are preserved. "
        "Archived classrooms cannot be modified."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
    ),
)
def deactivate_class_access_code(
    code_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Deactivate an access code in the caller-owned transaction."""
    try:
        deactivate_access_code(
            code_id=code_id,
            actor=user,
            db=db,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


@classes_router.post(
    "/join",
    response_model=JoinClassResponse,
    operation_id="joinClass",
    summary="Join a classroom",
    description=(
        "Enrol the authenticated student using a classroom access code. "
        "A student may have only one active classroom per language. "
        "Students who voluntarily left may rejoin, but students removed "
        "by a teacher cannot. Teachers cannot join as students, and "
        "teaching assistants cannot join classes they assist. "
        "Successful enrolment also makes it the student's current class. "
        "Repeating a successful request with the original access code "
        "returns success without consuming another use, even if the code "
        "has expired or reached its usage limit. Deactivated codes are rejected."
    ),
    responses=error_responses(
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.CONFLICT,
    ),
)
def join_class(
    request: JoinClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> JoinClassResponse:
    """Join a classroom and select it in one transaction."""
    class_id = validate_code_and_enrol(
        user.id,
        request.access_code,
        db,
    )

    language = get_class_language(class_id, db)

    if language is None:
        raise AppError(ErrorCode.INTERNAL_ERROR)

    user.current_class_id = class_id
    db.commit()

    return JoinClassResponse(
        class_id=class_id,
        language=language,
    )


@classes_router.post(
    "/{class_id}/leave",
    status_code=HTTPStatus.NO_CONTENT,
    operation_id="leaveClass",
    summary="Leave a classroom",
    description=(
        "Leave an actively enrolled classroom. "
        "The student may rejoin later using a valid access code. "
        "If this was the student's current class, another active class "
        "is selected when available; otherwise, the selection is cleared. "
        "Leaving ends any open (active or paused) class game sessions."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
    ),
)
def leave_class_endpoint(
    class_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Leave a classroom and close its open games in one transaction."""
    try:
        leave_class(user.id, class_id, db)
        end_open_sessions(class_id, db, student_id=user.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
