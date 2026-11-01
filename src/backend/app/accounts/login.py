"""Module used for all user accounts to sign-in."""

import hashlib
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.password import check_password_hash
from app.accounts.tokens import create_access_token, create_refresh_token
from app.errors import AppError, ErrorCode
from app.models.accounts import User


def login_account(form_data: OAuth2PasswordRequestForm, db: Session) -> dict[str, str]:
    """Validates user credentials and returns a JWT access token"""
    email = form_data.username.lower()  #  email is entered in username field
    check_password_hash(email, form_data.password, db)

    user = db.scalars(select(User).where(User.email == email)).one_or_none()
    if user is None:
        raise AppError(
            ErrorCode.INVALID_CREDENTIALS, "The email or password you entered are incorrect."
        )
    if not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED, "Your account has been disabled.")
    access_token = create_access_token({"sub": str(user.id)})
    refresh_token = create_refresh_token(user.id, db)

    return {"access_token": access_token, "refresh_token": refresh_token}

    raise AppError(
        ErrorCode.INVALID_CREDENTIALS, "The username or email you provided is not correct."
    )


def get_login_key(request: Request) -> str:
    ip = get_remote_address(request)
    email = getattr(request.state, "login_email", "")

    #  Hashes the email so it is not stored in the rate-limit key
    email_hash = hashlib.sha256(email.strip().casefold().encode()).hexdigest()
    return f"login:{ip}:{email_hash}"


# Parses the form and sets the request state variable so that slowapi can read from it
def get_login_form(
    request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> OAuth2PasswordRequestForm:
    request.state.login_email = form.username
    return form
