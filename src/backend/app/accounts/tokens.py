"""Module used for creation, rotation, and revocation of access tokens and refresh tokens."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from jwt import encode
from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.accounts import RefreshToken, User

ACCESS_TOKEN_KEY = get_settings().access_token_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRES_MINUTES = 60
REFRESH_TOKEN_LIFETIME = timedelta(days=14)
# A rotated token reused this soon after its rotation is refused without revoking its family:
# two tabs refreshing together, or a retry after a lost response, should not sign the user out
REPLAY_GRACE = timedelta(seconds=30)


# Returned by token functions to for route to handle. Not returned to user
class TokenPair(BaseModel):
    access_token: str
    refresh_token: str


# Access token response returned to user
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def encode_jwt(data: dict[str, Any], expires_at: timedelta, key: str) -> str:
    """Creates a new JWT using the provided data."""
    expire = now() + expires_at
    data.update({"exp": expire})
    encoded_jwt: str = encode(data, key, ALGORITHM)
    return encoded_jwt


def create_access_token(data: dict[str, Any]) -> str:
    """Creates a new access token with the provided data."""
    return encode_jwt(data, timedelta(minutes=ACCESS_TOKEN_EXPIRES_MINUTES), str(ACCESS_TOKEN_KEY))


def create_refresh_token(
    user_id: UUID, db: Session, family_id: UUID | None = None
) -> tuple[str, UUID]:
    """Creates a new refresh token and returns it with its row id. Pass a family_id if the new
    token should use other tokens' family_id"""
    token_id = uuid4()
    created = now()
    expires = created + REFRESH_TOKEN_LIFETIME
    new_family_id = family_id or uuid4()
    # Opaque random value: it is only ever looked up by its hash, never decoded, so it needs no key
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    stmt = insert(RefreshToken).values(
        id=token_id,
        user_id=user_id,
        family_id=new_family_id,
        token_hash=token_hash,
        created_at=created,
        expires_at=expires,
    )
    db.execute(stmt)
    db.flush()
    return token, token_id


def rotate_tokens(token: str, db: Session) -> TokenPair:
    """Rotates the provided token represented by token_id, then creates and returns a new
    JWT access code and refresh token in the same family.

    The user row is locked before the token row, same as a password reset. Otherwise a
    reset can revoke the tokens it can see while this insert is still uncommitted, and
    the new token stays usable after the reset.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    # no lock yet: only which user, so that row can be taken before the token row
    user_id = db.scalar(select(RefreshToken.user_id).where(RefreshToken.token_hash == token_hash))
    if user_id is None:
        raise AppError(ErrorCode.NOT_AUTHENTICATED)
    # reset takes FOR NO KEY UPDATE on the user (consume_token). FOR SHARE waits for it,
    # and the reset waits for this. FOR KEY SHARE would not, so it would not close the race.
    db.execute(select(User.id).where(User.id == user_id).with_for_update(read=True))
    # locks the token and reads it again, so a reset that committed while we waited is seen
    valid_token_id = validate_refresh_token(token, db)
    if valid_token_id is None:
        raise AppError(ErrorCode.NOT_AUTHENTICATED)
    user_id, family_id = db.execute(
        select(RefreshToken.user_id, RefreshToken.family_id).where(
            RefreshToken.id == valid_token_id
        )
    ).one()
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED, "Your account has been disabled.")

    new_refresh_token, new_token_id = create_refresh_token(user_id, db, family_id)

    db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == valid_token_id)
        .values(rotated_at=now(), replaced_by=new_token_id)
    )

    db.flush()

    new_access_token = create_access_token({"sub": str(user_id)})

    return TokenPair(access_token=new_access_token, refresh_token=new_refresh_token)


def validate_refresh_token(token: str, db: Session) -> UUID | None:
    """Returns the id of the token if it is valid, and None otherwise.
    Locks the token's row until the caller's transaction ends, so two refreshes with the same
    token run one after the other and only the first can rotate it."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    ).scalar_one_or_none()

    if result is None:
        return None
    if result.replaced_by is not None:
        if result.rotated_at is not None and now() - result.rotated_at < REPLAY_GRACE:
            return None  # likely a concurrent tab or a retry: refuse, but keep the family
        revoke_token_family(result.family_id, db)
        raise AppError(ErrorCode.NOT_AUTHENTICATED)
    if result.expires_at > now() and result.revoked_at is None and result.rotated_at is None:
        return result.id
    return None


def revoke_refresh_tokens(user_id: UUID, db: Session) -> None:
    """Revokes all refresh tokens for the given user_id.
    This function does not commit: the caller owns the transaction"""
    # tokens revoked earlier keep the time they were revoked
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    db.execute(stmt)
    db.flush()


def revoke_token_family(family_id: UUID, db: Session) -> None:
    """Revokes every refresh token in one family, which is one sign-in on one device.
    This function does not commit: the caller owns the transaction"""
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    db.execute(stmt)
    db.flush()


def now() -> datetime:
    return datetime.now(UTC)
