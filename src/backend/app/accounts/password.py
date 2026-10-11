"""Module used for the verification and hashing of passwords."""

from bcrypt import checkpw, gensalt, hashpw
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import User


def validate_password_security(password: str) -> None:
    """Returns a list of criteria that the password did not meet.
    This list will be empty if all requirements passed
    """

    if len(password) < 8:
        raise AppError(ErrorCode.INVALID_CREDENTIALS, "Password must contain minimum 8 characters")

    if password.isdigit():
        raise AppError(ErrorCode.INVALID_CREDENTIALS, "Password must contain at least one number")

    if not any(char.isdigit() for char in password):
        raise AppError(ErrorCode.INVALID_CREDENTIALS, "Password must contain at least one letter")

    if not any(char.isupper() for char in password):
        raise AppError(ErrorCode.INVALID_CREDENTIALS, "Password must contain at least one capital")


def hash_password(password: str) -> bytes:
    """Takes a plaintext password and returns the bcrypt hashed password."""
    bytes = password.encode("utf-8")
    salt = gensalt(12)
    hashed = hashpw(bytes, salt)
    return hashed


def check_password_hash(email: str, password: str, db: Session) -> bool:
    """Takes a plaintext password and checks it agaist the stored hash for the user"""
    pw_bytes = password.encode("utf-8")
    try:
        stmt = select(User.email, User.password_hash).where(User.email == email)
        result = db.execute(stmt)
        for row in result:
            if row[0] == email and checkpw(pw_bytes, str(row[1]).encode("utf-8")):
                return True
            else:
                raise AppError(
                    ErrorCode.INVALID_CREDENTIALS,
                    "The email or password you entered are incorrect.",
                )
    except AppError as err:
        raise AppError(err.code, err.message, err.details) from err
    except Exception as err:
        raise AppError(
            ErrorCode.INTERNAL_ERROR, "Something went wrong when authenticating you"
        ) from err
    return False
