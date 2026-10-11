import re
import zoneinfo
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.accounts.password import hash_password, validate_password_security
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import UserRole


def register_account(
    email: str,
    username: str,
    password: str,
    db: Session,
    class_code: str | None = None,
) -> None:
    try:
        validate_password_security(password)

        if not email_is_valid(email):
            raise AppError(
                ErrorCode.INVALID_CREDENTIALS, "The email you have entered is not valid."
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

        #  TODO: Validate access code here

        hashed_password = hash_password(password)
        timezone = zoneinfo.ZoneInfo("America/Los_Angeles")
        timestamp = datetime.now(timezone)
        stmt = insert(User).values(
            email=email,
            username=username,
            password_hash=str(hashed_password),
            role=UserRole.STUDENT,
            can_manage_content=False,
            created_at=timestamp,
            updated_at=timestamp,
        )
        db.execute(stmt)
        db.commit()
    except AppError as err:
        raise AppError(err.code, err.message, err.details) from err
    except Exception as err:
        raise AppError(ErrorCode.INTERNAL_ERROR, "Something went wrong on our server.") from err


def username_exists(username: str, db: Session) -> bool:
    """Returns True if username is found in the db, and False if there were no results."""
    query = select(User.username).where(User.username == username).limit(1)
    result = db.execute(query)  #  Usernames are unique, only need first
    for row in result:
        return row[0] == username
    return False


def email_exists(email: str, db: Session) -> bool:
    """Returns True if email is found in the db, and False if there were no results."""
    query = select(User.email).where(User.email == email).limit(1)
    result = db.execute(query)  #  Emails are unique, only need first
    for row in result:
        return row[0] == email
    return False


def email_is_valid(email: str) -> bool:
    """Checks for properly formatted email. Returns True if email passes regex test."""
    pattern = "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    match = re.match(pattern, email)
    return bool(match)
