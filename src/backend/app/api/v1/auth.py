from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts.register import RegisterRequest, RegisterResponse, register_account
from app.db import get_db
from app.errors import error_responses

auth_router = APIRouter(prefix="/auth")


@auth_router.post(
    "/register",
    tags=["register"],
    status_code=201,
    operation_id="registerUser",
    # EMAIL_TAKEN, USERNAME_TAKEN, INVALID_ACCESS_CODE
    responses=error_responses(HTTPStatus.CONFLICT, HTTPStatus.BAD_REQUEST),
)
def register(request: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> RegisterResponse:
    """Student sign-up: email, username, password, optional access code."""
    user = register_account(request, db)
    response = RegisterResponse(
        id=user.id, username=user.username, email=user.email, role=user.role
    )
    return response
