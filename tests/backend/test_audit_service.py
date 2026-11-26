"""The audit service records the changes admins make."""

from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.audit import AuditEvent
from app.models.engagement import Class
from app.models.enums import AuditAction, UserRole
from app.services import audit


@pytest.fixture
def teacher(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.STAFF)


def test_record_returns_the_stored_row_with_defaults(
    db: Session, admin: User, teacher: User
) -> None:
    event = audit.record(admin.id, AuditAction.CREATE_STAFF, db, target_user_id=teacher.id)
    assert event.id is not None and event.created_at is not None
    assert (event.actor_id, event.action, event.target_user_id) == (
        admin.id,
        AuditAction.CREATE_STAFF,
        teacher.id,
    )
    assert event.target_class_id is None and event.details == {}
    assert db.scalar(select(AuditEvent).where(AuditEvent.id == event.id)) is event


def test_record_keeps_every_field_given(
    db: Session, admin: User, teacher: User, make_class: Callable[..., Class]
) -> None:
    school_class = make_class()
    details = {"from_owner": str(uuid4())}
    event = audit.record(
        admin.id,
        AuditAction.TRANSFER_CLASS,
        db,
        target_user_id=teacher.id,
        target_class_id=school_class.id,
        details=details,
    )
    db.expire(event)  # read back what the database stored, not what was passed in
    assert (event.target_user_id, event.target_class_id) == (teacher.id, school_class.id)
    assert event.details == details


def test_record_does_not_commit(
    db: Session, admin: User, teacher: User, transaction_calls: list[str]
) -> None:
    audit.record(admin.id, AuditAction.CHANGE_EMAIL, db, target_user_id=teacher.id)
    assert "commit" not in transaction_calls


def test_record_keeps_order_within_one_transaction(db: Session, admin: User, teacher: User) -> None:
    # each test runs in one transaction, where now() would give both entries the same time
    first = audit.record(admin.id, AuditAction.DEACTIVATE, db, target_user_id=teacher.id)
    second = audit.record(admin.id, AuditAction.REACTIVATE, db, target_user_id=teacher.id)
    assert first.created_at < second.created_at


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"action": "user_created"}, "ck_admin_audit_log_action"),
        ({"details": ["not", "an", "object"]}, "ck_admin_audit_log_details_object"),
        ({"actor_id": uuid4()}, "fk_admin_audit_log_actor_id_users"),
        ({"target_user_id": uuid4()}, "fk_admin_audit_log_target_user_id_users"),
        ({"target_class_id": uuid4()}, "fk_admin_audit_log_target_class_id_classes"),
        ({"target_user_id": None}, "not-null"),
    ],
)
def test_database_rejects_bad_rows(
    db: Session, admin: User, teacher: User, fields: dict[str, object], constraint: str
) -> None:
    values: dict[str, object] = {
        "actor_id": admin.id,
        "action": AuditAction.CREATE_STAFF,
        "target_user_id": teacher.id,
    }
    db.add(AuditEvent(**(values | fields)))
    with pytest.raises(IntegrityError, match=constraint):
        db.flush()
