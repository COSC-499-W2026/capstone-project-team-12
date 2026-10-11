"""Public interface of app.accounts: the only module other components may import.

Planned (Interface card #10): get_current_user, require_class_role, can_manage_content and
is_ta_in_language. Agree on each signature with its users before adding it here.
"""

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounts.login import login_account
from app.accounts.register import register_account
from app.accounts.tokens import revoke_refresh_tokens, rotate_refresh_token

__all__ = [
    "AuthRequest",
    "login_account",
    "register_account",
    "rotate_refresh_token",
]


class AuthRequest:
    class LoginRequest(BaseModel):
        email: str
        password: str

    class RegisterRequest(BaseModel):
        email: str
        username: str
        password: str
        access_code: str | None = None

    class RefreshRequest(BaseModel):
        token_id: UUID


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str


#  /auth/register
def student_register(request: AuthRequest.RegisterRequest, db: Session) -> None:
    """Requests to register an account in the database"""
    register_account(request.email, request.username, request.password, db, request.access_code)


#  /auth/login
def validate_login(request: AuthRequest.LoginRequest, db: Session) -> dict[str, str | bytes]:
    """Authenticates the user using email and password"""
    return login_account(request.email, request.password, db)


#  /auth/refresh
def rotate_token(request: AuthRequest.RefreshRequest, db: Session) -> dict[str, str | bytes] | None:
    return rotate_refresh_token(request.token_id, db)


def revoke_all_refresh_tokens(user_id: UUID, db: Session) -> None:
    revoke_refresh_tokens(user_id, db)
