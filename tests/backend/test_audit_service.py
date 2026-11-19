"""The audit service records administrative actions and lists them newest first."""

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
from app.models.enums import AuditAction, AuditResult, UserRole
from app.services import audit


@pytest.fixture
def admin(make_user: Callable[..., User]) -> User:
    return make_user(role=UserRole.ADMIN)


def test_record_returns_the_stored_row_with_defaults(db: Session, admin: User) -> None:
    event = audit.record(admin.id, AuditAction.CREATE_USER, db)
    assert event.id is not None and event.created_at is not None
    assert (event.actor_id, event.action, event.result) == (
        admin.id,
        AuditAction.CREATE_USER,
        AuditResult.SUCCESS,
    )
    assert event.target_id is None and event.details == {}
    assert event.ip_address is None and event.trace_id is None
    assert db.scalar(select(AuditEvent).where(AuditEvent.id == event.id)) is event


def test_record_keeps_every_field_given(
    db: Session, admin: User, make_class: Callable[..., Class]
) -> None:
    school_class = make_class()
    details = {"from_owner": str(uuid4()), "to_owner": str(uuid4())}
    event = audit.record(
        admin.id,
        AuditAction.TRANSFER_CLASS,
        db,
        result=AuditResult.DENIED,
        target_id=school_class.id,
        details=details,
        ip_address="203.0.113.7",
        trace_id="trace-123",
    )
    db.expire(event)  # read back what the database stored, not what was passed in
    assert event.result == AuditResult.DENIED and event.target_id == school_class.id
    assert event.details == details
    assert (event.ip_address, event.trace_id) == ("203.0.113.7", "trace-123")


def test_record_accepts_the_plain_string_values(db: Session, admin: User) -> None:
    action: Any = "user_deactivated"
    result: Any = "failure"
    event = audit.record(admin.id, action, db, result=result)
    assert (event.action, event.result) == (AuditAction.DEACTIVATE_USER, AuditResult.FAILURE)


@pytest.mark.parametrize(
    ("action", "result"),
    [("user_deleted", AuditResult.SUCCESS), (AuditAction.CREATE_USER, "maybe")],
)
def test_record_rejects_an_unknown_action_or_result(
    db: Session, admin: User, action: Any, result: Any
) -> None:
    with pytest.raises(ValueError):
        audit.record(admin.id, action, db, result=result)
    assert db.scalar(select(AuditEvent).where(AuditEvent.actor_id == admin.id)) is None


def test_record_does_not_commit(db: Session, admin: User, transaction_calls: list[str]) -> None:
    audit.record(admin.id, AuditAction.UPDATE_USER, db)
    assert "commit" not in transaction_calls


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"action": "user_deleted"}, "ck_audit_log_action"),
        ({"result": "maybe"}, "ck_audit_log_result"),
        ({"details": ["not", "an", "object"]}, "ck_audit_log_details_object"),
        ({"actor_id": uuid4()}, "fk_audit_log_actor_id_users"),
    ],
)
def test_database_rejects_bad_rows(
    db: Session, admin: User, fields: dict[str, object], constraint: str
) -> None:
    values: dict[str, object] = {
        "actor_id": admin.id,
        "action": AuditAction.CREATE_USER,
        "result": AuditResult.SUCCESS,
    }
    db.add(AuditEvent(**(values | fields)))
    with pytest.raises(IntegrityError, match=constraint):
        db.flush()


def test_list_events_is_newest_first(db: Session, admin: User) -> None:
    recorded = [audit.record(admin.id, action, db) for action in AuditAction]
    listed = audit.list_events(db, actor_id=admin.id)
    assert [event.id for event in listed] == [event.id for event in reversed(recorded)]


def test_list_events_filters(
    db: Session, make_user: Callable[..., User], make_class: Callable[..., Class]
) -> None:
    first, second = make_user(role=UserRole.ADMIN), make_user(role=UserRole.ADMIN)
    target = make_class().id
    created = audit.record(first.id, AuditAction.CREATE_USER, db)
    denied = audit.record(
        first.id, AuditAction.TRANSFER_CLASS, db, result=AuditResult.DENIED, target_id=target
    )
    other = audit.record(second.id, AuditAction.CREATE_USER, db, target_id=target)
    actors = {"actor_id": first.id}  # keeps other tests' rows out of each listing

    def ids(**filters: Any) -> list[UUID]:
        return [event.id for event in audit.list_events(db, **filters)]

    assert ids(**actors) == [denied.id, created.id]
    assert ids(**actors, action=AuditAction.CREATE_USER) == [created.id]
    assert ids(**actors, result=AuditResult.DENIED) == [denied.id]
    assert ids(target_id=target) == [other.id, denied.id]
    assert ids(**actors, since=denied.created_at) == [denied.id]
    assert ids(**actors, until=denied.created_at) == [created.id]


def test_list_events_pages(db: Session, admin: User) -> None:
    recorded = [audit.record(admin.id, AuditAction.UPDATE_USER, db) for _ in range(5)]
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
