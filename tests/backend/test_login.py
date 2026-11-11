import hashlib
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.testclient import TestClient
from httpx2 import Response
from jwt import decode
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.orm import Session

from app.accounts import email_links as email_links_module
from app.accounts import login as login_module
from app.accounts.email_links import reset_password
from app.accounts.interface import revoke_all_refresh_tokens
from app.accounts.login import login_account
from app.accounts.password import hash_password
from app.accounts.tokens import (
    REPLAY_GRACE,
    TokenPair,
    create_refresh_token,
    encode_jwt,
    rotate_tokens,
)
from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.accounts import RefreshToken, User
from app.models.email import EmailToken
from app.models.engagement import AccessCode, Class
from app.models.enums import EmailKind, UserRole
from app.services.email import EmailService

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


def _backend_is_waiting_on_a_lock(engine: Engine, pid: int) -> bool:
    with Session(engine) as look:
        look.execute(text("SET lock_timeout = '5s'"))
        kind = look.scalar(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": pid},
        )
    return kind == "Lock"


OLD_PASSWORD, NEW_PASSWORD = "OldPassword1", "NewPassword1"
WAIT_S = 10  # every wait between threads gives up after this


@contextmanager
def _committed_student(engine: Engine) -> Iterator[tuple[User, str, str]]:
    """A verified student committed for real, with a refresh token and a reset link, so other
    connections see them; everything is removed afterwards."""
    with Session(engine, expire_on_commit=False) as setup:
        user = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
            email_verified_at=datetime.now(UTC),
            password_hash=hash_password(OLD_PASSWORD),
        )
        setup.add(user)
        setup.flush()
        refresh_token, _ = create_refresh_token(user.id, setup)
        link = EmailService.create_token(user, EmailKind.RESET_PASSWORD, setup)
        setup.commit()
    try:
        yield user, refresh_token, link
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(text("SET lock_timeout = '10s'"))
            owned = RefreshToken.user_id == user.id
            cleanup.execute(update(RefreshToken).where(owned).values(replaced_by=None))
            cleanup.execute(delete(RefreshToken).where(owned))
            cleanup.execute(delete(EmailToken).where(EmailToken.user_id == user.id))
            cleanup.execute(delete(User).where(User.id == user.id))
            cleanup.commit()


class _InThread:
    """Runs a job of its own session in a thread and keeps what it returned or raised."""

    def __init__(self, engine: Engine, job: Callable[[Session], object]) -> None:
        self.pid: int | None = None
        self.outcome: object = None
        self.error: BaseException | None = None

        def run() -> None:
            try:
                with Session(engine) as session:
                    session.execute(text("SET lock_timeout = '10s'"))
                    self.pid = session.scalar(text("SELECT pg_backend_pid()"))
                    try:
                        self.outcome = job(session)
                    except AppError as refused:
                        self.outcome = refused
                    session.commit()
            except BaseException as error:  # reported by finish()
                self.error = error

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def finish(self) -> object:
        self.thread.join(timeout=WAIT_S + 5)
        assert not self.thread.is_alive(), "a thread is stuck"
        if self.error is not None:
            raise self.error
        return self.outcome


def _wait(event: threading.Event) -> None:
    assert event.wait(timeout=WAIT_S), "the other thread never got there"


def _tokens(engine: Engine, user: User) -> list[bool]:
    """Whether each of the user's refresh tokens is revoked."""
    with Session(engine) as check:
        revoked = check.scalars(
            select(RefreshToken.revoked_at).where(RefreshToken.user_id == user.id)
        )
        return [at is not None for at in revoked]


