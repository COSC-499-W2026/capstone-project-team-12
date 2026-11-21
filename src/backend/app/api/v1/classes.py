"""Classroom endpoints for joining and managing classes."""

import csv
from dataclasses import asdict
from datetime import date, datetime
from http import HTTPStatus
from io import StringIO
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.accounts.interface import (
    get_current_user,
    is_class_staff,
    require_class_role,
    require_code_class_role,
    require_staff,
)
from app.db import get_db
from app.engagement.interface import (
    archive_class,
    create_class,
    deactivate_access_code,
    generate_access_code,
    get_class_leaderboard,
    get_class_location_analytics,
    get_class_participation,
    get_class_student_analytics,
    list_staff_classes,
    remove_student,
    require_active_enrolment,
    restore_student,
    update_class_settings,
)
from app.errors import AppError, ErrorCode, error_responses
from app.game.interface import end_open_sessions
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Difficulty, DifficultySuggestion, Language

leaderboard_router = APIRouter(prefix="/classes", tags=["classes"])
teacher_classes_router = APIRouter(
    prefix="/teacher/classes",
    tags=["classes"],
)
teacher_access_codes_router = APIRouter(
    prefix="/teacher/access-codes",
    tags=["classes"],
)


class LeaderboardItem(BaseModel):
    """One student's position in the classroom leaderboard."""

    rank: int
    username: str
    total_points: int
    is_me: bool = Field(description="Whether this entry belongs to the signed-in user.")


class ClassLeaderboardResponse(BaseModel):
    """Paginated classroom leaderboard."""

    items: list[LeaderboardItem]
    page: int
    page_size: int
    total: int
    has_next_page: bool


@leaderboard_router.get(
    "/{class_id}/leaderboard",
    response_model=ClassLeaderboardResponse,
    operation_id="getClassLeaderboard",
    summary="Get classroom leaderboard",
    description=(
        "Rank students by points earned in this class, using active enrolments for open classes. "
        "Practice points and points from other classes are excluded. "
        "Students may view their own class leaderboard; class owners and "
        "teaching assistants may view classes they manage, including final standings for "
        "archived classes. Returns 403 LEADERBOARD_DISABLED when the leaderboard is turned off. "
        "Students whose enrolment ended when the class was archived receive 409 CLASS_ARCHIVED."
    ),
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.CONFLICT,
    ),
)
def get_class_leaderboard_endpoint(
    class_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ClassLeaderboardResponse:
    """Return class rankings to enrolled students and assigned staff."""
    staff = is_class_staff(user.id, class_id, db)
    if not staff:
        require_active_enrolment(user.id, class_id, db)

    school_class = db.get_one(Class, class_id)
    if not school_class.leaderboard_enabled:
        raise AppError(ErrorCode.LEADERBOARD_DISABLED)

    entries = get_class_leaderboard(
        class_id,
        db,
        viewer_id=user.id,
        include_archived_enrolments=staff and school_class.archived_at is not None,
    )
    total = len(entries)
    start = (page - 1) * page_size
    return ClassLeaderboardResponse(
        items=[
            LeaderboardItem(
                rank=entry.rank,
                username=entry.username,
                total_points=entry.total_points,
                is_me=entry.is_me,
            )
            for entry in entries[start : start + page_size]
        ],
        page=page,
        page_size=page_size,
        total=total,
        has_next_page=start + page_size < total,
    )


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
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER, ClassStaffRole.ASSISTANT))],
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


class AnalyticsTotalsResponse(BaseModel):
    """Statistics for finished classroom results."""

    model_config = ConfigDict(from_attributes=True)

    finished: int
    completed: int
    failed: int
    success_rate: float
    photo_attempts: int
    average_time_seconds: float | None
    flagged_results: int
    gps_overrides: int


class LocationAnalyticsResponse(AnalyticsTotalsResponse):
    location_id: UUID
    label: str


class ClassAnalyticsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    class_stats: AnalyticsTotalsResponse
    locations: list[LocationAnalyticsResponse]


