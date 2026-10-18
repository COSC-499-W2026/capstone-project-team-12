"""Teacher classroom and access-code management endpoints."""

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
    create_class_record,
    list_staff_classes,
    save_access_code_with_retry,
    semester_expiration,
)
from app.errors import error_responses
from app.models.accounts import User
from app.models.enums import ClassStaffRole, Language, UserRole

teacher_classes_router = APIRouter(
    prefix="/teacher/classes",
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