def _blocked(engine: Engine, job: _InThread) -> bool:
    """Waits until the job's session waits on a lock (True) or the job ends (False).

    It doesn't say which lock: each test fails without its fix, which is what shows the lock is
    the user's."""
    deadline = time.monotonic() + WAIT_S
    while job.thread.is_alive():
        if job.pid is not None and _backend_is_waiting_on_a_lock(engine, job.pid):
            return True
        assert time.monotonic() < deadline, "the job neither waited nor finished"
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("via", ["refresh", "login"])
def test_a_reset_revokes_the_token_a_refresh_or_login_is_creating(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch, via: str
) -> None:
    """A refresh or login has inserted its new refresh token but not committed when a reset
    starts. The reset waits on the user lock the refresh or login holds, then revokes the new
    token too. Without that lock the reset's revoke can't see the uncommitted row, and the
    token stays usable after the reset. The lock order is checked by
    test_a_refresh_waits_for_a_reset_without_deadlocking."""
    inserted, go_on = threading.Event(), threading.Event()

    def insert_then_wait(
        owner: UUID, db: Session, family_id: UUID | None = None
    ) -> tuple[str, UUID]:
        created = create_refresh_token(owner, db, family_id)
        inserted.set()  # flushed, not committed
        _wait(go_on)
        return created

    module = "app.accounts.tokens" if via == "refresh" else "app.accounts.login"
    monkeypatch.setattr(f"{module}.create_refresh_token", insert_then_wait)
    with _committed_student(test_engine) as (user, refresh_token, link):
        form = OAuth2PasswordRequestForm(username=user.email, password=OLD_PASSWORD)

        def sign_in(session: Session) -> TokenPair:
            if via == "refresh":
                return rotate_tokens(refresh_token, session)
            return login_account(form, session)

        signing_in = _InThread(test_engine, sign_in)
        resetting: _InThread | None = None
        try:
            _wait(inserted)
            resetting = _InThread(
                test_engine, lambda session: reset_password(link, NEW_PASSWORD, session)
            )
            waited = _blocked(test_engine, resetting)
        finally:
            go_on.set()
            try:
                outcome = signing_in.finish()
            finally:  # even if the sign-in failed, so cleanup never waits on its locks
                if resetting is not None:
                    resetting.finish()

        assert isinstance(outcome, TokenPair)
        assert _tokens(test_engine, user) == [True, True]  # the new token is revoked too
        assert waited, "the reset did not wait on a lock"


def test_a_login_checked_before_a_reset_is_refused_after_it(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old password passes bcrypt, then a reset commits before the login goes on: the
    login reads the hash again under the user lock and is refused, so it adds no token."""
    checked, go_on = threading.Event(), threading.Event()
    check = login_module.verified_password_hash

    def checked_then_wait(email: str, password: str, db: Session) -> str | None:
        stored = check(email, password, db)
        checked.set()
        _wait(go_on)
        return stored

    monkeypatch.setattr("app.accounts.login.verified_password_hash", checked_then_wait)
    with _committed_student(test_engine) as (user, _, link):
        form = OAuth2PasswordRequestForm(username=user.email, password=OLD_PASSWORD)
        signing_in = _InThread(test_engine, lambda session: login_account(form, session))
        try:
            _wait(checked)
            with Session(test_engine) as reset:
                reset_password(link, NEW_PASSWORD, reset)
                reset.commit()
        finally:
            go_on.set()
            outcome = signing_in.finish()

        assert isinstance(outcome, AppError)
        assert outcome.code == ErrorCode.INVALID_CREDENTIALS
        assert _tokens(test_engine, user) == [True]  # only the one the reset revoked


def test_a_refresh_waits_for_a_reset_without_deadlocking(
    test_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reset holds the user and is about to revoke; a refresh then must wait on the user,
    not lock its token first. If it took the token first, the reset's revoke would wait on the
    token and the refresh on the user: a deadlock, which Postgres would end with an error."""
    revoking, go_on = threading.Event(), threading.Event()
    revoke = email_links_module.revoke_refresh_tokens

    def wait_then_revoke(user_id: UUID, db: Session) -> None:
        revoking.set()
        _wait(go_on)
        revoke(user_id, db)

    monkeypatch.setattr("app.accounts.email_links.revoke_refresh_tokens", wait_then_revoke)
    with _committed_student(test_engine) as (user, refresh_token, link):
        resetting = _InThread(
            test_engine, lambda session: reset_password(link, NEW_PASSWORD, session)
        )
        refreshing: _InThread | None = None
        try:
            _wait(revoking)  # the reset holds the user's lock
            refreshing = _InThread(
                test_engine, lambda session: rotate_tokens(refresh_token, session)
            )
            assert _blocked(test_engine, refreshing), "the refresh never waited on a lock"
        finally:
            go_on.set()
            try:
                resetting.finish()
            finally:  # even if the reset failed, so cleanup never waits on its locks
                outcome = refreshing.finish() if refreshing is not None else None

        assert isinstance(outcome, AppError)
        assert outcome.code == ErrorCode.NOT_AUTHENTICATED
        assert _tokens(test_engine, user) == [True]


def test_overlong_password_is_rejected(client: TestClient, student: User):
    overlong = "A1" * 50  # 100 bytes: past bcrypt's 72-byte limit
    for email in (VALID_EMAIL, "nobody@email.com"):
        response = login(client, email=email, password=overlong)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS
