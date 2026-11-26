"""Teaching assistant invitations, removal and acceptance. The routes check who may call them."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from random import SystemRandom
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.interface import (
    PendingInvitation,
    accept_invitation,
    decline_invitation,
    invite_assistant,
    list_invitations,
    remove_assistant,
)
from app.engagement.interface import validate_code_and_enrol
from app.errors import AppError, ErrorCode
from app.models.accounts import INVITATION_TTL, ClassStaff, ClassStaffInvitation, User
from app.models.email import EmailOutbox
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, EmailKind, InvitationStatus, Language, UserRole
from app.services.email_sender import OutgoingMail, send_due_emails


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
    assert email.params == {"class_name": school_class.name, "invitation_id": str(invitation.id)}
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


class _NoMailer:
    def send(self, mail: OutgoingMail) -> None:
        pass


def test_the_7_days_start_when_the_invitation_email_is_sent(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    invitation = invite_assistant(school_class.id, owner, make_user().email, db)
    invitation.expires_at = datetime.now(UTC) + timedelta(days=1)  # as if queued 6 days ago
    db.flush()

    send_due_emails(_NoMailer(), db)

    db.refresh(invitation)
    assert invitation.expires_at > datetime.now(UTC) + INVITATION_TTL - timedelta(minutes=1)


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


@pytest.mark.parametrize(
    "change",
    [
        lambda class_id, owner, email, db: invite_assistant(class_id, owner, email, db),
        lambda class_id, owner, email, db: remove_assistant(class_id, email, db),
    ],
    ids=["invite", "remove"],
)
def test_staff_of_archived_or_missing_classes_cannot_change(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    owner: User,
    change: Callable[[UUID, User, str, Session], Any],
) -> None:
    email = make_user().email
    archived = make_class(archived_at=datetime.now(UTC))

    assert error_code(lambda: change(archived.id, owner, email, db)) == ErrorCode.CLASS_ARCHIVED
    assert error_code(lambda: change(uuid4(), owner, email, db)) == ErrorCode.NOT_FOUND


def test_remove_takes_away_an_assistant_and_repeating_it_changes_nothing(
    db: Session,
    make_user: Callable[..., User],
    make_class_staff: Callable[..., ClassStaff],
    school_class: Class,
) -> None:
    assistant = make_user()
    make_class_staff(school_class, assistant, ClassStaffRole.ASSISTANT)

    assert remove_assistant(school_class.id, assistant.email.upper(), db) is True
    assert staff_role(db, school_class.id, assistant.id) is None
    assert remove_assistant(school_class.id, assistant.email, db) is False


def test_remove_cancels_a_pending_invitation(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)

    assert remove_assistant(school_class.id, student.email, db) is True

    assert invitation.status == InvitationStatus.CANCELLED
    assert invitation.responded_at is None
    accept = error_code(lambda: accept_invitation(student.id, invitation.id, db))
    assert accept == ErrorCode.NOT_FOUND


def test_remove_leaves_the_owner_and_ignores_unknown_accounts(
    db: Session, school_class: Class, owner: User
) -> None:
    assert remove_assistant(school_class.id, owner.email, db) is False
    assert staff_role(db, school_class.id, owner.id) == ClassStaffRole.OWNER
    assert remove_assistant(school_class.id, f"{uuid4().hex}@example.com", db) is False


def test_accepting_makes_the_invitee_an_assistant_once(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)

    assert accept_invitation(student.id, invitation.id, db) == school_class.id

    assert staff_role(db, school_class.id, student.id) == ClassStaffRole.ASSISTANT
    assert invitation.status == InvitationStatus.ACCEPTED
    assert invitation.responded_at is not None
    # accepting again changes nothing, so it can't bring back an assistant the owner removed
    remove_assistant(school_class.id, student.email, db)
    assert accept_invitation(student.id, invitation.id, db) == school_class.id
    assert staff_role(db, school_class.id, student.id) is None


def test_accepting_checks_again_that_the_invitee_is_not_a_student_of_the_class(
    db: Session,
    make_user: Callable[..., User],
    make_enrollment: Callable[..., ClassEnrollment],
    school_class: Class,
    owner: User,
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)
    make_enrollment(student, school_class)  # joined the class after being invited

    accept = error_code(lambda: accept_invitation(student.id, invitation.id, db))

    assert accept == ErrorCode.TA_SAME_CLASS_CONFLICT
    assert staff_role(db, school_class.id, student.id) is None
    assert invitation.status == InvitationStatus.PENDING


def test_only_the_invitee_can_accept_an_open_invitation(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)

    def accept(user_id: UUID, invitation_id: UUID) -> ErrorCode:
        return error_code(lambda: accept_invitation(user_id, invitation_id, db))

    assert accept(make_user().id, invitation.id) == ErrorCode.NOT_FOUND
    assert accept(student.id, uuid4()) == ErrorCode.NOT_FOUND
    invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    assert accept(student.id, invitation.id) == ErrorCode.NOT_FOUND
    assert staff_role(db, school_class.id, student.id) is None


def test_an_invitation_to_an_archived_class_cannot_be_accepted(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)
    school_class.archived_at = datetime.now(UTC)
    db.flush()

    accept = error_code(lambda: accept_invitation(student.id, invitation.id, db))

    assert accept == ErrorCode.CLASS_ARCHIVED


def test_declining_answers_the_invitation_once(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)

    assert decline_invitation(student.id, invitation.id, db) == school_class.id

    assert invitation.status == InvitationStatus.DECLINED
    assert invitation.responded_at is not None
    responded_at = invitation.responded_at
    assert decline_invitation(student.id, invitation.id, db) == school_class.id  # no change
    assert invitation.responded_at == responded_at


def test_an_accepted_invitation_cannot_be_declined(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)
    accept_invitation(student.id, invitation.id, db)

    decline = error_code(lambda: decline_invitation(student.id, invitation.id, db))

    assert decline == ErrorCode.NOT_FOUND
    assert staff_role(db, school_class.id, student.id) == ClassStaffRole.ASSISTANT


def test_an_invitation_to_an_archived_class_can_still_be_declined(
    db: Session, make_user: Callable[..., User], school_class: Class, owner: User
) -> None:
    student = make_user()
    invitation = invite_assistant(school_class.id, owner, student.email, db)
    school_class.archived_at = datetime.now(UTC)
    db.flush()

    assert decline_invitation(student.id, invitation.id, db) == school_class.id


def test_the_list_holds_only_invitations_the_user_can_still_answer(
    db: Session, make_user: Callable[..., User], make_class: Callable[..., Class], owner: User
) -> None:
    student = make_user()
    classes = [make_class(name=f"JAPN {number}") for number in range(5)]
    older, newer, declined, expired, archived = [
        invite_assistant(school_class.id, owner, student.email, db) for school_class in classes
    ]
    older.created_at = datetime.now(UTC) - timedelta(days=1)
    decline_invitation(student.id, declined.id, db)
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    classes[4].archived_at = datetime.now(UTC)
    invite_assistant(classes[0].id, owner, make_user().email, db)  # someone else's
    db.flush()

    assert list_invitations(student.id, db) == [
        PendingInvitation(newer.id, classes[1].id, "JAPN 1", newer.expires_at),
        PendingInvitation(older.id, classes[0].id, "JAPN 0", older.expires_at),
    ]


def test_staff_changes_leave_the_transaction_to_the_caller(
    db: Session,
    make_user: Callable[..., User],
    school_class: Class,
    owner: User,
    transaction_calls: list[str],
) -> None:
    student = make_user()
    transaction_calls.clear()

    invitation = invite_assistant(school_class.id, owner, student.email, db)
    accept_invitation(student.id, invitation.id, db)
    remove_assistant(school_class.id, student.email, db)
    declined = invite_assistant(school_class.id, owner, student.email, db)
    decline_invitation(student.id, declined.id, db)

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


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


@pytest.fixture
def committed_invitation(
    test_engine: Engine,
) -> Iterator[tuple[User, ClassStaffInvitation, AccessCode]]:
    """Commit a student invited to a class they could also join, so two transactions see it."""
    with Session(test_engine, expire_on_commit=False) as db:
        student, owner = (
            User(email=f"{uuid4().hex}@example.com", username=f"ta_{uuid4().hex[:12]}", role=role)
            for role in (UserRole.STUDENT, UserRole.STAFF)
        )
        school_class = Class(name="JAPN 101", semester="2026W1", target_language=Language.JA)
        db.add_all([student, owner, school_class])
        db.flush()
        code = AccessCode(
            class_id=school_class.id,
            code="".join(SystemRandom().choices(ACCESS_CODE_ALPHABET, k=8)),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            created_by=owner.id,
        )
        invitation = ClassStaffInvitation(
            class_id=school_class.id,
            invitee_id=student.id,
            email=student.email,
            invited_by=owner.id,
            expires_at=datetime.now(UTC) + INVITATION_TTL,
        )
        owner_row = ClassStaff(
            class_id=school_class.id, user_id=owner.id, role=ClassStaffRole.OWNER
        )
        db.add_all([code, invitation, owner_row])
        db.commit()

    try:
        yield student, invitation, code
    finally:
        with Session(test_engine) as db:
            for table in (ClassEnrollment, ClassStaffInvitation, ClassStaff, AccessCode):
                db.execute(delete(table).where(table.class_id == school_class.id))
            db.execute(delete(User).where(User.id.in_([student.id, owner.id])))
            db.execute(delete(Class).where(Class.id == school_class.id))
            db.commit()


@pytest.mark.parametrize("accept_first", [True, False], ids=["accept-first", "join-first"])
def test_accepting_and_joining_at_once_leave_only_one_role(
    test_engine: Engine,
    committed_invitation: tuple[User, ClassStaffInvitation, AccessCode],
    run_together: Callable[..., list[Any]],
    accept_first: bool,
) -> None:
    student, invitation, code = committed_invitation

    def accept(session: Session) -> UUID:
        return accept_invitation(student.id, invitation.id, session)

    def join(session: Session) -> object:
        return validate_code_and_enrol(student.id, code.code, session)

    first, second = (accept, join) if accept_first else (join, accept)
    # the second starts while the first still holds its locks, and must wait for its commit
    _, later = run_together(first, (second, 0.1), hold=0.5)

    assert isinstance(later, AppError)
    assert later.code == ErrorCode.TA_SAME_CLASS_CONFLICT
    with Session(test_engine) as db:
        assistant = staff_role(db, invitation.class_id, student.id)
        enrolled = db.scalar(
            select(ClassEnrollment.id).where(
                ClassEnrollment.student_id == student.id, ClassEnrollment.is_active.is_(True)
            )
        )
    assert (assistant is not None) == accept_first
    assert (enrolled is not None) == (not accept_first)
