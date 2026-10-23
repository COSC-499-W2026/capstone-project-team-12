import os
import zoneinfo
import hashlib
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from bcrypt import checkpw, gensalt, hashpw
from jwt import decode, encode
from pydantic import BaseModel
from sqlalchemy import desc, insert, select, update
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import RefreshToken

ACCESS_TOKEN_KEY = os.getenv("ACCESS_TOKEN_KEY")
REFRESH_TOKEN_KEY = os.getenv("REFRESH_TOKEN_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRES_MINUTES = 60
REFRESH_TOKEN_EXPIRES_DAYS = 14


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str


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
    expire = now() + timedelta(ACCESS_TOKEN_EXPIRES_MINUTES)
    data.update({"exp": expire})
    return encode_jwt(data, timedelta(ACCESS_TOKEN_EXPIRES_MINUTES), str(ACCESS_TOKEN_KEY))


def create_refresh_token(user_id: UUID, db: Session, family_id: UUID | None = None) -> str:
    """Creates a new refresh token. Pass a family_id if the new token should use other
    tokens' family_id"""
    token_id = uuid4()
    created = now()
    expires = now() + timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS)
    new_family_id = family_id or uuid4()
    jwt = encode_jwt(
        {"sub": str(user_id)}, timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS), str(REFRESH_TOKEN_KEY)
    )
    jwt_hash = hashlib.sha256(jwt.encode()).hexdigest()
    stmt = insert(RefreshToken).values(
        id=token_id,
        user_id=user_id,
        family_id=new_family_id,
        token_hash=jwt_hash,
        created_at=created,
        expires_at=expires,
    )
    db.execute(stmt)
    db.commit()
    return jwt


def rotate_tokens(token: str, db: Session) -> TokenResponse:
    """Rotates the provided token represented by token_id, then creates and returns a new
    JWT access code and refresh token in the same family."""
    valid_token_id = validate_refresh_token(token, db)
    if valid_token_id is None:
        raise AppError(ErrorCode.NOT_AUTHENTICATED)
    result = db.execute(
        select(RefreshToken.user_id, RefreshToken.family_id).where(
            RefreshToken.id == valid_token_id
        )
    )

    user_id: UUID
    family_id: UUID
    if result:
        for row in result:
            user_id = row[0]
            family_id = row[1]

    db.execute(
        update(RefreshToken).where(RefreshToken.id == valid_token_id).values(rotated_at=now())
    )

    db.commit()

    new_access_token = create_access_token({"sub": str(user_id)})
    new_refresh_token = create_refresh_token(user_id, db, family_id)

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token
    )


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
            if (
                row[1] > now()
                and row[2] is None
                and row[3] is None
                and row[4] == hash
            ):
                return id
    return None


def revoke_refresh_tokens(user_id: UUID, db: Session) -> None:
    """Revokes all refresh tokens for the given user_id"""
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked_at=now())
    db.execute(stmt)
    db.commit()


def now() -> datetime:
    """Returns current datetime in Pacific Timezone"""
    return datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
