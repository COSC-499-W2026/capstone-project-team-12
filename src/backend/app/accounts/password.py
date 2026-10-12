"""Module used for the verification and hashing of passwords."""

from bcrypt import gensalt, hashpw

from app.errors import AppError, ErrorCode


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
