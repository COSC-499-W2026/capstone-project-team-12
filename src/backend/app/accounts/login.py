"""Module used for all user accounts to sign-in."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.password import check_password_hash
from app.accounts.tokens import new_access_token, new_refresh_token
from app.models.accounts import User


def login_account(
    email: str, password: str, db: Session, access_token: str | None = None
) -> dict[str, str | bytes]:
    """Validates user credentials and returns a JWT access token"""
    check_password_hash(email, password, db)

    stmt = select(User.id).where(User.email == email)
    result = db.execute(stmt)
    if result:
        for row in result:
            user_id = row[0]

    jwt_data: dict[str, UUID | datetime] = {"id": user_id}
    encoded_jwt = new_access_token(jwt_data)
    encoded_refresh_token = new_refresh_token(user_id, db)
    return {"jwt": encoded_jwt, "refresh_token": encoded_refresh_token}
