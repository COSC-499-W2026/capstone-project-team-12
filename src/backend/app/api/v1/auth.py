from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.accounts.login import login_account
from app.accounts.register import RegisterRequest, register_account
from app.accounts.tokens import TokenResponse, rotate_tokens
from app.db import get_db
from app.errors import error_responses

auth_router = APIRouter(prefix="/auth")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


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


@auth_router.post("/login", tags=["login"], status_code=200)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> dict[str, str]:
    """Sign in with email and password (all roles)"""
    tokens: TokenResponse = login_account(form_data, db)

    refresh_token = str(tokens.refresh_token.get("token"))
    response.set_cookie(
        "refresh", refresh_token, secure=True, httponly=True
    )  #  refresh token set through cookie, httponly and secure

    return tokens.access_token  #  Only access token is returned through json


@auth_router.post("/refresh", tags=["refresh"], status_code=200)
def refresh(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh: str | None = Cookie(default=None),
) -> dict[str, str] | None:
    """Rotates refresh token of user and returns a new access token and refresh token"""
    tokens = rotate_tokens(str(refresh), db)

    refresh_token = str(tokens.refresh_token.get("token"))
    response.set_cookie(
        "refresh", refresh_token, secure=True, httponly=True
    )  #  refresh token set through cookie, httponly and secure

    return tokens.access_token  #  Only access token is returned through json
