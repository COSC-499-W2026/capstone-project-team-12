from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts.register import RegisterRequest, register_account
from app.db import get_db
from app.errors import error_responses

auth_router = APIRouter(prefix="/auth")


@auth_router.post(
    "/register",
    tags=["register"],
    status_code=201,
    operation_id="registerUser",
    responses=error_responses(HTTPStatus.CONFLICT),  # EMAIL_TAKEN, USERNAME_TAKEN
)
def register(request: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Student sign-up: email, username, password, optional access code."""
    register_account(request, db)
    db.commit()
    return {"status": "ok"}
