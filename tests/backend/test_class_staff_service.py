"""Teaching assistant invitations. The route checks who may invite."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.interface import invite_assistant
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, User
from app.models.email import EmailOutbox
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import EmailKind, InvitationStatus, UserRole


@pytest.fixture
def owner(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.STAFF)


@pytest.fixture
def school_class(
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
    owner: User,
) -> Class:
    school_class = make_class()
    make_class_staff(school_class, owner)
    return school_class


def error_code(call: Callable[[], object]) -> ErrorCode:
    with pytest.raises(AppError) as exc:
        call()
    return exc.value.code


def staff_role(db: Session, class_id: UUID, user_id: UUID) -> str | None:
    statement = select(ClassStaff.role).where(
        ClassStaff.class_id == class_id, ClassStaff.user_id == user_id
    )
    return db.scalar(statement)


def invitation_emails(db: Session, user: User) -> list[EmailOutbox]:
    return list(
        db.scalars(
            select(EmailOutbox).where(
                EmailOutbox.user_id == user.id, EmailOutbox.template == EmailKind.TA_INVITATION
            )
        )
    )


def test_invite_stores_a_pending_invitation_and_queues_one_email(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    before = datetime.now(UTC)

    invitation = invite_assistant(school_class.id, owner, f" {student.email.upper()} ", db)

    assert (invitation.class_id, invitation.invitee_id, invitation.invited_by) == (
        school_class.id,
        student.id,
        owner.id,
    )
    assert invitation.email == student.email
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.responded_at is None
    assert before + INVITATION_TTL <= invitation.expires_at <= datetime.now(UTC) + INVITATION_TTL
    [email] = invitation_emails(db, student)
    assert email.params == {"class_name": school_class.name}
    # not an assistant until the invitation is accepted
    assert staff_role(db, school_class.id, student.id) is None


def test_inviting_again_while_pending_returns_the_same_invitation(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    first = invite_assistant(school_class.id, owner, teacher.email, db)
    again = invite_assistant(school_class.id, owner, teacher.email, db)

    assert again.id == first.id
    assert len(invitation_emails(db, teacher)) == 1


def test_an_expired_invitation_is_replaced_by_a_new_one(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    old = invite_assistant(school_class.id, owner, student.email, db)
    old.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()

    new = invite_assistant(school_class.id, owner, student.email, db)

    assert new.id != old.id
    assert old.status == InvitationStatus.EXPIRED
    assert len(invitation_emails(db, student)) == 2


def test_only_staff_and_student_accounts_outside_the_class_staff_can_be_invited(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    admin = make_user(role=UserRole.ADMIN)

    def invite(email: str) -> ErrorCode:
        return error_code(lambda: invite_assistant(school_class.id, owner, email, db))

    assert invite(f"{uuid4().hex}@example.com") == ErrorCode.USER_NOT_FOUND
    assert invite(admin.email) == ErrorCode.FORBIDDEN_ROLE
    assert invite(owner.email) == ErrorCode.VALIDATION_ERROR  # already owns or assists the class
    invitations = (
        select(func.count())
        .select_from(ClassStaffInvitation)
        .where(ClassStaffInvitation.class_id == school_class.id)
    )
    assert db.scalar(invitations) == 0


def test_a_student_of_the_class_is_refused_but_one_from_another_class_can_be_invited(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
    school_class: Class,
    owner: User,
) -> None:
    enrolled = make_user()
    make_enrollment(enrolled, school_class)
    elsewhere = make_user()
    make_enrollment(elsewhere, make_class())  # another class in the same language

    conflict = error_code(lambda: invite_assistant(school_class.id, owner, enrolled.email, db))

    assert conflict == ErrorCode.TA_SAME_CLASS_CONFLICT
    assert invitation_emails(db, enrolled) == []
    invite_assistant(school_class.id, owner, elsewhere.email, db)  # no conflict: doesn't raise


def test_the_database_allows_one_pending_invitation_per_person_and_class(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invite_assistant(school_class.id, owner, student.email, db)

    db.add(
        ClassStaffInvitation(
            class_id=school_class.id,
            invitee_id=student.id,
            invited_by=owner.id,
            expires_at=datetime.now(UTC) + INVITATION_TTL,
        )
    )
    with pytest.raises(IntegrityError, match="uq_class_staff_invitations_one_pending"):
        db.flush()
