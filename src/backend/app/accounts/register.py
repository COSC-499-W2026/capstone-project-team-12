import re

from pydantic import BaseModel
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.password import hash_password, validate_password_security
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import EmailKind, UserRole
from app.services.email import EmailService


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    access_code: str | None = None


def register_account(request: RegisterRequest, db: Session) -> None:
    try:
        email = request.email.lower()
        username = request.username
        password = request.password

        validate_password_security(password)

        if not email_is_valid(email):
            raise AppError(ErrorCode.VALIDATION_ERROR, "The email you have entered is not valid.")

        if not username_is_valid(username):
            raise AppError(
                ErrorCode.VALIDATION_ERROR, "The username you have entered is not valid."
            )

        if username_exists(username, db):
            raise AppError(
                ErrorCode.USERNAME_TAKEN, "The username you have chosen is already in use."
            )

        if email_exists(email, db):
            raise AppError(
                ErrorCode.EMAIL_TAKEN,
                "The email you have chosen is already associated with an account",
            )

        hashed_password = hash_password(password)
        stmt = insert(User).values(
            email=email,
            username=username,
            password_hash=hashed_password,
            role=UserRole.STUDENT,
            can_manage_content=False,
        )
        user = db.scalars(stmt.returning(User)).one()
        EmailService.queue_email(user, EmailKind.VERIFY_EMAIL, db)  # spec: sign-up sends it

        #  TODO: Validate access code here

    except IntegrityError as err:
        diag = getattr(err.orig, "diag", None)
        constraint = getattr(diag, "constraint_name", None)
        if constraint == "uq_users_email":
            raise AppError(
                ErrorCode.EMAIL_TAKEN, "The email you have chosen is already in use."
            ) from err
        elif constraint == "uq_users_username_lower":
            raise AppError(
                ErrorCode.USERNAME_TAKEN, "The username you have chosen is already in use."
            ) from err
        # any other constraint is a real error
        raise


def username_exists(username: str, db: Session) -> bool:
    """Returns True if username is found in the db, and False if there were no results."""
    query = select(User.username).where(func.lower(User.username) == username.lower()).limit(1)
    result = db.execute(query)  #  Usernames are unique, only need first
    for row in result:
        return row[0].lower() == username.lower()
    return False


def username_is_valid(username: str) -> bool:
    return (
        len(username) >= 3
        and len(username) <= 30
        and not username.endswith(" ")
        and not username.startswith(" ")
    )


def email_exists(email: str, db: Session) -> bool:
    """Returns True if email is found in the db, and False if there were no results."""
    query = select(User.email).where(User.email == email).limit(1)
    result = db.execute(query)  #  Emails are unique, only need first
    for row in result:
        return row[0] == email
    return False


def email_is_valid(email: str) -> bool:
    """Checks for properly formatted email. Returns True if email passes regex test."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    match = re.fullmatch(pattern, email)
    return bool(match)
