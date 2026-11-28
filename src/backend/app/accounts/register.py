import re
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.password import hash_password, validate_password_security
from app.db import violated_constraint
from app.engagement.interface import validate_code_and_enrol
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import EmailKind, UserRole
from app.services.email import EmailService


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    access_code: str | None = None


class RegisterResponse(BaseModel):
    id: UUID
    username: str
    email: str
    role: UserRole


EMAIL_TAKEN_MESSAGE = "An account with this email address already exists."
USERNAME_TAKEN_MESSAGE = "This username is already in use."
# only deleting an account gives it a username with this prefix (e.g. deleted-user-7f3a...)
DELETED_USERNAME_PREFIX = "deleted-"


def register_account(request: RegisterRequest, db: Session) -> User:
    validate_password_security(request.password)
    user = insert_user(
        request.email,
        request.username,
        request.password,
        role=UserRole.STUDENT,
        can_manage_content=False,
        db=db,
    )
    EmailService.queue_email(user, EmailKind.VERIFY_EMAIL, db)  # spec: sign-up sends it
    if request.access_code is not None:
        validate_code_and_enrol(user.id, request.access_code, db)
    return user


def insert_user(
    email: str,
    username: str,
    password: str | None,
    role: UserRole,
    can_manage_content: bool,
    db: Session,
) -> User:
    """Add an account and return it. Doesn't commit.

    The address is stored trimmed and lowercase; the password is hashed, or left unset when
    None. 422 VALIDATION_ERROR with a detail for each badly formed field; 409 EMAIL_TAKEN or
    USERNAME_TAKEN if any account (deactivated ones too) has the address or the username in
    any case, including when another request takes either one between the check and the insert.
    """
    email = email.strip().lower()
    details = [
        {"in": "body", "field": field, "message": message}
        for field, valid, message in (
            ("email", email_is_valid(email), "Not a valid email address."),
            (
                "username",
                username_is_valid(username),
                "Must be 3 to 30 characters with no space at either end, and not start with"
                f" {DELETED_USERNAME_PREFIX!r}.",
            ),
        )
        if not valid
    ]
    if details:
        raise AppError(ErrorCode.VALIDATION_ERROR, details[0]["message"], details)
    if username_exists(username, db):
        raise AppError(ErrorCode.USERNAME_TAKEN, USERNAME_TAKEN_MESSAGE)
    if email_exists(email, db):
        raise AppError(ErrorCode.EMAIL_TAKEN, EMAIL_TAKEN_MESSAGE)
    try:
        return db.scalars(
            insert(User)
            .values(
                email=email,
                username=username,
                password_hash=None if password is None else hash_password(password),
                role=role,
                can_manage_content=can_manage_content,
            )
            .returning(User)
        ).one()
    except IntegrityError as err:
        # another request took the address or username after the checks above
        constraint = violated_constraint(err)
        if constraint == "uq_users_email":
            raise AppError(ErrorCode.EMAIL_TAKEN, EMAIL_TAKEN_MESSAGE) from err
        if constraint == "uq_users_username_lower":
            raise AppError(ErrorCode.USERNAME_TAKEN, USERNAME_TAKEN_MESSAGE) from err
        raise  # any other constraint is a real error


def username_exists(username: str, db: Session) -> bool:
    """Returns True if username is found in the db, and False if there were no results."""
    query = select(User.username).where(func.lower(User.username) == username.lower()).limit(1)
    result = db.execute(query)  #  Usernames are unique, only need first
    for row in result:
        return row[0].lower() == username.lower()
    return False


def username_is_valid(username: str) -> bool:
    """The prefix of deleted accounts is refused in any case, so no one can look like one."""
    return (
        len(username) >= 3
        and len(username) <= 30
        and not username.endswith(" ")
        and not username.startswith(" ")
        and not username.lower().startswith(DELETED_USERNAME_PREFIX)
    )


def is_deleted(user: User) -> bool:
    """Whether the account was deleted, which anonymised it: it can't be changed any more."""
    return user.username.startswith(DELETED_USERNAME_PREFIX)


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
