import hashlib
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from jwt import decode
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.orm import Session

from app.accounts.interface import revoke_all_refresh_tokens
from app.accounts.password import hash_password
from app.accounts.tokens import REPLAY_GRACE, create_refresh_token, encode_jwt, rotate_tokens
from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.accounts import RefreshToken, User
from app.models.engagement import AccessCode, Class
from app.models.enums import UserRole

VALID_EMAIL = "test@email.com"
VALID_PASSWORD = "ValidPassword1"

INVALID_EMAIL = "invalidemail"
INVALID_PASSWORD = "NotValidPassword1"


@pytest.fixture
def student(make_user: Callable[..., User]) -> User:
    """An active student who can sign in with VALID_EMAIL and VALID_PASSWORD."""
    return make_user(email=VALID_EMAIL, password_hash=hash_password(VALID_PASSWORD))


def login(client: TestClient, email: str = VALID_EMAIL, password: str = VALID_PASSWORD) -> Response:
    response = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    client.cookies.clear()  # tests send the refresh cookie themselves, so none leaks between calls
    return response


def refresh(client: TestClient, refresh_token: str) -> Response:
    response = client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"refresh_token={refresh_token}"}
    )
    client.cookies.clear()
    return response


def test_login_returns_working_tokens(client: TestClient, student: User):
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert response.cookies["refresh_token"]
    # the access token must work on a protected route: a bad access code gives 400, not 401
    joined = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": "abc"},
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert joined.status_code == 400
    assert joined.json()["error"]["code"] == ErrorCode.INVALID_ACCESS_CODE


def test_access_token_expires_in_60_minutes(client: TestClient, student: User):
    access_token = login(client).json()["access_token"]
    key = str(get_settings().access_token_secret_key)
    claims = decode(access_token, key, ["HS256"])
    expected = datetime.now(UTC) + timedelta(minutes=60)
    assert abs(claims["exp"] - expected.timestamp()) < 60


def test_expired_access_token_on_restricted_route(
    client: TestClient, student: User, make_class: Class, make_access_code: AccessCode
):
    data = {"sub": str(student.id)}
    past_expiry = timedelta(minutes=-2)
    expired_token = encode_jwt(data, past_expiry, get_settings().access_token_secret_key)
    new_class = make_class()
    access_code = make_access_code(school_class=new_class)
    joined = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": access_code.code},
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert joined.status_code == 401
    assert joined.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_refresh_rotates_tokens(client: TestClient, student: User):
    old_refresh_token = login(client).cookies["refresh_token"]
    response = refresh(client, old_refresh_token)
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.cookies["refresh_token"] != old_refresh_token


