"""Module used for creation, rotation, and revocation of access tokens and refresh tokens."""


import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from jwt import decode, encode
from pydantic import BaseModel
from sqlalchemy import desc, insert, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.models.accounts import RefreshToken, User

ACCESS_TOKEN_KEY = get_settings().access_token_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRES_MINUTES = 60
REFRESH_TOKEN_EXPIRES_DAYS = 14


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def encode_jwt(data: dict[str, Any], expires_at: timedelta, key: str) -> str:
    """Creates a new JWT using the provided data."""
    expire = now() + expires_at
    data.update({"exp": expire})
    encoded_jwt: str = encode(data, key, ALGORITHM)
    return encoded_jwt


def decode_jwt(jwt: str, key: str) -> dict[str, Any]:
    """Decodes provided JWT and returns its contents"""
    decoded_jwt = decode(jwt, key, [ALGORITHM])
    return decoded_jwt


def create_access_token(data: dict[str, Any]) -> str:
    """Creates a new access token with the provided data."""
    expire = now() + timedelta(minutes=ACCESS_TOKEN_EXPIRES_MINUTES)
    data.update({"exp": expire})
    return encode_jwt(data, timedelta(minutes=ACCESS_TOKEN_EXPIRES_MINUTES), str(ACCESS_TOKEN_KEY))


def create_refresh_token(user_id: UUID, db: Session, family_id: UUID | None = None) -> str:
    """Creates a new refresh token. Pass a family_id if the new token should use other
    tokens' family_id"""
    token_id = uuid4()
    created = now()
    expires = now() + timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS)
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
    db.commit()
    return token


def rotate_tokens(token: str, db: Session) -> dict[str, str]:
    """Rotates the provided token represented by token_id, then creates and returns a new
    JWT access code and refresh token in the same family."""
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
    db.execute(
        update(RefreshToken).where(RefreshToken.id == valid_token_id).values(rotated_at=now())
    )

    db.commit()

    new_access_token = create_access_token({"sub": str(user_id)})
    new_refresh_token = create_refresh_token(user_id, db, family_id)

    return {"access_token": new_access_token, "refresh_token": new_refresh_token}


def validate_refresh_token(token: str, db: Session) -> UUID | None:
    """Returns the id of the token if it is valid, and None otherwise"""
    hash = hashlib.sha256(token.encode()).hexdigest()
    result = db.execute(
        select(
            RefreshToken.id,
            RefreshToken.expires_at,
            RefreshToken.revoked_at,
            RefreshToken.rotated_at,
            RefreshToken.token_hash,
        )
        .where(RefreshToken.token_hash == hash)
        .order_by(desc(RefreshToken.created_at))
    )
    if result:
        for row in result:
            id = row[0]
            if row[1] > now() and row[2] is None and row[3] is None and row[4] == hash:
                return id
    return None


def revoke_refresh_tokens(user_id: UUID, db: Session) -> None:
    """Revokes all refresh tokens for the given user_id"""
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked_at=now())
    db.execute(stmt)
    db.commit()


def now() -> datetime:
    return datetime.now(UTC)
