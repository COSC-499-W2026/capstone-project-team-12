"""Tests for GET /api/v1/me/achievements (A12)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Achievement, PointsLedger, UserAchievement
from app.models.enums import Language, PointsSourceType, UserRole

URL = "/api/v1/me/achievements"


def _definition(db: Session, category: str) -> Achievement:
    """Find a seeded achievement belonging to the requested category."""
    achievement = db.scalar(
        select(Achievement)
        .where(Achievement.category == category)
        .order_by(Achievement.achievement_key)
    )
    assert achievement is not None
    return achievement


def _items(
    client: TestClient,
    user: User,
    auth_headers: Callable[[User], dict[str, str]],
    language: Language = Language.JA,
) -> list[dict]:
    """Get achievements for an authenticated user."""
    response = client.get(
        URL,
        params={"language": language.value},
        headers=auth_headers(user),
    )
    assert response.status_code == 200, response.text
    return response.json()["achievements"]


def test_new_student_sees_all_active_achievements_locked(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    items = _items(client, student, auth_headers)

    active = db.scalars(select(Achievement).where(Achievement.is_active.is_(True))).all()

    assert len(items) == len(active)
    assert {item["achievement_key"] for item in items} == {
        achievement.achievement_key for achievement in active
    }

    for item in items:
        assert item["unlocked"] is False
        assert item["unlocked_at"] is None
        assert item["language"] == (None if item["category"] == "streak" else Language.JA.value)
        assert "progress" in item


def test_language_specific_achievements_are_filtered(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "learning")

    db.add_all(
        [
            UserAchievement(
                user_id=student.id,
                achievement_id=achievement.id,
                language=Language.JA,
            ),
            UserAchievement(
                user_id=student.id,
                achievement_id=achievement.id,
                language=Language.FR,
            ),
        ]
    )
    db.flush()

    for language in (Language.JA, Language.FR):
        items = _items(client, student, auth_headers, language)
        earned = [item for item in items if item["unlocked"]]

        assert len(earned) == 1
        assert earned[0]["achievement_key"] == achievement.achievement_key
        assert earned[0]["language"] == language.value
        assert earned[0]["category"] == "learning"
        assert earned[0]["icon_key"] == achievement.icon_key
        assert earned[0]["unlocked_at"] is not None


def test_achievement_unlocked_in_one_language_is_locked_in_another(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "learning")

    db.add(
        UserAchievement(
            user_id=student.id,
            achievement_id=achievement.id,
            language=Language.JA,
        )
    )
    db.flush()

    french_items = _items(client, student, auth_headers, Language.FR)
    matching = [
        item for item in french_items if item["achievement_key"] == achievement.achievement_key
    ]

    assert len(matching) == 1
    assert matching[0]["language"] == Language.FR.value
    assert matching[0]["unlocked"] is False
    assert matching[0]["unlocked_at"] is None


def test_global_achievements_appear_for_any_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "streak")

    db.add(
        UserAchievement(
            user_id=student.id,
            achievement_id=achievement.id,
            language=None,
        )
    )
    db.flush()

    for language in (Language.JA, Language.FR):
        items = _items(client, student, auth_headers, language)
        matching = [
            item for item in items if item["achievement_key"] == achievement.achievement_key
        ]

        assert len(matching) == 1
        assert matching[0]["language"] is None
        assert matching[0]["unlocked"] is True
        assert matching[0]["unlocked_at"] is not None


def test_other_students_achievements_are_excluded(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    first = make_user(role=UserRole.STUDENT)
    second = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "learning")

    db.add(
        UserAchievement(
            user_id=first.id,
            achievement_id=achievement.id,
            language=Language.JA,
        )
    )
    db.flush()

    items = _items(client, second, auth_headers)

    assert items
    assert all(item["unlocked"] is False for item in items)
    assert all(item["unlocked_at"] is None for item in items)


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.get(URL, params={"language": Language.JA.value})
    assert response.status_code == 401


def test_staff_can_view_achievements(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    staff = make_user(role=UserRole.STAFF)
    items = _items(client, staff, auth_headers)

    assert items
    assert all(item["unlocked"] is False for item in items)


def test_admin_is_forbidden(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    admin = make_user(role=UserRole.ADMIN)

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(admin),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_achievements_are_ordered_newest_first(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    definitions = db.scalars(
        select(Achievement)
        .where(Achievement.category == "learning")
        .order_by(Achievement.achievement_key)
        .limit(2)
    ).all()

    assert len(definitions) == 2

    older = UserAchievement(
        user_id=student.id,
        achievement_id=definitions[0].id,
        language=Language.JA,
        unlocked_at=datetime.now(UTC) - timedelta(days=2),
    )
    newer = UserAchievement(
        user_id=student.id,
        achievement_id=definitions[1].id,
        language=Language.JA,
        unlocked_at=datetime.now(UTC) - timedelta(days=1),
    )

    db.add_all([older, newer])
    db.flush()

    items = _items(client, student, auth_headers)
    earned = [item for item in items if item["unlocked"]]

    assert len(earned) == 2
    assert [item["achievement_key"] for item in earned] == [
        definitions[1].achievement_key,
        definitions[0].achievement_key,
    ]
    assert items[:2] == earned
    assert all(not item["unlocked"] for item in items[2:])


def test_inactive_earned_achievement_remains_visible(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "learning")

    db.add(
        UserAchievement(
            user_id=student.id,
            achievement_id=achievement.id,
            language=Language.JA,
        )
    )
    achievement.is_active = False
    db.flush()

    items = _items(client, student, auth_headers)
    matching = [item for item in items if item["achievement_key"] == achievement.achievement_key]

    assert len(matching) == 1
    assert matching[0]["unlocked"] is True
    assert matching[0]["unlocked_at"] is not None


def test_inactive_unearned_achievement_is_hidden(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "learning")
    achievement.is_active = False
    db.flush()

    items = _items(client, student, auth_headers)

    assert achievement.achievement_key not in {item["achievement_key"] for item in items}


def test_completion_progress_starts_at_zero(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = db.scalar(select(Achievement).where(Achievement.achievement_key == "starter"))
    assert achievement is not None

    items = _items(client, student, auth_headers)
    matching = next(
        item for item in items if item["achievement_key"] == achievement.achievement_key
    )

    assert matching["unlocked"] is False
    assert matching["progress"] == {"current": 0, "required": 5}


def test_completion_progress_counts_only_selected_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    db.add_all(
        [
            PointsLedger(
                user_id=student.id,
                source_type=PointsSourceType.LOCATION,
                source_ref=f"progress-ja-{index}",
                class_id=None,
                language=Language.JA,
                points=100,
            )
            for index in range(3)
        ]
    )
    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=PointsSourceType.LOCATION,
            source_ref="progress-fr-0",
            class_id=None,
            language=Language.FR,
            points=100,
        )
    )
    db.flush()

    japanese = _items(client, student, auth_headers, Language.JA)
    french = _items(client, student, auth_headers, Language.FR)

    ja_starter = next(item for item in japanese if item["achievement_key"] == "starter")
    fr_starter = next(item for item in french if item["achievement_key"] == "starter")

    assert ja_starter["progress"] == {"current": 3, "required": 5}
    assert fr_starter["progress"] == {"current": 1, "required": 5}
    assert ja_starter["unlocked"] is False


def test_unsupported_progress_is_null(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    items = _items(client, student, auth_headers)

    for item in items:
        if item["achievement_key"] in (
            "campus_master",
            "week_warrior",
            "challenge_master",
            "perfect_session",
        ):
            assert item["progress"] is None


def test_progress_does_not_automatically_unlock_achievement(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=PointsSourceType.LOCATION,
            source_ref="progress-without-unlock",
            class_id=None,
            language=Language.JA,
            points=100,
        )
    )
    db.flush()

    items = _items(client, student, auth_headers)
    first_step = next(item for item in items if item["achievement_key"] == "first_step")

    assert first_step["progress"] == {"current": 1, "required": 1}
    assert first_step["unlocked"] is False
    assert first_step["unlocked_at"] is None
