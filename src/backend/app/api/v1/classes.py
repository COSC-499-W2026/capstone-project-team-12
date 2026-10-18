"""Classroom endpoints for joining and managing classes."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.engagement.interface import validate_code_and_enrol
from app.errors import error_responses
from app.models.accounts import User
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import Language

classes_router = APIRouter(prefix="/me/classes", tags=["classes"])


class JoinClassRequest(BaseModel):
    """Access code provided by a student joining a classroom."""

    access_code: str = Field(
        min_length=1,
        max_length=32,
        description=("Eight-character classroom access code. Spaces and hyphens are accepted."),
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
        "Successful enrolment also makes it the student's current class. "
        "Repeating a successful request for the same class returns success "
        "without consuming another access-code use."
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
    """Join a classroom and select it in one transaction.

    Lock the student's database row before checking enrolment. This
    serializes concurrent join requests for the same student when using
    PostgreSQL, preventing competing requests from both passing the
    one-active-class-per-language check.

    Repeating a successful request for the same access code and class is
    idempotent: it returns the existing class without consuming another
    access-code use.
    """
    db.scalar(select(User).where(User.id == user.id).with_for_update())

    normalized_code = "".join(request.access_code.upper().split()).replace("-", "")

    # A retry may arrive after the original transaction committed, even
    # if the code has since expired or reached its usage limit.
    # If this code belongs to the student's already-active class, return
    # that successful result without incrementing the usage count again.
    existing_code = db.scalar(select(AccessCode).where(AccessCode.code == normalized_code))

    if existing_code is not None:
        existing_enrolment = db.scalar(
            select(ClassEnrollment).where(
                ClassEnrollment.student_id == user.id,
                ClassEnrollment.class_id == existing_code.class_id,
                ClassEnrollment.is_active.is_(True),
            )
        )

        if existing_enrolment is not None:
            school_class = db.get(Class, existing_code.class_id)

            # validate_code_and_enrol() checks class existence. This branch
            # handles only an already-active enrolment for the same class.
            if school_class is not None and school_class.archived_at is None:
                user.current_class_id = school_class.id
                db.commit()

                return JoinClassResponse(
                    class_id=school_class.id,
                    language=Language(school_class.target_language),
                )

    class_id = validate_code_and_enrol(
        user.id,
        request.access_code,
        db,
    )

    # The enrolment function has already checked that this class exists.
    school_class = db.get(Class, class_id)
    assert school_class is not None, "The enrolled class must exist in the same transaction."

    user.current_class_id = class_id

    db.commit()

    return JoinClassResponse(
        class_id=class_id,
        language=Language(school_class.target_language),
    )