@teacher_classes_router.get(
    "/{class_id}/analytics",
    response_model=ClassAnalyticsResponse,
    operation_id="getClassLocationAnalytics",
    summary="Get classroom location analytics",
    description=(
        "Return class totals and per-location success rates, photo attempts, completion "
        "times, flags, and GPS overrides. Only completed and failed "
        "classroom location results are included. Practice sessions "
        "are excluded. Class owners and assigned teaching assistants "
        "may access statistics, including for archived classrooms."
    ),
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER, ClassStaffRole.ASSISTANT))],
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
    ),
)
def get_class_analytics_endpoint(
    class_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> ClassAnalyticsResponse:
    """Return classroom location analytics without modifying data."""
    return ClassAnalyticsResponse.model_validate(get_class_location_analytics(class_id, db))


class DifficultyHintResponse(BaseModel):
    """Advisory hint based on finished classroom results at one difficulty."""

    model_config = ConfigDict(from_attributes=True)

    difficulty: Difficulty
    finished: int
    completed: int
    failed: int
    success_rate: float | None
    suggestion: DifficultySuggestion | None


class StudentAnalyticsResponse(BaseModel):
    """Classroom-specific progress for an enrolled student."""

    model_config = ConfigDict(from_attributes=True)

    student_id: UUID
    username: str
    is_active_enrollment: bool
    joined_at: datetime
    left_at: datetime | None
    sessions: int
    completed_locations: int
    failed_locations: int
    classroom_points: int
    success_rate: float
    last_activity_at: datetime | None
    streak_days: int
    flags: list[str]
    difficulty_hints: list[DifficultyHintResponse]


@teacher_classes_router.get(
    "/{class_id}/students",
    response_model=list[StudentAnalyticsResponse],
    operation_id="getClassStudentAnalytics",
    summary="Get individual student statistics for a classroom",
    description=(
        "Return classroom-specific progress for current and former students, "
        "including sessions, completed and failed locations, points, and "
        "success rates, plus global streaks and classroom flags. "
        "Difficulty hints use all finished results at each level in this class "
        "and require at least five results. "
        "Class owners and assigned teaching assistants may access these "
        "statistics, including for archived classrooms."
    ),
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER, ClassStaffRole.ASSISTANT))],
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
    ),
)
def get_class_students_endpoint(
    class_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> list[StudentAnalyticsResponse]:
    """Return individual classroom student statistics without modifying data."""
    statistics = get_class_student_analytics(class_id, db)

    return [StudentAnalyticsResponse.model_validate(item) for item in statistics]


@teacher_classes_router.get(
    "/{class_id}/participation.csv",
    operation_id="exportClassParticipation",
    response_class=Response,
    summary="Export classroom participation marks",
    description=(
        "Download a CSV of participation marks for all current and former enrollees, including "
        "students with zero activity, over an inclusive date range. "
        "Participation counts distinct completed (location, difficulty) pairs in classroom games; "
        "replays of the same pair and Practice games do not increase the mark. "
        "The percentage is capped at 100. Class owners and assigned teaching assistants may export "
        "marks, including for archived classrooms. The export includes sessions, active days, "
        "class points and badge unlocks for the selected period."
    ),
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER, ClassStaffRole.ASSISTANT))],
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )
    | {200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_class_participation_endpoint(
    class_id: UUID,
    start_date: date,
    end_date: date,
    db: Annotated[Session, Depends(get_db)],
    target_locations: Annotated[int, Query(ge=1)] = 10,
) -> Response:
    """Return a CSV export of classroom participation marks."""
    participation = get_class_participation(
        class_id=class_id,
        db=db,
        start_date=start_date,
        end_date=end_date,
        target_locations=target_locations,
    )

    output = StringIO(newline="")
    columns = (
        "student_id username enrollment_status is_active_enrollment distinct_completed_locations "
        "target_locations participation_percent sessions active_days points badges"
    )
    writer = csv.DictWriter(output, fieldnames=columns.split())
    writer.writeheader()
    for item in participation:
        username = item.username
        if username.lstrip().startswith(("=", "+", "-", "@")) or username.startswith(
            ("\t", "\r", "\n")
        ):
            username = "'" + username
        writer.writerow(asdict(item) | {"username": username})

    filename = f"class-{class_id}-participation-{start_date}-to-{end_date}.csv"
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    dependencies=[Depends(require_staff)],
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
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER))],
)
def update_class_endpoint(
    class_id: UUID,
    request: UpdateClassRequest,
    db: Annotated[Session, Depends(get_db)],
) -> UpdateClassResponse:
    """Update owner-editable classroom settings in one transaction."""
    try:
        school_class = update_class_settings(
            class_id=class_id,
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
    dependencies=[Depends(require_code_class_role(ClassStaffRole.OWNER, ClassStaffRole.ASSISTANT))],
)
def deactivate_class_access_code(
    code_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Deactivate an access code in the caller-owned transaction."""
    try:
        deactivate_access_code(code_id=code_id, db=db)
        db.commit()
    except Exception:
        db.rollback()
        raise


@teacher_classes_router.post(
    "/{class_id}/archive",
    status_code=HTTPStatus.NO_CONTENT,
    operation_id="archiveClass",
    summary="Archive a classroom",
    description=(
        "Only the classroom owner can archive it. Archiving ends active enrolments "
        "and open class games, preserves earned points, and prevents future joins "
        "and games. Repeating the request succeeds."
    ),
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER))],
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.NOT_FOUND,
    ),
)
def archive_class_endpoint(
    class_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Archive the classroom and end its games in one transaction."""
    try:
        archive_class(class_id, db)
        end_open_sessions(class_id, db)
        db.commit()
    except Exception:
        db.rollback()
        raise


@teacher_classes_router.delete(
    "/{class_id}/students/{student_id}",
    status_code=HTTPStatus.NO_CONTENT,
    operation_id="removeClassStudent",
    summary="Remove a student from a classroom",
    description=(
        "Only the owner can remove a student. Removal ends the student's open games in this "
        "class, preserves earned points, and prevents the student from rejoining with a code "
        "until restored. Archived classes cannot be modified."
    ),
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER))],
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def remove_class_student_endpoint(
    class_id: UUID,
    student_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    try:
        if remove_student(student_id, class_id, db):
            end_open_sessions(class_id, db, student_id=student_id)
        db.commit()
    except Exception:
        db.rollback()
        raise


@teacher_classes_router.post(
    "/{class_id}/students/{student_id}/restore",
    status_code=HTTPStatus.NO_CONTENT,
    operation_id="restoreClassStudent",
    summary="Restore a removed student",
    description=(
        "Only the owner can restore a removed student. Restores the original enrolment without "
        "consuming an access code or changing the student's class selection. Returns 409 if "
        "the class is archived, the student assists it, or the student already has an active "
        "class in the same language."
    ),
    dependencies=[Depends(require_class_role(ClassStaffRole.OWNER))],
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def restore_class_student_endpoint(
    class_id: UUID,
    student_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    try:
        restore_student(student_id, class_id, db)
        db.commit()
    except Exception:
        db.rollback()
        raise
