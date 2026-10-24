"""Classroom endpoints for joining and managing classes."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.engagement.interface import (
    create_class,
    generate_access_code,
    get_class_language,
    leave_class,
    validate_code_and_enrol,
)

from app.errors import AppError, ErrorCode, error_responses
from app.game.interface import end_open_sessions
from app.models.accounts import User
from app.models.enums import Language

classes_router = APIRouter(prefix="/me/classes", tags=["classes"])


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
    expires_at: datetime = Field(
        description="Timezone-aware expiration for the initial access code.",
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


@classes_router.post(
    "",
    response_model=CreateClassResponse,
    status_code=HTTPStatus.CREATED,
    operation_id="createClass",
    summary="Create a classroom",
    description=(
        "Create a classroom with a fixed target language and generate "
        "its initial eight-character access code. Only active staff "
        "accounts may create classrooms. The classroom, owner assignment, "
        "and initial access code are saved in one transaction."
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
    leave_class(user.id, class_id, db)
    end_open_sessions(class_id, db, student_id=user.id)
    db.commit()