def test_rotated_refresh_token_is_rejected(client: TestClient, student: User):
    old_refresh_token = login(client).cookies["refresh_token"]
    assert refresh(client, old_refresh_token).status_code == 200
    reused = refresh(client, old_refresh_token)
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_invalid_email(client: TestClient, student: User):
    response = login(client, email=INVALID_EMAIL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_invalid_password(client: TestClient, student: User):
    response = login(client, password=INVALID_PASSWORD)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_revoke_tokens(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]

    revoke_all_refresh_tokens(student.id, db)

    revoked = db.scalars(
        select(RefreshToken.revoked_at).where(RefreshToken.user_id == student.id)
    ).all()
    assert revoked  # the login really made a token
    assert all(revoked_at is not None for revoked_at in revoked)
    assert refresh(client, refresh_token).status_code == 401


def test_refresh_with_no_refresh_token(client: TestClient):
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_expired_refresh_token_is_rejected(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == student.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert refresh(client, refresh_token).status_code == 401


def test_disabled_account_cannot_login(client: TestClient, db: Session, student: User):
    student.is_active = False
    db.flush()
    response = login(client)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_DISABLED


def test_disabled_account_cannot_refresh(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]
    student.is_active = False
    db.flush()
    response = refresh(client, refresh_token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_DISABLED


def test_refresh_cookie_is_protected(client: TestClient, student: User):
    cookie = login(client).headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert f"max-age={14 * 24 * 60 * 60}" in cookie


def test_using_replaced_token_revokes_family(client: TestClient, db: Session, student: User):
    first_token = login(client).cookies["refresh_token"]
    second_token = refresh(client, first_token).cookies["refresh_token"]
    other_device = login(client).cookies["refresh_token"]  # a second sign-in is its own family

    # past the grace window, so the reuse counts as a replay, not a concurrent tab
    first_hash = hashlib.sha256(first_token.encode()).hexdigest()
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == first_hash)
        .values(rotated_at=datetime.now(UTC) - REPLAY_GRACE - timedelta(seconds=1))
    )

    replay = refresh(client, first_token)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED

    db.rollback()  # a real request ends here: anything not committed is gone
    assert refresh(client, second_token).status_code == 401  # same family: revoked
    assert refresh(client, other_device).status_code == 200  # other family: untouched


def test_reuse_within_grace_window_keeps_family(client: TestClient, db: Session, student: User):
    first_token = login(client).cookies["refresh_token"]
    second_token = refresh(client, first_token).cookies["refresh_token"]

    reused = refresh(client, first_token)  # a second tab or a retry, moments later
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED

    db.rollback()
    assert refresh(client, second_token).status_code == 200  # the family still works


def test_rotation_links_old_token_to_new(client: TestClient, db: Session, student: User):
    first_token = login(client).cookies["refresh_token"]
    second_token = refresh(client, first_token).cookies["refresh_token"]

    def row(token: str) -> RefreshToken:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return db.scalars(select(RefreshToken).where(RefreshToken.token_hash == token_hash)).one()

    assert row(first_token).replaced_by == row(second_token).id


def test_refreshes_sent_at_once_rotate_only_once(test_engine: Engine) -> None:
    """Five refreshes with the same token racing, each in its own committed transaction (the
    savepoint session the other tests use can't run two at once)."""
    with Session(test_engine, expire_on_commit=False) as setup:
        user = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
        )
        setup.add(user)
        setup.flush()
        token, token_id = create_refresh_token(user.id, setup)
        family_id = setup.scalars(
            select(RefreshToken.family_id).where(RefreshToken.id == token_id)
        ).one()
        setup.commit()
    start = threading.Barrier(5, timeout=10)
    successes: list[str] = []
    errors: list[BaseException] = []  # a thread's exception is otherwise only printed

    def rotate() -> None:
        try:
            with Session(test_engine) as session:
                start.wait()
                try:
                    pair = rotate_tokens(token, session)
                except AppError:
                    session.commit()  # as the route does
                    return
                session.commit()
                successes.append(pair.refresh_token)
        except BaseException as error:
            errors.append(error)

    try:
        threads = [threading.Thread(target=rotate) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads), "a refresh is stuck"
        if errors:
            raise errors[0]
        assert len(successes) == 1
        with Session(test_engine) as check:
            live = select(RefreshToken).where(
                RefreshToken.family_id == family_id,
                RefreshToken.rotated_at.is_(None),
                RefreshToken.revoked_at.is_(None),
            )
            assert len(check.scalars(live).all()) == 1  # the other four didn't revoke it either
    finally:
        with Session(test_engine) as cleanup:
            cleanup.execute(text("SET lock_timeout = '10s'"))  # a stuck thread can't hang pytest
            # replaced_by points within the family, so clear it before deleting the rows
            family = RefreshToken.family_id == family_id
            cleanup.execute(update(RefreshToken).where(family).values(replaced_by=None))
            cleanup.execute(delete(RefreshToken).where(family))
            cleanup.execute(delete(User).where(User.id == user.id))
            cleanup.commit()


def test_overlong_password_is_rejected(client: TestClient, student: User):
    overlong = "A1" * 50  # 100 bytes: past bcrypt's 72-byte limit
    for email in (VALID_EMAIL, "nobody@email.com"):
        response = login(client, email=email, password=overlong)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS
