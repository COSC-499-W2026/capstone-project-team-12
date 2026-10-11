import os
import zoneinfo
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from bcrypt import gensalt, hashpw
from jwt import decode, encode
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import RefreshToken

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRES_MINUTES = 60
REFRESH_TOKEN_EXPIRES_DAYS = 14


def new_access_token(data: dict[str, UUID | datetime]) -> str:
    """Creates a new access token using the provided data."""
    expire = now() + timedelta(minutes=ACCESS_TOKEN_EXPIRES_MINUTES)
    data.update({"exp": expire})
    encoded_jwt: str = encode(data, SECRET_KEY, ALGORITHM)
    return encoded_jwt


def new_refresh_token(user_id: UUID, db: Session, family_id: UUID | None = None) -> bytes:
    """Creates a new refresh token. Pass a family_id if the new token should use other
    tokens' family_id"""
    token_id = uuid4()
    created = now()
    expires = now() + timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS)

    new_family_id = family_id or uuid4()

    id_bytes = str(token_id).encode("utf-8")
    salt = gensalt(12)
    hash: bytes = hashpw(id_bytes, salt)

    stmt = insert(RefreshToken).values(
        id=token_id,
        user_id=user_id,
        family_id=new_family_id,
        token_hash=hash,
        created_at=created,
        expires_at=expires,
    )

    result = db.execute(stmt)
    if result:
        db.commit()

    return hash


def rotate_refresh_token(token_id: UUID, db: Session) -> dict[str, str | bytes] | None:
    """Rotates the provided token represented by token_id, then creates and returns a new
    JWT access code and refresh token in the same family."""
    if not validate_refresh_token(token_id, db):
        raise AppError(ErrorCode.INVALID_TOKEN, "Your session has expired. Please log-in again.")
    update_stmt = update(RefreshToken).where(RefreshToken.id == token_id).values(rotated_at=now())
    result = db.execute(update_stmt)
    if result:
        db.commit()
    select_stmt = select(RefreshToken.user_id, RefreshToken.family_id).where(
        RefreshToken.id == token_id
    )
    result = db.execute(select_stmt)

    for row in result:
        user_id = row[0]
        family_id = row[1]

    new_ref_token = new_refresh_token(user_id, db, family_id)
    data = {"id": user_id}
    new_acc_token = new_access_token(data)
    return {"jwt": new_acc_token, "refresh_token": new_ref_token}


def validate_access_token(jwt: str, user_id: UUID) -> bool:
    """Returns True if access token is authentic for the provided user."""
    decoded_jwt = decode(jwt)
    expires = decoded_jwt.pop("exp")
    return decoded_jwt.get("id") == user_id and expires > now()


def validate_refresh_token(token_id: UUID, db: Session) -> bool:
    """Returns True if refresh token is still active (has not been expired or revoked)."""
    stmt = select(RefreshToken.expires_at, RefreshToken.revoked_at).where(
        RefreshToken.id == token_id
    )
    result = db.execute(stmt)
    if result:
        for row in result:
            expires_at: datetime = row[0]
            revoked_at = row[1]
        return expires_at > now() and revoked_at is None
    return False


def revoke_refresh_tokens(user_id: UUID, db: Session) -> None:
    """Revokes all refresh tokens for the given user_id"""
    stmt = update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked_at=now())
    result = db.execute(stmt)
    if result:
        db.commit()


def now() -> datetime:
    return datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
