"""Teaching assistant invitations. The route checks who may invite."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.interface import invite_assistant
from app.engagement.interface import archive_class
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, User
from app.models.email import EmailOutbox
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import EmailKind, InvitationStatus, Language, UserRole


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

    invitation = invite_assistant(school_class.id, owner.id, f" {student.email.upper()} ", db)

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

    first = invite_assistant(school_class.id, owner.id, teacher.email, db)
    again = invite_assistant(school_class.id, owner.id, teacher.email, db)

    assert again.id == first.id
    assert len(invitation_emails(db, teacher)) == 1


def test_only_staff_and_student_accounts_outside_the_class_staff_can_be_invited(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    admin = make_user(role=UserRole.ADMIN)

    def invite(email: str) -> ErrorCode:
        return error_code(lambda: invite_assistant(school_class.id, owner.id, email, db))

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

    conflict = error_code(lambda: invite_assistant(school_class.id, owner.id, enrolled.email, db))

    assert conflict == ErrorCode.TA_SAME_CLASS_CONFLICT
    assert invitation_emails(db, enrolled) == []
    invite_assistant(school_class.id, owner.id, elsewhere.email, db)  # no conflict: doesn't raise


def test_inviting_to_an_unknown_class_is_not_found(
    db: Session, make_user: Callable[..., User], owner: User
) -> None:
    student = make_user()

    missing = error_code(lambda: invite_assistant(uuid4(), owner.id, student.email, db))

    assert missing == ErrorCode.NOT_FOUND


def test_an_archive_that_locks_the_class_first_leaves_no_invitation_or_email(
    test_engine: Engine, run_together: Callable[..., list[object]]
) -> None:
    # committed, so the archive and the invitation can run in two transactions at once
    with Session(test_engine, expire_on_commit=False) as setup:
        school_class = Class(name="JAPN 101", semester="2026W1", target_language=Language.JA)
        owner, invitee = (
            User(email=f"{uuid4().hex}@example.com", username=f"u_{uuid4().hex[:8]}", role=role)
            for role in (UserRole.STAFF, UserRole.STUDENT)
        )
        setup.add_all([school_class, owner, invitee])
        setup.commit()
    try:
        # the invitation starts while archive holds the class lock, so it waits for the commit
        _, refused = run_together(
            lambda db: archive_class(school_class.id, db),
            (lambda db: invite_assistant(school_class.id, owner.id, invitee.email, db), 0.2),
            hold=0.5,
        )
        assert isinstance(refused, AppError), "the invitation went through"
        assert refused.code == ErrorCode.CLASS_ARCHIVED
        with Session(test_engine) as check:
            invitations = select(ClassStaffInvitation).filter_by(class_id=school_class.id)
            assert check.scalars(invitations).all() == []
            assert invitation_emails(check, invitee) == []
    finally:
        with Session(test_engine) as cleanup:
            cleanup.execute(delete(ClassStaffInvitation).filter_by(class_id=school_class.id))
            cleanup.execute(delete(EmailOutbox).filter_by(user_id=invitee.id))
            cleanup.execute(delete(User).where(User.id.in_([owner.id, invitee.id])))
            cleanup.execute(delete(Class).filter_by(id=school_class.id))
            cleanup.commit()


def test_the_database_allows_one_pending_invitation_per_person_and_class(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invite_assistant(school_class.id, owner.id, student.email, db)

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
