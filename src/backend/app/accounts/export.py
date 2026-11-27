"""An admin's export of what is stored about one account, as a single JSON document.

The export holds the profile, the class enrolments, the classes the user assists as a TA and
the classes they own, every game with its locations, the points (the total per language and
each award), the badges and the photos not yet purged. It leaves out what only the system needs: the
password hash, refresh tokens and emailed links. The games come from game, which decides what of
them is the user's data.

No photo is kept yet: verification reads an upload and drops it, and the photo attempts store
that will keep photos until the purge job removes them is still to come. Until then `photos` is
always empty.

Each export is recorded in the admin audit log as export_user. Nothing here commits.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.staff import USER_NOT_FOUND_MESSAGE
from app.errors import AppError, ErrorCode
from app.game.interface import list_games_for_export
from app.models.accounts import ClassStaff, User
from app.models.engagement import (
    Achievement,
    Class,
    ClassEnrollment,
    PointsLedger,
    UserAchievement,
    UserLanguageStats,
)
from app.models.enums import (
    ArrivalOverride,
    AuditAction,
    ClassStaffRole,
    Difficulty,
    EnrollmentEndReason,
    Language,
    LocationStatus,
    PointsSourceType,
    SessionEndReason,
    SessionState,
    SkipReason,
    UserRole,
)
from app.services import audit


class _FromRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ExportedProfile(_FromRow):
    id: UUID
    email: str
    email_verified_at: datetime | None
    username: str
    role: UserRole
    can_manage_content: bool
    current_class_id: UUID | None = Field(
        description="The class the app follows as stored; null for Practice."
    )
    preferred_language: Language | None = Field(description="The Practice language.")
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExportedEnrolment(_FromRow):
    class_id: UUID
    class_name: str
    semester: str
    language: Language
    is_active: bool
    end_reason: EnrollmentEndReason | None
    joined_at: datetime
    left_at: datetime | None


class ExportedStaffClass(_FromRow):
    class_id: UUID
    class_name: str
    semester: str
    added_at: datetime


class ExportedSessionLocation(_FromRow):
    location_id: UUID
    position: int
    status: LocationStatus
    skip_reason: SkipReason | None
    started_at: datetime | None
    arrived_at: datetime | None
    arrival_override: ArrivalOverride | None
    attempts_used: int
    points_awarded: int
    flags: list[str]
    completed_at: datetime | None
    last_lat: float | None = Field(
        description="The last GPS position sent at this location; no track is kept."
    )
    last_lng: float | None
    last_accuracy_m: float | None
    last_ping_at: datetime | None


class ExportedSession(_FromRow):
    id: UUID
    class_id: UUID | None = Field(description="Null for a Practice game.")
    target_language: Language
    difficulty: Difficulty
    state: SessionState
    end_reason: SessionEndReason | None
    started_at: datetime
    paused_at: datetime | None
    completed_at: datetime | None
    last_activity_at: datetime
    resume_count: int
    shuffles_used: int
    total_score: int
    locations: list[ExportedSessionLocation] = Field(
        description="In game order. At a position a shuffle changed, the replaced locations come "
        "first, in no set order, and the one now in the game last."
    )


class ExportedPointsTotal(_FromRow):
    language: Language
    total_points: int


class ExportedPointsAward(_FromRow):
    source_type: PointsSourceType
    source_ref: str = Field(
        description="What earned them: the game location's id, the daily challenge's id or the "
        "streak milestone in days."
    )
    class_id: UUID | None = Field(description="Null for Practice and streak milestones.")
    language: Language
    points: int
    created_at: datetime


class ExportedPoints(BaseModel):
    totals: list[ExportedPointsTotal] = Field(description="The total in each language.")
    awards: list[ExportedPointsAward] = Field(description="Every award, oldest first.")


class ExportedBadge(_FromRow):
    achievement_key: str
    language: Language | None = Field(description="Null for a global badge.")
    unlocked_at: datetime


class ExportedPhoto(BaseModel):
    """A sign photo that hasn't been purged yet. Provisional: photos are not stored yet."""

    session_id: UUID
    position: int = Field(description="The location in the game the photo was taken at.")
    attempt: int
    uploaded_at: datetime
    url: str = Field(description="A signed link to the photo, valid for a few minutes.")


