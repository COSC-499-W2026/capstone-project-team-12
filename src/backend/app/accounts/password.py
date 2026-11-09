"""Module used for the verification and hashing of passwords."""

from bcrypt import checkpw, gensalt, hashpw
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import User

_DUMMY_HASH = hashpw(b"DummyPassword", gensalt(12))


def validate_password_security(password: str) -> None:
    """Returns a list of criteria that the password did not meet.
    This list will be empty if all requirements passed
    """

    if len(password) < 8:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Password must contain minimum 8 characters")

    if len(password.encode("utf-8")) > 70:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Password must contain less than 70 characters.")

    if password.isdigit():
        raise AppError(ErrorCode.VALIDATION_ERROR, "Password must contain at least one letter")

    if not any(char.isdigit() for char in password):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Password must contain at least one number")

    if not any(char.isupper() for char in password):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Password must contain at least one capital")


def hash_password(password: str) -> str:
    """Takes a plaintext password and returns the bcrypt hashed password."""
    bytes = password.encode("utf-8")
    salt = gensalt(12)
    hashed = hashpw(bytes, salt)
    return hashed.decode("utf-8")


def check_password_hash(email: str, password: str, db: Session) -> bool:
    """Takes a plaintext password and checks it agaist the stored hash for the user"""
    return verified_password_hash(email, password, db) is not None


def verified_password_hash(email: str, password: str, db: Session) -> str | None:
    """The stored hash the password matched, or None if it didn't.

    Read without a lock: bcrypt is slow, so a caller that must not act on a password changed in
    the meantime locks the user afterwards and checks the hash is still this one (login).
    """
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    password_bytes = password.encode()
    # bcrypt raises past 72 bytes, and registration caps passwords at 70, so longer is never right
    if user is None or len(password_bytes) > 72:
        checkpw(password_bytes[:72], _DUMMY_HASH)  # Dummy check so timing does not reveal why
        return None
    stored = str(user.password_hash)
    return stored if checkpw(password_bytes, stored.encode()) else None
