"""Tests for GET /api/v1/me/achievements (A12)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Achievement, UserAchievement
from app.models.enums import Language, UserRole

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


def test_new_student_has_no_achievements(
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
    assert response.json() == {"achievements": []}


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

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(student),
    )

    assert response.status_code == 200, response.text
    items = response.json()["achievements"]
    assert len(items) == 1
    assert items[0]["achievement_key"] == achievement.achievement_key
    assert items[0]["language"] == Language.JA.value
    assert items[0]["category"] == "learning"
    assert items[0]["icon_key"] == achievement.icon_key
    assert items[0]["unlocked_at"] is not None


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
        response = client.get(
            URL,
            params={"language": language.value},
            headers=auth_headers(student),
        )

        assert response.status_code == 200, response.text
        items = response.json()["achievements"]
        assert len(items) == 1
        assert items[0]["achievement_key"] == achievement.achievement_key
        assert items[0]["language"] is None


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

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(second),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"achievements": []}


def test_unauthenticated_request_is_rejected(client: TestClient) -> None:
    response = client.get(URL, params={"language": Language.JA.value})
    assert response.status_code == 401


def test_non_student_is_forbidden(
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

    response = client.get(
        URL,
        params={"language": Language.JA.value},
        headers=auth_headers(student),
    )

    assert response.status_code == 200, response.text

    items = response.json()["achievements"]
    assert len(items) == 2
    assert [item["achievement_key"] for item in items] == [
        definitions[1].achievement_key,
        definitions[0].achievement_key,
    ]
