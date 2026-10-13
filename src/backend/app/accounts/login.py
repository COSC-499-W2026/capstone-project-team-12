"""Module used for all user accounts to sign-in."""

from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.password import check_password_hash
from app.accounts.tokens import TokenResponse, create_access_token, create_refresh_token
from app.errors import AppError, ErrorCode
from app.models.accounts import User


def login_account(form_data: OAuth2PasswordRequestForm, db: Session) -> TokenResponse:
    """Validates user credentials and returns a JWT access token"""
    email = form_data.username.lower()  #  email is entered in username field
    check_password_hash(email, form_data.password, db)

    stmt = select(User.id).where(User.email == email).limit(1)
    result = db.execute(stmt)
    if result:
        for row in result:
            user_id = row[0]
            jwt_data = {"sub": user_id}
            new_access_token = create_access_token(jwt_data)
            new_refresh_token = create_refresh_token(user_id, db)
            return TokenResponse(
                access_token={"token": new_access_token, "type": "bearer"},
                refresh_token={"token": new_refresh_token, "type": "refresh"},
            )

    raise AppError(
        ErrorCode.INVALID_CREDENTIALS, "The username or email you provided is not correct."
    )