class UserExport(BaseModel):
    """What is stored about one account. Lists are oldest first unless noted."""

    exported_at: datetime
    profile: ExportedProfile
    class_enrolments: list[ExportedEnrolment] = Field(
        description="Every class the user joined as a student, left ones too."
    )
    ta_roles: list[ExportedStaffClass] = Field(description="The classes the user assists as a TA.")
    owned_classes: list[ExportedStaffClass] = Field(description="The classes the user owns.")
    sessions: list[ExportedSession] = Field(description="Every game, finished ones too.")
    points: ExportedPoints
    badges: list[ExportedBadge] = Field(
        description="Every badge earned, including ones no longer awarded."
    )
    photos: list[ExportedPhoto] = Field(
        description="The photos not yet purged. Always empty for now: photos are not stored yet."
    )


def export_user(admin_id: UUID, user_id: UUID, db: Session) -> UserExport:
    """Everything stored about the account `user_id`, of any role, deactivated ones too.

    404 USER_NOT_FOUND for an unknown id, which records nothing. An export is recorded with the
    account as the target; `exported_at` is that entry's time. Doesn't commit.
    """
    user = db.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.USER_NOT_FOUND, USER_NOT_FOUND_MESSAGE)
    # only the role: an address or username here would outlive the account being anonymised
    entry = audit.record(
        admin_id, AuditAction.EXPORT_USER, db, target_user_id=user.id, details={"role": user.role}
    )
    return UserExport(
        exported_at=entry.created_at,
        profile=ExportedProfile.model_validate(user),
        class_enrolments=_enrolments(user.id, db),
        ta_roles=_staff_classes(user.id, ClassStaffRole.ASSISTANT, db),
        owned_classes=_staff_classes(user.id, ClassStaffRole.OWNER, db),
        sessions=[
            ExportedSession.model_validate(game) for game in list_games_for_export(user.id, db)
        ],
        points=_points(user.id, db),
        badges=_badges(user.id, db),
        photos=[],  # see the module docstring
    )


def _enrolments(user_id: UUID, db: Session) -> list[ExportedEnrolment]:
    rows = db.execute(
        select(
            ClassEnrollment.class_id,
            Class.name.label("class_name"),
            Class.semester,
            ClassEnrollment.language,
            ClassEnrollment.is_active,
            ClassEnrollment.end_reason,
            ClassEnrollment.joined_at,
            ClassEnrollment.left_at,
        )
        .join(Class, Class.id == ClassEnrollment.class_id)
        .where(ClassEnrollment.student_id == user_id)
        .order_by(ClassEnrollment.joined_at, ClassEnrollment.class_id)
    )
    return [ExportedEnrolment.model_validate(row) for row in rows]


def _staff_classes(user_id: UUID, role: ClassStaffRole, db: Session) -> list[ExportedStaffClass]:
    rows = db.execute(
        select(
            ClassStaff.class_id,
            Class.name.label("class_name"),
            Class.semester,
            ClassStaff.added_at,
        )
        .join(Class, Class.id == ClassStaff.class_id)
        .where(ClassStaff.user_id == user_id, ClassStaff.role == role)
        .order_by(ClassStaff.added_at, ClassStaff.class_id)
    )
    return [ExportedStaffClass.model_validate(row) for row in rows]


def _points(user_id: UUID, db: Session) -> ExportedPoints:
    totals = db.scalars(
        select(UserLanguageStats)
        .where(UserLanguageStats.user_id == user_id)
        .order_by(UserLanguageStats.language)
    )
    awards = db.scalars(
        select(PointsLedger)
        .where(PointsLedger.user_id == user_id)
        .order_by(PointsLedger.created_at, PointsLedger.id)
    )
    return ExportedPoints(
        totals=[ExportedPointsTotal.model_validate(row) for row in totals],
        awards=[ExportedPointsAward.model_validate(row) for row in awards],
    )


def _badges(user_id: UUID, db: Session) -> list[ExportedBadge]:
    rows = db.execute(
        select(Achievement.achievement_key, UserAchievement.language, UserAchievement.unlocked_at)
        .join(Achievement, Achievement.id == UserAchievement.achievement_id)
        .where(UserAchievement.user_id == user_id)
        # one award can unlock several badges at the same time
        .order_by(
            UserAchievement.unlocked_at, Achievement.achievement_key, UserAchievement.language
        )
    )
    return [ExportedBadge.model_validate(row) for row in rows]
