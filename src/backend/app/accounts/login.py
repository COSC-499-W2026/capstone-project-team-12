"""Module used for all user accounts to sign-in."""

import hashlib
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.password import verified_password_hash
from app.accounts.tokens import TokenPair, create_access_token, create_refresh_token
from app.errors import AppError, ErrorCode
from app.models.accounts import User

# one answer for an unknown email and a wrong password, so neither gives the other away
_WRONG_LOGIN = "The email or password you entered is incorrect."


def login_account(form_data: OAuth2PasswordRequestForm, db: Session) -> TokenPair:
    """Validates user credentials and returns a JWT access token"""
    email = form_data.username.lower()  #  email is entered in username field
    verified = verified_password_hash(email, form_data.password, db)
    if verified is None:
        raise AppError(ErrorCode.INVALID_CREDENTIALS, _WRONG_LOGIN)

    # The password was checked without a lock (bcrypt is slow). Lock the user FOR SHARE, the
    # same as a refresh: a password reset holds it FOR NO KEY UPDATE, so one waits for the other.
    # Then read the hash again: if a reset changed it meanwhile, this login is refused, and a
    # reset that comes later revokes the token this login creates. Don't write the user row
    # here: two logins hold FOR SHARE together and would deadlock upgrading it.
    user = db.scalars(
        select(User).where(User.email == email).with_for_update(read=True),
        execution_options={"populate_existing": True},
    ).one_or_none()
    if user is None or user.password_hash != verified:
        raise AppError(ErrorCode.INVALID_CREDENTIALS, _WRONG_LOGIN)
    if not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED, "Your account has been disabled.")
    access_token = create_access_token({"sub": str(user.id)})
    refresh_token, _ = create_refresh_token(user.id, db)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


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
