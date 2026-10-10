"""Public interface of app.accounts: the only module other components may import.

Planned (Interface card #10): get_current_user, require_class_role, can_manage_content and
is_ta_in_language. Agree on each signature with its users before adding it here.
"""

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounts.register import register_account

__all__ = ["RegisterRequest", "student_register"]


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str
    access_code: str | None = None


def student_register(request: RegisterRequest, db: Session) -> None:
    """Requests to register an account in the database"""
    register_account(request.email, request.username, request.password, db, request.access_code)
