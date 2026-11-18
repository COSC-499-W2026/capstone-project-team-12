"""Tests for GET /api/v1/me/engagement (A12)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Achievement, UserAchievement, UserLanguageStats
from app.models.enums import AchievementCategory, Language, UserRole

URL = "/api/v1/me/engagement"


def test_new_student_starts_at_zero(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(student),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "language": Language.JA.value,
        "total_points": 0,
        "level": 1,
        "points_to_next_level": 500,
        "recent_badges": [],
    }


def test_points_are_separate_by_language(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)

    db.add(
        UserLanguageStats(
            user_id=student.id,
            language=Language.JA,
            total_points=600,
        )
    )
    db.add(
        UserLanguageStats(
            user_id=student.id,
            language=Language.ZH,
            total_points=1700,
        )
    )
    db.flush()

    japanese = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(student),
    )
    chinese = client.get(
        URL,
        params={"language": Language.ZH.value},
        headers=auth_headers(student),
    )

    assert japanese.status_code == 200, japanese.text
    assert chinese.status_code == 200, chinese.text

    assert japanese.json()["total_points"] == 600
    assert japanese.json()["level"] == 2
    assert japanese.json()["points_to_next_level"] == 900

    assert chinese.json()["total_points"] == 1700
    assert chinese.json()["level"] == 3
    assert chinese.json()["points_to_next_level"] == 1300


def test_students_cannot_read_another_students_points(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    first = make_user(role=UserRole.STUDENT)
    second = make_user(role=UserRole.STUDENT)

    db.add(
        UserLanguageStats(
            user_id=first.id,
            language=Language.JA,
            total_points=5000,
        )
    )
    db.flush()

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(second),
    )

    assert response.status_code == 200, response.text
    assert response.json()["total_points"] == 0
    assert response.json()["level"] == 1
    assert response.json()["points_to_next_level"] == 500


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.get(URL, params={"language": Language.JA.value})

    assert response.status_code == 401


def test_staff_can_read_engagement(
    client: TestClient,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(teacher),
    )

    assert response.status_code == 200, response.text
    assert response.json()["total_points"] == 0
    assert response.json()["level"] == 1
    assert response.json()["points_to_next_level"] == 500


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


def test_level_progress_boundaries(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    stats = UserLanguageStats(
        user_id=student.id,
        language=Language.JA,
        total_points=0,
    )
    db.add(stats)
    db.flush()

    cases = [
        (0, 1, 500),
        (499, 1, 1),
        (500, 2, 1000),
        (1499, 2, 1),
        (1500, 3, 1500),
        (2999, 3, 1),
        (3000, 4, 2000),
        (4999, 4, 1),
        (5000, 5, None),
        (6000, 5, None),
    ]

    for points, expected_level, expected_remaining in cases:
        stats.total_points = points
        db.flush()

        response = client.get(
            URL,
            params={"language": Language.JA.value},
            headers=auth_headers(student),
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total_points"] == points
        assert data["level"] == expected_level
        assert data["points_to_next_level"] == expected_remaining


def test_recent_badges_include_language_and_global_unlocks(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    student = make_user(role=UserRole.STUDENT)
    now = datetime.now(UTC)

    definitions = [
        ("test_ja_badge", AchievementCategory.LEARNING, Language.JA, 3),
        ("test_global_badge", AchievementCategory.STREAK, None, 2),
        ("test_zh_badge", AchievementCategory.LEARNING, Language.ZH, 1),
    ]

    for key, category, language, days_ago in definitions:
        achievement = Achievement(
            achievement_key=key,
            category=category.value,
            unlock_condition={"type": "locations_completed", "threshold": 1},
            icon_key="test_icon",
            is_active=True,
        )
        db.add(achievement)
        db.flush()

        db.add(
            UserAchievement(
                user_id=student.id,
                achievement_id=achievement.id,
                language=language,
                unlocked_at=now - timedelta(days=days_ago),
            )
        )

    db.flush()

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(student),
    )

    assert response.status_code == 200, response.text

    badges = response.json()["recent_badges"]

    assert [badge["achievement_key"] for badge in badges] == [
        "test_global_badge",
        "test_ja_badge",
    ]
    assert badges[0]["language"] is None
    assert badges[1]["language"] == Language.JA.value
    assert all(badge["unlocked_at"] for badge in badges)


def test_recent_badges_do_not_include_other_users_unlocks(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    auth_headers: Callable[[User], dict[str, str]],
) -> None:
    first = make_user(role=UserRole.STUDENT)
    second = make_user(role=UserRole.STUDENT)

    achievement = Achievement(
        achievement_key="test_private_badge",
        category=AchievementCategory.LEARNING.value,
        unlock_condition={"type": "locations_completed", "threshold": 1},
        icon_key="test_icon",
        is_active=True,
    )
    db.add(achievement)
    db.flush()

    db.add(
        UserAchievement(
            user_id=first.id,
            achievement_id=achievement.id,
            language=Language.JA,
        )
    )
    db.flush()

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(second),
    )

    assert response.status_code == 200, response.text
    assert response.json()["recent_badges"] == []
