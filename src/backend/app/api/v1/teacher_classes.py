"""Teacher and teaching assistant classroom listing endpoints."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.engagement.interface import list_staff_classes
from app.errors import error_responses
from app.models.accounts import User
from app.models.enums import ClassStaffRole, Language

teacher_classes_router = APIRouter(
    prefix="/teacher/classes",
    tags=["teacher-classes"],
)


class ClassListItem(BaseModel):
    id: UUID
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
    summary="List classrooms for a teacher or TA",
    description="Return classrooms owned or assisted by the authenticated user.",
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def list_teacher_classes(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ClassListItem]:
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
