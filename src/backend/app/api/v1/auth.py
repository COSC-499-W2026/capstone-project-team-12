from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.accounts.login import login_account
from app.accounts.register import RegisterRequest, register_account
from app.accounts.tokens import TokenResponse, rotate_tokens
from app.db import get_db
from app.errors import AppError, ErrorCode, error_responses

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


@auth_router.post(
    "/login",
    tags=["login"],
    status_code=200,
    operation_id="loginUser",
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def login(
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Sign in with email and password (all roles)

    Returns an access token (expiry 60 minutes) and a refresh token (expiry 14 days).
    """
    tokens = login_account(form_data, db)
    response.set_cookie("refresh_token", tokens["refresh_token"])
    return TokenResponse(token=tokens["access_token"], token_type="bearer")


@auth_router.post(
    "/refresh",
    tags=["refresh"],
    status_code=200,
    operation_id="refreshTokens",
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def refresh(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """Rotates refresh token of user and returns a new access token and refresh token"""
    if refresh_token is None:
        raise AppError(ErrorCode.NOT_AUTHENTICATED, "Invalid token: please sign in again")
    else:
        tokens = rotate_tokens(refresh_token, db)
        response.set_cookie("refresh_token", tokens["refresh_token"])
        return TokenResponse(token=tokens["access_token"], token_type="bearer")
