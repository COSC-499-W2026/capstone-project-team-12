"""The core tables (#8) reject bad data at the database level, not only in the services."""

import random
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models.accounts import ClassStaff, User
from app.models.engagement import ACCESS_CODE_ALPHABET, AccessCode, Class, ClassEnrollment
from app.models.enums import ClassStaffRole, EnrollmentEndReason, Language, UserRole


def add_user(db: Session, **fields: Any) -> User:
    values: dict[str, Any] = {
        "email": f"{uuid4().hex[:12]}@example.com",
        "username": f"user_{uuid4().hex[:8]}",
        "role": UserRole.STUDENT,
    }
    values.update(fields)
    user = User(**values)
    db.add(user)
    db.flush()
    return user


def add_class(db: Session, **fields: Any) -> Class:
    values: dict[str, Any] = {
        "name": "JAPN 101 - Fall 2026",
        "semester": "2026W1",
        "target_language": Language.JA,
    }
    values.update(fields)
    school_class = Class(**values)
    db.add(school_class)
    db.flush()
    return school_class


def add_staff(db: Session, school_class: Class, user: User, role: str) -> None:
    db.add(ClassStaff(class_id=school_class.id, user_id=user.id, role=role))
    db.flush()


def add_code(db: Session, school_class: Class, **fields: Any) -> AccessCode:
    values: dict[str, Any] = {
        "class_id": school_class.id,
        "code": "".join(random.choices(ACCESS_CODE_ALPHABET, k=8)),
        "expires_at": datetime.now(UTC) + timedelta(days=90),
        "created_by": add_user(db, role=UserRole.STAFF).id,
    }
    values.update(fields)
    code = AccessCode(**values)
    db.add(code)
    db.flush()
    return code


def enrol(db: Session, student: User, school_class: Class, **fields: Any) -> ClassEnrollment:
    values: dict[str, Any] = {
        "student_id": student.id,
        "class_id": school_class.id,
        "access_code_id": add_code(db, school_class).id,
        "language": school_class.target_language,
    }
    values.update(fields)
    enrollment = ClassEnrollment(**values)
    db.add(enrollment)
    db.flush()
    return enrollment


def test_constraints_follow_the_naming_convention() -> None:
    users = Base.metadata.tables["users"]
    names = {constraint.name for constraint in users.constraints}
    assert {"pk_users", "uq_users_email", "ck_users_role", "ck_users_email_lowercase"} <= names


def test_a_user_gets_server_defaults(db: Session) -> None:
    user = add_user(db)
    db.refresh(user)
    assert user.id is not None
    assert user.is_active is True
    assert user.can_manage_content is False
    assert user.password_hash is None
    assert user.created_at.tzinfo is not None


def test_raw_sql_inserts_get_the_server_defaults(db: Session) -> None:
    """Seed data and raw SQL skip the Python defaults, so the database has to fill them in.

    Raw SQL on purpose: an ORM object or a Core insert() still sends the Python defaults, so
    the server defaults would never run. The ORM test above covers the Python side.
    """
    user = db.execute(
        text(
            "INSERT INTO users (email, username, role) VALUES ('raw@example.com', 'raw', 'student')"
            " RETURNING id, is_active, can_manage_content, password_hash, created_at, updated_at"
        )
    ).one()
    assert user.id is not None
    assert user.is_active is True
    assert user.can_manage_content is False
    assert user.password_hash is None
    assert user.created_at.tzinfo is not None
    assert user.updated_at is not None

    school_class = db.execute(
        text(
            "INSERT INTO classes (name, semester, target_language)"
            " VALUES ('JAPN 101', '2026W1', 'ja') RETURNING id, leaderboard_enabled, created_at"
        )
    ).one()
    assert school_class.id is not None
    assert school_class.leaderboard_enabled is True
    assert school_class.created_at is not None

    staff = db.execute(
        text(
            "INSERT INTO class_staff (class_id, user_id, role)"
            " VALUES (:class_id, :user_id, 'owner') RETURNING added_at"
        ),
        {"class_id": school_class.id, "user_id": user.id},
    ).one()
    assert staff.added_at is not None


def test_uppercase_email_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_users_email_lowercase"):
        add_user(db, email="Mia@Example.com")


def test_duplicate_email_is_rejected(db: Session) -> None:
    add_user(db, email="mia@example.com")
    with pytest.raises(IntegrityError, match="uq_users_email"):
        add_user(db, email="mia@example.com")


def test_usernames_are_unique_regardless_of_case(db: Session) -> None:
    add_user(db, username="Mia")
    with pytest.raises(IntegrityError, match="uq_users_username_lower"):
        add_user(db, username="mia")


@pytest.mark.parametrize("username", ["ab", "a" * 31])
def test_username_length_is_checked(db: Session, username: str) -> None:
    with pytest.raises(IntegrityError, match="ck_users_username_length"):
        add_user(db, username=username)


