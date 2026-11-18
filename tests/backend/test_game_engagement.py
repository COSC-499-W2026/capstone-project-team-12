"""get_game_engagement (A23, #47): the level and badges one game added, for its summary."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import (
    GameEngagement,
    apply_location_result,
    get_game_engagement,
)
from app.models.accounts import User
from app.models.engagement import Achievement, PointsLedger, UserAchievement
from app.models.enums import Language, LocationStatus, PointsSourceType
from app.models.game import GameSession, SessionLocation

START = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return START + timedelta(minutes=minutes)


def award(
    db: Session,
    student: User,
    at_time: datetime,
    points: int = 100,
    *,
    location_id: UUID | None = None,
    language: Language = Language.JA,
    source: PointsSourceType = PointsSourceType.LOCATION,
) -> UUID:
    """A ledger row as a finished location (or another source) writes it, at a set time."""
    ref = location_id or uuid4()
    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=source,
            source_ref=str(ref),
            language=language,
            points=points,
            created_at=at_time,
        )
    )
    db.flush()
    return ref


def unlock(
    db: Session, student: User, key: str, at_time: datetime, language: Language | None
) -> None:
    achievement = db.scalars(select(Achievement).where(Achievement.achievement_key == key)).one()
    db.add(
        UserAchievement(
            user_id=student.id,
            achievement_id=achievement.id,
            language=language,
            unlocked_at=at_time,
        )
    )
    db.flush()


def read(db: Session, student: User, *location_ids: UUID) -> GameEngagement:
    return get_game_engagement(student.id, Language.JA, location_ids, START, db)


def test_a_game_with_no_award_keeps_the_level_it_started_with(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    award(db, student, at(-60), points=600)  # an earlier game: level 2
    award(db, student, at(30), points=1000)  # a later game must not count

    assert read(db, student, uuid4()) == GameEngagement(level=2, level_up=False, new_badges=())


def test_points_from_the_moment_the_game_started_are_not_before_it(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    award(db, student, START, points=500)  # the game's own transaction, not an earlier game

    assert read(db, student, uuid4()).level == 1


def test_only_location_awards_are_the_games(db: Session, make_user: Callable[..., User]) -> None:
    student = make_user()
    location = award(db, student, at(1))
    # another source whose ref happens to match must not stretch the game to a later time
    award(
        db,
        student,
        at(30),
        points=1000,
        location_id=location,
        source=PointsSourceType.STREAK_MILESTONE,
    )

    assert read(db, student, location).level == 1


def test_the_level_is_as_of_the_games_last_award(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    award(db, student, at(-60), points=400)
    first, second = award(db, student, at(1)), award(db, student, at(2))
    award(db, student, at(2), points=50, source=PointsSourceType.DAILY_CHALLENGE)  # same finish
    award(db, student, at(30), points=3000)  # a later game: level 4 today
    award(db, student, at(1.5), points=5000, language=Language.FR)  # another language

    result = read(db, student, first, second)

    assert (result.level, result.level_up) == (2, True)  # 400 + 100 + 100 + 50


def test_no_level_up_when_the_game_stays_within_a_level(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    award(db, student, at(-60), points=100)
    location = award(db, student, at(1))

    result = read(db, student, location)

    assert (result.level, result.level_up) == (1, False)


def test_new_badges_are_the_ones_unlocked_with_the_games_awards(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    first, second = award(db, student, at(1)), award(db, student, at(2))
    unlock(db, student, "first_step", at(1), Language.JA)  # with the first award
    unlock(db, student, "week_warrior", at(2), None)  # a global (streak) badge, with the second
    unlock(db, student, "explorer", at(1.5), Language.JA)  # between awards: not this game's
    unlock(db, student, "first_step", at(2), Language.FR)  # another language
    someone_else = make_user()
    unlock(db, someone_else, "first_step", at(1), Language.JA)

    badges = read(db, student, first, second).new_badges

    assert [(b.achievement_key, b.language, b.unlocked_at) for b in badges] == [
        ("first_step", Language.JA, at(1)),
        ("week_warrior", None, at(2)),
    ]


def test_only_this_games_locations_and_this_student_count(
    db: Session, make_user: Callable[..., User]
) -> None:
    student, someone_else = make_user(), make_user()
    shared = uuid4()
    award(db, someone_else, at(1), points=5000, location_id=shared)
    award(db, student, at(3), location_id=uuid4())  # a location of another game

    assert read(db, student, shared) == GameEngagement(level=1, level_up=False, new_badges=())


def test_a_badge_from_a_real_location_result_is_found(
    db: Session,
    make_user: Callable[..., User],
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    """The award and the badge come from apply_location_result in one transaction."""
    student = make_user()
    game = make_game_session(student=student)
    location = make_session_location(
        game,
        position=1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
    )
    apply_location_result(
        location.id,
        student.id,
        completed=True,
        target_language=game.target_language,
        class_id=game.class_id,
        db=db,
    )

    result = get_game_engagement(
        student.id, Language(game.target_language), [location.id], game.started_at, db
    )

    assert [badge.achievement_key for badge in result.new_badges] == ["first_step"]
    assert (result.level, result.level_up) == (1, False)
