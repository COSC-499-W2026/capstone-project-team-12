"""Classroom endpoints for joining and managing classes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.engagement.interface import get_class_language, validate_code_and_enrol
from app.errors import AppError, ErrorCode, ErrorResponse
from app.models.accounts import User
from app.models.enums import Language

classes_router = APIRouter(prefix="/me/classes", tags=["classes"])


class JoinClassRequest(BaseModel):
    """Access code provided by a student joining a classroom."""

    access_code: str = Field(
        min_length=1,
        description="Eight-character classroom access code. Spaces and hyphens are accepted.",
        examples=["K7PM-3QXD"],
    )


class JoinClassResponse(BaseModel):
    """Classroom selected after successful enrolment."""

    class_id: UUID
    language: Language


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
        "Successful enrolment also makes it the student's current class."
    ),
    responses={
        400: {
            "model": ErrorResponse,
            "description": ("INVALID_ACCESS_CODE: Invalid, expired, inactive or exhausted code."),
        },
        401: {
            "model": ErrorResponse,
            "description": "NOT_AUTHENTICATED: Authentication required.",
        },
        403: {
            "model": ErrorResponse,
            "description": "FORBIDDEN_ROLE or ACCOUNT_DISABLED.",
        },
        409: {
            "model": ErrorResponse,
            "description": (
                "ALREADY_IN_CLASS_FOR_LANGUAGE, REMOVED_FROM_CLASS, "
                "TA_SAME_CLASS_CONFLICT or CLASS_ARCHIVED."
            ),
        },
        422: {
            "model": ErrorResponse,
            "description": "VALIDATION_ERROR: Invalid request body.",
        },
    },
)
def join_class(
    request: JoinClassRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> JoinClassResponse:
    """Join a classroom and select it in one transaction."""
    class_id = validate_code_and_enrol(user.id, request.access_code, db)
    language = get_class_language(class_id, db)

    if language is None:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            "Class language could not be determined.",
        )

    user.current_class_id = class_id

    db.commit()

    return JoinClassResponse(class_id=class_id, language=language)