@pytest.mark.parametrize("username", [" mia", "mia "])
def test_username_has_no_surrounding_spaces(db: Session, username: str) -> None:
    with pytest.raises(IntegrityError, match="ck_users_username_trimmed"):
        add_user(db, username=username)


def test_only_staff_accounts_can_manage_content(db: Session) -> None:
    add_user(db, role=UserRole.STAFF, can_manage_content=True)
    with pytest.raises(IntegrityError, match="ck_users_content_rights_staff_only"):
        add_user(db, role=UserRole.STUDENT, can_manage_content=True)


def test_unknown_role_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_users_role"):
        add_user(db, role="teacher")


def test_unknown_practice_language_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_users_preferred_language"):
        add_user(db, preferred_language="en")


def test_current_class_must_exist(db: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_users_current_class_id_classes"):
        add_user(db, current_class_id=uuid4())


def test_unknown_class_language_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_classes_target_language"):
        add_class(db, target_language="en")


def test_semester_must_be_a_term_code(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_classes_semester_format"):
        add_class(db, semester="Fall 2026")


def test_a_class_has_at_most_one_owner_but_many_assistants(db: Session) -> None:
    school_class = add_class(db)
    add_staff(db, school_class, add_user(db, role=UserRole.STAFF), ClassStaffRole.OWNER)
    add_staff(db, school_class, add_user(db, role=UserRole.STAFF), ClassStaffRole.ASSISTANT)
    add_staff(db, school_class, add_user(db), ClassStaffRole.ASSISTANT)
    with pytest.raises(IntegrityError, match="uq_class_staff_one_owner"):
        add_staff(db, school_class, add_user(db, role=UserRole.STAFF), ClassStaffRole.OWNER)


def test_a_user_can_own_several_classes(db: Session) -> None:
    teacher = add_user(db, role=UserRole.STAFF)
    add_staff(db, add_class(db), teacher, ClassStaffRole.OWNER)
    add_staff(db, add_class(db, target_language=Language.FR), teacher, ClassStaffRole.OWNER)


def test_unknown_staff_role_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_class_staff_role"):
        add_staff(db, add_class(db), add_user(db), "teacher")


# access_codes and class_enrollments (#8 PR 2)


def test_access_code_gets_server_defaults(db: Session) -> None:
    code = add_code(db, add_class(db))
    db.refresh(code)
    assert code.is_active is True
    assert code.uses_count == 0
    assert code.max_uses is None


def test_raw_sql_access_codes_and_enrolments_get_the_server_defaults(db: Session) -> None:
    """Same idea as the users test: raw SQL skips the Python defaults."""
    student = add_user(db)
    school_class = add_class(db)
    code = db.execute(
        text(
            "INSERT INTO access_codes (class_id, code, expires_at, created_by)"
            " VALUES (:class_id, 'RAWCDE23', now() + interval '90 days', :created_by)"
            " RETURNING id, is_active, uses_count, max_uses, created_at"
        ),
        {"class_id": school_class.id, "created_by": add_user(db, role=UserRole.STAFF).id},
    ).one()
    assert code.id is not None
    assert code.is_active is True
    assert code.uses_count == 0
    assert code.max_uses is None
    assert code.created_at is not None

    enrollment = db.execute(
        text(
            "INSERT INTO class_enrollments (student_id, class_id, access_code_id, language)"
            " VALUES (:student_id, :class_id, :code_id, 'ja')"
            " RETURNING id, is_active, end_reason, joined_at, left_at"
        ),
        {"student_id": student.id, "class_id": school_class.id, "code_id": code.id},
    ).one()
    assert enrollment.id is not None
    assert enrollment.is_active is True
    assert enrollment.end_reason is None
    assert enrollment.joined_at is not None
    assert enrollment.left_at is None


def test_duplicate_access_code_is_rejected(db: Session) -> None:
    school_class = add_class(db)
    add_code(db, school_class, code="K7PM3QXD")
    with pytest.raises(IntegrityError, match="uq_access_codes_code"):
        add_code(db, school_class, code="K7PM3QXD")


@pytest.mark.parametrize("code", ["K7PM-3QXD", "k7pm3qxd", "K7PM3QX"])
def test_access_code_is_stored_normalised(db: Session, code: str) -> None:
    with pytest.raises(IntegrityError, match="ck_access_codes_code_format"):
        add_code(db, add_class(db), code=code)


def test_max_uses_must_be_positive(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_access_codes_max_uses_positive"):
        add_code(db, add_class(db), max_uses=0)


def test_uses_cannot_go_over_max_uses(db: Session) -> None:
    code = add_code(db, add_class(db), max_uses=2)
    code.uses_count = 2
    db.flush()
    code.uses_count = 3
    with pytest.raises(IntegrityError, match="ck_access_codes_uses_within_max"):
        db.flush()


def test_enrolment_language_must_match_the_class(db: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_class_enrollments_class_id_language_classes"):
        enrol(db, add_user(db), add_class(db, target_language=Language.JA), language=Language.FR)


def test_a_student_is_enrolled_once_per_class(db: Session) -> None:
    student, school_class = add_user(db), add_class(db)
    enrol(db, student, school_class)
    with pytest.raises(IntegrityError, match="uq_class_enrollments_student_id_class_id"):
        enrol(db, student, school_class)


def test_one_active_class_per_language(db: Session) -> None:
    student = add_user(db)
    enrol(db, student, add_class(db))
    enrol(db, student, add_class(db, target_language=Language.FR))  # other language is fine
    with pytest.raises(IntegrityError, match="uq_class_enrollments_one_active_per_language"):
        enrol(db, student, add_class(db))


def test_a_student_can_join_again_after_leaving(db: Session) -> None:
    student = add_user(db)
    old = enrol(db, student, add_class(db))
    old.is_active = False
    old.end_reason = EnrollmentEndReason.LEFT
    old.left_at = datetime.now(UTC)
    db.flush()
    enrol(db, student, add_class(db))


@pytest.mark.parametrize(
    ("is_active", "end_reason"),
    [(True, EnrollmentEndReason.LEFT), (False, None)],
)
def test_end_reason_must_match_the_status(
    db: Session, is_active: bool, end_reason: str | None
) -> None:
    # left_at matches the status, so only the end_reason CHECK can fail
    left_at = None if is_active else datetime.now(UTC)
    student, school_class = add_user(db), add_class(db)
    with pytest.raises(IntegrityError, match="ck_class_enrollments_end_reason_matches_status"):
        enrol(
            db, student, school_class, is_active=is_active, end_reason=end_reason, left_at=left_at
        )


def test_unknown_end_reason_is_rejected(db: Session) -> None:
    # the quotes stop it from also matching ck_class_enrollments_end_reason_matches_status
    with pytest.raises(IntegrityError, match='"ck_class_enrollments_end_reason"'):
        enrol(
            db,
            add_user(db),
            add_class(db),
            is_active=False,
            end_reason="graduated",
            left_at=datetime.now(UTC),
        )


def leave(enrollment: ClassEnrollment) -> None:
    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = datetime.now(UTC)


def test_a_student_can_rejoin_the_same_class(db: Session) -> None:
    """Rejoining reuses the old row, so all three "left" fields have to be cleared."""
    student, school_class = add_user(db), add_class(db)
    enrollment = enrol(db, student, school_class)
    leave(enrollment)
    db.flush()
    enrollment.is_active = True
    enrollment.end_reason = None
    enrollment.left_at = None
    db.flush()


def test_rejoining_without_clearing_the_leave_time_is_rejected(db: Session) -> None:
    enrollment = enrol(db, add_user(db), add_class(db))
    leave(enrollment)
    db.flush()
    enrollment.is_active = True
    enrollment.end_reason = None  # left_at forgotten
    with pytest.raises(IntegrityError, match="ck_class_enrollments_left_at_matches_status"):
        db.flush()


def test_a_student_who_left_must_have_a_leave_time(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_class_enrollments_left_at_matches_status"):
        enrol(
            db,
            add_user(db),
            add_class(db),
            is_active=False,
            end_reason=EnrollmentEndReason.LEFT,
        )


def test_leave_time_cannot_be_before_the_join_time(db: Session) -> None:
    enrollment = enrol(db, add_user(db), add_class(db))
    db.refresh(enrollment)
    leave(enrollment)
    enrollment.left_at = enrollment.joined_at - timedelta(days=1)
    with pytest.raises(IntegrityError, match="ck_class_enrollments_left_after_joining"):
        db.flush()


def test_a_code_from_another_class_cannot_be_used(db: Session) -> None:
    class_a, class_b = add_class(db), add_class(db)
    with pytest.raises(
        IntegrityError, match="fk_class_enrollments_access_code_id_class_id_access_codes"
    ):
        enrol(db, add_user(db), class_b, access_code_id=add_code(db, class_a).id)


def test_old_enrolments_in_the_same_language_are_kept(db: Session) -> None:
    """Only the active row counts for "one class per language", so history stays."""
    student = add_user(db)
    for _ in range(2):
        old = enrol(db, student, add_class(db))
        leave(old)
        db.flush()
    enrol(db, student, add_class(db))


@pytest.mark.parametrize("code", ["K0PM3QXD", "KOPM3QXD", "K1PM3QXD", "KIPM3QXD", "KLPM3QXD"])
def test_access_code_has_no_easily_confused_characters(db: Session, code: str) -> None:
    with pytest.raises(IntegrityError, match="ck_access_codes_code_format"):
        add_code(db, add_class(db), code=code)


def test_access_code_cannot_be_too_long(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_access_codes_code_format"):
        add_code(db, add_class(db), code="K7PM3QXDA")


def test_a_code_can_be_limited_to_one_use(db: Session) -> None:
    code = add_code(db, add_class(db), max_uses=1)
    code.uses_count = 1
    db.flush()


def test_uses_count_cannot_be_negative(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_access_codes_uses_within_max"):
        add_code(db, add_class(db), uses_count=-1)
