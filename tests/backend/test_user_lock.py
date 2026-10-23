"""The user row is locked before a game starts or a class or Practice language changes (A15)."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from conftest import TwoClassStudent
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.accounts.profile import select_class
from app.errors import AppError, ErrorCode
from app.game.start import start_game
from app.models.accounts import User
from app.models.enums import Difficulty, Language
from app.models.game import GameSession
from app.services.locks import lock_user


@pytest.fixture
def statements(db: Session) -> Iterator[list[str]]:
    """The SQL the session sends, in order."""
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        seen.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    yield seen
    event.remove(bind, "before_cursor_execute", record)


def _assert_locked_before_the_game_check(statements: list[str]) -> None:
    lock = next(i for i, s in enumerate(statements) if "FOR NO KEY UPDATE" in s and "users" in s)
    game_check = next(i for i, s in enumerate(statements) if "game_sessions" in s)
    assert lock < game_check


def test_start_game_locks_before_checking_for_an_open_game(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    statements: list[str],
) -> None:
    student = make_user()
    make_game_session(student)
    statements.clear()

    with pytest.raises(AppError) as raised:
        start_game(student, Difficulty.BASIC, db, language=Language.JA)

    assert raised.value.code == ErrorCode.SESSION_ALREADY_OPEN
    _assert_locked_before_the_game_check(statements)


def test_class_switch_locks_before_checking_for_an_open_game(
    db: Session, two_class_student: TwoClassStudent, statements: list[str]
) -> None:
    statements.clear()

    select_class(two_class_student.student, two_class_student.french.id, db)

    _assert_locked_before_the_game_check(statements)


def test_lock_keeps_unsaved_changes_when_the_session_does_not_autoflush(
    db: Session, make_user: Callable[..., User]
) -> None:
    # the app's sessions have autoflush off, so lock_user must flush by itself
    student = make_user(preferred_language=Language.JA)
    student.preferred_language = Language.KO

    with db.no_autoflush:
        lock_user(student.id, db)

    assert student.preferred_language == Language.KO
