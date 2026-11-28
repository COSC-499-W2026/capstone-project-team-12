"""An admin's export of what is stored about one account, as a single JSON document.

The export holds the profile, the class enrolments, the classes the user assists as a TA and
the classes they own. It leaves out what only the system needs: the password hash, refresh
tokens and emailed links.

Each export is recorded in the admin audit log as export_user. Nothing here commits.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.staff import USER_NOT_FOUND_MESSAGE
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import AuditAction, ClassStaffRole, EnrollmentEndReason, Language, UserRole
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


class UserExport(BaseModel):
    """What is stored about one account. Lists are oldest first unless noted."""

    exported_at: datetime
    profile: ExportedProfile
    class_enrolments: list[ExportedEnrolment] = Field(
        description="Every class the user joined as a student, left ones too."
    )
    ta_roles: list[ExportedStaffClass] = Field(description="The classes the user assists as a TA.")
    owned_classes: list[ExportedStaffClass] = Field(description="The classes the user owns.")


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
