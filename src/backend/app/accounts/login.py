"""Module used for all user accounts to sign-in."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.password import check_password_hash
from app.accounts.tokens import new_access_token, new_refresh_token
from app.models.accounts import User


class LoginRequest(BaseModel):
    email: str
    password: str
    access_token: str | None


def login_account(request: LoginRequest, db: Session) -> dict[str, str | bytes]:
    """Validates user credentials and returns a JWT access token"""
    check_password_hash(request.email.lower(), request.password, db)

    stmt = select(User.id).where(User.email == request.email.lower())
    result = db.execute(stmt)
    if result:
        for row in result:
            user_id = row[0]

    jwt_data: dict[str, UUID | datetime] = {"id": user_id}
    encoded_jwt = new_access_token(jwt_data)
    encoded_refresh_token = new_refresh_token(user_id, db)
    return {"jwt": encoded_jwt, "refresh_token": encoded_refresh_token}
