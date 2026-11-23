"""The audit service records the changes admins make and lists them newest first."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

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
def admin(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.ADMIN)


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


def test_record_accepts_the_plain_string_value(db: Session, admin: User, teacher: User) -> None:
    action: Any = "deactivate"
    event = audit.record(admin.id, action, db, target_user_id=teacher.id)
    assert event.action == AuditAction.DEACTIVATE


def test_record_rejects_an_unknown_action(db: Session, admin: User, teacher: User) -> None:
    action: Any = "user_created"
    with pytest.raises(ValueError):
        audit.record(admin.id, action, db, target_user_id=teacher.id)
    assert db.scalar(select(AuditEvent).where(AuditEvent.actor_id == admin.id)) is None


def test_record_does_not_commit(
    db: Session, admin: User, teacher: User, transaction_calls: list[str]
) -> None:
    audit.record(admin.id, AuditAction.CHANGE_EMAIL, db, target_user_id=teacher.id)
    assert "commit" not in transaction_calls


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


def test_list_events_is_newest_first(db: Session, admin: User, teacher: User) -> None:
    recorded = [
        audit.record(admin.id, action, db, target_user_id=teacher.id) for action in AuditAction
    ]
    listed = audit.list_events(db, actor_id=admin.id)
    assert [event.id for event in listed] == [event.id for event in reversed(recorded)]


def test_list_events_filters(
    db: Session, make_user: Callable[..., User], make_class: Callable[..., Class]
) -> None:
    first, second = make_user(role=UserRole.ADMIN), make_user(role=UserRole.ADMIN)
    teacher, new_owner = make_user(role=UserRole.STAFF), make_user(role=UserRole.STAFF)
    school_class = make_class()
    created = audit.record(first.id, AuditAction.CREATE_STAFF, db, target_user_id=teacher.id)
    transferred = audit.record(
        first.id,
        AuditAction.TRANSFER_CLASS,
        db,
        target_user_id=new_owner.id,
        target_class_id=school_class.id,
    )
    other = audit.record(second.id, AuditAction.DEACTIVATE, db, target_user_id=teacher.id)
    actors = {"actor_id": first.id}  # keeps other tests' rows out of each listing

    def ids(**filters: Any) -> list[UUID]:
        return [event.id for event in audit.list_events(db, **filters)]

    assert ids(**actors) == [transferred.id, created.id]
    assert ids(**actors, action=AuditAction.CREATE_STAFF) == [created.id]
    assert ids(target_user_id=teacher.id) == [other.id, created.id]
    assert ids(target_class_id=school_class.id) == [transferred.id]
    assert ids(**actors, since=transferred.created_at) == [transferred.id]
    assert ids(**actors, until=transferred.created_at) == [created.id]


def test_list_events_pages(db: Session, admin: User, teacher: User) -> None:
    recorded = [
        audit.record(admin.id, AuditAction.CHANGE_CONTENT_RIGHTS, db, target_user_id=teacher.id)
        for _ in range(5)
    ]
    newest_first = [event.id for event in reversed(recorded)]
    page = audit.list_events(db, actor_id=admin.id, limit=2, offset=1)
    assert [event.id for event in page] == newest_first[1:3]
    assert audit.list_events(db, actor_id=admin.id, offset=5) == []


@pytest.mark.parametrize(
    "paging", [{"limit": 0}, {"limit": audit.MAX_PAGE_SIZE + 1}, {"offset": -1}]
)
def test_list_events_rejects_bad_paging(db: Session, paging: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        audit.list_events(db, **paging)
