"""Tests for the student's current class and Practice language (A15)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import TwoClassStudent
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.accounts.profile import current_class_id, select_class, set_practice_language
from app.engagement.interface import get_active_class_ids
from app.errors import AppError, ErrorCode
from app.game.sessions import session_idle_limit
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import EnrollmentEndReason, Language, SessionState
from app.models.game import GameSession

ENDINGS = [EnrollmentEndReason.LEFT, EnrollmentEndReason.REMOVED, EnrollmentEndReason.ARCHIVED]
OPEN_STATES = [SessionState.ACTIVE, SessionState.PAUSED]


def test_active_class_ids_newest_first(
    db: Session,
    make_user: Callable[..., User],
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[..., None],
) -> None:
    roster = two_class_student
    assert get_active_class_ids(roster.student.id, db) == [roster.french.id, roster.japanese.id]
    assert get_active_class_ids(make_user().id, db) == []

    end_enrolment(roster.french_enrolment)

    assert get_active_class_ids(roster.student.id, db) == [roster.japanese.id]


def test_stored_class_is_kept(db: Session, two_class_student: TwoClassStudent) -> None:
    # the French class was joined later, but the student chose Japanese
    assert current_class_id(two_class_student.student, db) == two_class_student.japanese.id


def test_no_stored_class_means_practice(db: Session, two_class_student: TwoClassStudent) -> None:
    two_class_student.student.current_class_id = None

    assert current_class_id(two_class_student.student, db) is None


@pytest.mark.parametrize("reason", ENDINGS)
def test_ended_enrolment_falls_back_to_the_other_class(
    db: Session,
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[..., None],
    reason: EnrollmentEndReason,
) -> None:
    roster = two_class_student
    end_enrolment(roster.japanese_enrolment, reason)

    assert current_class_id(roster.student, db) == roster.french.id
    assert roster.student.current_class_id == roster.japanese.id  # reading doesn't write

    end_enrolment(roster.french_enrolment, reason)
    assert current_class_id(roster.student, db) is None


def test_select_class(db: Session, two_class_student: TwoClassStudent) -> None:
    roster = two_class_student

    select_class(roster.student, roster.french.id, db)

    assert roster.student.current_class_id == roster.french.id
    assert not db.dirty  # flushed


def test_select_practice_and_back(db: Session, two_class_student: TwoClassStudent) -> None:
    roster = two_class_student

    select_class(roster.student, None, db)
    assert current_class_id(roster.student, db) is None

    select_class(roster.student, roster.french.id, db)
    assert current_class_id(roster.student, db) == roster.french.id


def test_class_and_language_in_one_request(db: Session, two_class_student: TwoClassStudent) -> None:
    roster = two_class_student

    select_class(roster.student, roster.french.id, db)
    set_practice_language(roster.student, Language.KO, db)

    assert roster.student.current_class_id == roster.french.id
    assert roster.student.preferred_language == Language.KO


def test_class_the_student_is_not_in_is_not_found(
    db: Session,
    two_class_student: TwoClassStudent,
    make_class: Callable[..., Class],
    end_enrolment: Callable[..., None],
) -> None:
    roster = two_class_student
    end_enrolment(roster.french_enrolment)

    # someone else's class, a class that doesn't exist and a class the student left look the same
    for class_id in (make_class().id, uuid4(), roster.french.id):
        with pytest.raises(AppError) as error:
            select_class(roster.student, class_id, db)
        assert error.value.code == ErrorCode.NOT_FOUND
    assert roster.student.current_class_id == roster.japanese.id


def test_archived_class_is_not_found(
    db: Session, two_class_student: TwoClassStudent, end_enrolment: Callable[..., None]
) -> None:
    roster = two_class_student
    roster.french.archived_at = datetime.now(UTC)  # archiving ends the enrolments of the class
    end_enrolment(roster.french_enrolment, EnrollmentEndReason.ARCHIVED)

    with pytest.raises(AppError) as error:
        select_class(roster.student, roster.french.id, db)

    assert error.value.code == ErrorCode.NOT_FOUND
    assert roster.student.current_class_id == roster.japanese.id


def test_student_from_another_session_is_refused(
    db: Session, two_class_student: TwoClassStudent
) -> None:
    roster = two_class_student
    db.expunge(roster.student)  # as if it had been loaded by another session

    with pytest.raises(ValueError, match="same db"):
        select_class(roster.student, roster.french.id, db)
    with pytest.raises(ValueError, match="same db"):
        set_practice_language(roster.student, Language.KO, db)


@pytest.mark.parametrize("state", OPEN_STATES)
def test_open_game_blocks_every_change(
    db: Session,
    two_class_student: TwoClassStudent,
    open_game: Callable[..., GameSession],
    state: SessionState,
) -> None:
    roster = two_class_student
    game = open_game(roster.student, state)

    for change in (
        lambda: select_class(roster.student, roster.french.id, db),
        lambda: select_class(roster.student, None, db),
        lambda: set_practice_language(roster.student, Language.KO, db),
    ):
        with pytest.raises(AppError) as error:
            change()
        assert error.value.code == ErrorCode.SESSION_ALREADY_OPEN
        assert error.value.details == [{"session_id": str(game.id)}]
    assert roster.student.current_class_id == roster.japanese.id
    assert roster.student.preferred_language == Language.JA


@pytest.mark.parametrize("state", OPEN_STATES)
def test_repeating_a_setting_works_during_a_game(
    db: Session,
    two_class_student: TwoClassStudent,
    open_game: Callable[..., GameSession],
    state: SessionState,
) -> None:
    roster = two_class_student
    open_game(roster.student, state)

    select_class(roster.student, roster.japanese.id, db)
    set_practice_language(roster.student, Language.JA, db)
    roster.student.current_class_id = None
    select_class(roster.student, None, db)  # repeating Practice too

    assert roster.student.current_class_id is None
    assert roster.student.preferred_language == Language.JA


def test_idle_game_does_not_block(
    db: Session,
    two_class_student: TwoClassStudent,
    open_game: Callable[..., GameSession],
) -> None:
    idle_since = datetime.now(UTC) - session_idle_limit() - timedelta(minutes=1)
    open_game(two_class_student.student, last_activity_at=idle_since)

    set_practice_language(two_class_student.student, Language.ZH, db)

    assert two_class_student.student.preferred_language == Language.ZH


def test_choosing_the_fallback_class_saves_it(
    db: Session, two_class_student: TwoClassStudent, end_enrolment: Callable[..., None]
) -> None:
    roster = two_class_student
    end_enrolment(roster.japanese_enrolment)
    assert current_class_id(roster.student, db) == roster.french.id  # shown, but not stored

    select_class(roster.student, roster.french.id, db)

    assert roster.student.current_class_id == roster.french.id


def test_repeat_is_judged_by_what_is_stored(
    db: Session, two_class_student: TwoClassStudent
) -> None:
    roster = two_class_student
    # another request commits a change after this one loaded the student
    db.execute(
        text("UPDATE users SET current_class_id = :c, preferred_language = 'ko' WHERE id = :i"),
        {"c": roster.french.id, "i": roster.student.id},
    )

    select_class(roster.student, roster.japanese.id, db)  # what this request saw is not stored
    set_practice_language(roster.student, Language.JA, db)

    db.refresh(roster.student)
    assert roster.student.current_class_id == roster.japanese.id
    assert roster.student.preferred_language == Language.JA


def test_stale_class_shown_as_current_can_be_chosen_during_a_game(
    db: Session,
    two_class_student: TwoClassStudent,
    end_enrolment: Callable[..., None],
    open_game: Callable[..., GameSession],
) -> None:
    roster = two_class_student
    end_enrolment(roster.japanese_enrolment)  # stored class is stale, the app shows French
    game = open_game(roster.student)

    select_class(roster.student, roster.french.id, db)
    assert roster.student.current_class_id == roster.french.id

    roster.student.current_class_id = roster.japanese.id
    with pytest.raises(AppError) as error:
        select_class(roster.student, None, db)  # Practice is a change
    assert error.value.code == ErrorCode.SESSION_ALREADY_OPEN
    assert error.value.details == [{"session_id": str(game.id)}]
