"""Teacher classroom and access-code management endpoints."""

from datetime import datetime
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.db import get_db
from app.engagement.interface import (
    create_class_record,
    list_staff_classes,
    require_class_owner,
    save_access_code_with_retry,
    semester_expiration,
    update_class_record,
)
from app.errors import error_responses
from app.models.accounts import User
from app.models.enums import ClassStaffRole, Language, UserRole

teacher_classes_router = APIRouter(
    prefix="/teacher/classes",
    tags=["teacher-classes"],
)

ClassName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
    ),
]


class CreateClassRequest(BaseModel):
    name: ClassName
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

    name: ClassName | None = None
    leaderboard_enabled: bool | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "UpdateClassRequest":
        """Require at least one non-null setting to update."""
        if not self.model_fields_set:
            raise ValueError("At least one classroom setting must be provided.")

        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Classroom name cannot be null.")

        if (
            "leaderboard_enabled" in self.model_fields_set
            and self.leaderboard_enabled is None
        ):
            raise ValueError("Leaderboard setting cannot be null.")

        return self


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
