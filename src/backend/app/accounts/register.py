from datetime import datetime, timezone
import zoneinfo
import re

from sqlalchemy.orm import Session
from sqlalchemy import select, insert, where, Result, Row

from app.db import get_db
from app.errors import AppError, ErrorCode
from app.accounts.password import validate_password_security, hash_password
from app.models.accounts import User
from app.models.enums import UserRole

def register_account(email: str, username: str, password: str, class_code: str | None = None) -> None:
    try:
        db: Session = get_db()

        if len(validate_password_security(password)) > 0:
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "The password you have chosen is not secure enough.")

        if not email_is_valid():
            raise AppError(ErrorCode.INVALID_CREDENTIALS, "The email you have entered is not valid.")
        
        if username_exists():
            raise AppError(ErrorCode.USERNAME_TAKEN, "The username you have chosen is already in use.")

        if email_exists():
            raise AppError(ErrorCode.EMAIL_TAKEN, "The email you have chosen is already associated with an account")
            
        #  TODO: Validate access code here

        hashed_password = hash_password(password)
        timezone = zoneinfo.ZoneInfo("America/Los_Angeles")
        timestamp = datetime.now(timezone)
        stmt = insert(User).values(
            email = email,
            username = username,
            password_hash = hashed_password,
            role = UserRole.STUDENT,
            can_manage_content = False,
            created_at = timestamp,
            updated_at = timestamp
        )
        db.execute(stmt)
        db.commit()
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "Something went wrong on our server.")


def username_exists(username: str, db: Session) -> bool:
    """Returns True if username is found in the db, and False if there were no results."""
    query = select(User.username).where(User.username == username)
    result: Result = db.execute(query).first()  #  Usernames are unique, only need first
    if result is Row:  #  If there is a result, there is a match
        return True
    else:
        return False


def email_exists(email: str, db: Session) -> bool:
    """Returns True if email is found in the db, and False if there were no results."""
    query = select(User.email).where(User.email == email)
    result: Result = db.execute(query).first()  #  Emails are unique, only need first
    if result is Row:  #  If there is a result, there is a match
        return True
    else:
        return False

def email_is_valid(email: str) -> bool:
    """Checks for properly formatted email. Returns True if email passes regex test."""
    pattern = "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    return re.match(pattern, email)