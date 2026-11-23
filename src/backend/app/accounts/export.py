"""An admin's export of what is stored about one account, as a single JSON document.

The export holds the profile, the class enrolments and the classes the user assists as a TA.
It leaves out what only the system needs: the password hash, refresh tokens and emailed links.

Each export is written to the audit log as user_exported, and an unknown id as a FAILURE that
names the error code. Nothing here commits: the route commits the entry, a FAILURE one before
re-raising.
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
from app.models.enums import (
    AuditAction,
    AuditResult,
    ClassStaffRole,
    EnrollmentEndReason,
    Language,
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


class ExportedTaRole(_FromRow):
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
    ta_roles: list[ExportedTaRole] = Field(description="The classes the user assists as a TA.")


def export_user(
    admin_id: UUID, user_id: UUID, db: Session, *, ip_address: str | None = None
) -> UserExport:
    """Everything stored about the account `user_id`, of any role, deactivated ones too.

    404 USER_NOT_FOUND for an unknown id, recorded as a FAILURE with no account details. A
    success is recorded with the account's address, username and role as they were when it
    was exported; `exported_at` is that entry's time. Doesn't commit.
    """
    user = db.get(User, user_id)
    if user is None:
        error = AppError(ErrorCode.USER_NOT_FOUND, USER_NOT_FOUND_MESSAGE)
        audit.record(
            admin_id,
            AuditAction.EXPORT_USER,
            db,
            result=AuditResult.FAILURE,
            target_id=user_id,
            details={"error": error.code.value},
            ip_address=ip_address,
        )
        raise error
    entry = audit.record(
        admin_id,
        AuditAction.EXPORT_USER,
        db,
        target_id=user.id,
        details={"email": user.email, "username": user.username, "role": user.role},
        ip_address=ip_address,
    )
    return UserExport(
        exported_at=entry.created_at,
        profile=ExportedProfile.model_validate(user),
        class_enrolments=_enrolments(user.id, db),
        ta_roles=_ta_roles(user.id, db),
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


def _ta_roles(user_id: UUID, db: Session) -> list[ExportedTaRole]:
    rows = db.execute(
        select(
            ClassStaff.class_id,
            Class.name.label("class_name"),
            Class.semester,
            ClassStaff.added_at,
        )
        .join(Class, Class.id == ClassStaff.class_id)
        .where(ClassStaff.user_id == user_id, ClassStaff.role == ClassStaffRole.ASSISTANT)
        .order_by(ClassStaff.added_at, ClassStaff.class_id)
    )
    return [ExportedTaRole.model_validate(row) for row in rows]
