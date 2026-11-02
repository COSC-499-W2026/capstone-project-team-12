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
    response = client.get(URL, params={"language": language.value}, headers=auth_headers(user))
    assert response.status_code == 200, response.text
    return response.json()["achievements"]


def _badge(items: list[dict], achievement: Achievement | str) -> dict:
    key = achievement if isinstance(achievement, str) else achievement.achievement_key
    matches = [item for item in items if item["achievement_key"] == key]
    assert len(matches) == 1
    return matches[0]


def _unlock(
    db: Session,
    user: User,
    achievement: Achievement,
    language: Language | None = Language.JA,
    unlocked_at: datetime | None = None,
) -> None:
    values = dict(user_id=user.id, achievement_id=achievement.id, language=language)
    if unlocked_at is not None:
        values["unlocked_at"] = unlocked_at
    db.add(UserAchievement(**values))
    db.flush()


def _points(db: Session, user: User, language: Language, count: int) -> None:
    db.add_all(
        PointsLedger(
            user_id=user.id,
            source_type=PointsSourceType.LOCATION,
            source_ref=f"progress-{user.id}-{language.value}-{i}",
            class_id=None,
            language=language,
            points=100,
        )
        for i in range(count)
    )
    db.flush()


def test_new_student_sees_all_active_achievements_locked(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    items = _items(client, make_user(role=UserRole.STUDENT), auth_headers)
    active = db.scalars(select(Achievement).where(Achievement.is_active.is_(True))).all()
    assert {item["achievement_key"] for item in items} == {a.achievement_key for a in active}
    assert len(items) == len(active)
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
    for language in (Language.JA, Language.FR):
        _unlock(db, student, achievement, language)
    for language in (Language.JA, Language.FR):
        earned = [i for i in _items(client, student, auth_headers, language) if i["unlocked"]]
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
    _unlock(db, student, achievement)
    french = _badge(_items(client, student, auth_headers, Language.FR), achievement)
    assert french["language"] == Language.FR.value
    assert french["unlocked"] is False
    assert french["unlocked_at"] is None


def test_global_achievements_appear_for_any_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "streak")
    _unlock(db, student, achievement, None)
    for language in (Language.JA, Language.FR):
        badge = _badge(_items(client, student, auth_headers, language), achievement)
        assert badge["language"] is None
        assert badge["unlocked"] is True
        assert badge["unlocked_at"] is not None


def test_other_students_achievements_are_excluded(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    first, second = (make_user(role=UserRole.STUDENT) for _ in range(2))
    _unlock(db, first, _definition(db, "learning"))
    items = _items(client, second, auth_headers)
    assert items
    assert all(not i["unlocked"] and i["unlocked_at"] is None for i in items)


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    assert client.get(URL, params={"language": Language.JA.value}).status_code == 401


def test_staff_can_view_achievements(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    items = _items(client, make_user(role=UserRole.STAFF), auth_headers)
    assert items
    assert all(not i["unlocked"] for i in items)


def test_admin_is_forbidden(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    admin = make_user(role=UserRole.ADMIN)
    response = client.get(URL, params={"language": Language.JA.value}, headers=auth_headers(admin))
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
    now = datetime.now(UTC)
    _unlock(db, student, definitions[0], unlocked_at=now - timedelta(days=2))
    _unlock(db, student, definitions[1], unlocked_at=now - timedelta(days=1))
    items = _items(client, student, auth_headers)
    earned = [i for i in items if i["unlocked"]]
    assert [i["achievement_key"] for i in earned] == [
        definitions[1].achievement_key,
        definitions[0].achievement_key,
    ]
    assert items[:2] == earned
    assert all(not i["unlocked"] for i in items[2:])


def test_inactive_earned_achievement_remains_visible(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    achievement = _definition(db, "learning")
    _unlock(db, student, achievement)
    achievement.is_active = False
    db.flush()
    badge = _badge(_items(client, student, auth_headers), achievement)
    assert badge["unlocked"] is True
    assert badge["unlocked_at"] is not None


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
    assert achievement.achievement_key not in {
        i["achievement_key"] for i in _items(client, student, auth_headers)
    }


def test_completion_progress_starts_at_zero(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    items = _items(client, make_user(role=UserRole.STUDENT), auth_headers)
    starter = _badge(items, "starter")
    assert starter["unlocked"] is False
    assert starter["progress"] == {"current": 0, "required": 5}


def test_completion_progress_counts_only_selected_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    _points(db, student, Language.JA, 3)
    _points(db, student, Language.FR, 1)
    ja = _badge(_items(client, student, auth_headers, Language.JA), "starter")
    fr = _badge(_items(client, student, auth_headers, Language.FR), "starter")
    assert ja["progress"] == {"current": 3, "required": 5}
    assert fr["progress"] == {"current": 1, "required": 5}
    assert ja["unlocked"] is False


def test_unsupported_progress_is_null(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    items = _items(client, make_user(role=UserRole.STUDENT), auth_headers)
    for key in ("campus_master", "week_warrior", "challenge_master", "perfect_session"):
        assert _badge(items, key)["progress"] is None


def test_progress_does_not_automatically_unlock_achievement(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    _points(db, student, Language.JA, 1)
    first_step = _badge(_items(client, student, auth_headers), "first_step")
    assert first_step["progress"] == {"current": 1, "required": 1}
    assert first_step["unlocked"] is False
    assert first_step["unlocked_at"] is None
