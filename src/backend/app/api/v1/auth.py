from http import HTTPStatus
from typing import Annotated

<<<<<<< HEAD
from fastapi import APIRouter, Cookie, Depends
=======
from fastapi import APIRouter, Cookie, Depends, Response
>>>>>>> origin/main
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.accounts.login import login_account
<<<<<<< HEAD
from app.accounts.register import RegisterRequest, register_account
=======
from app.accounts.register import RegisterRequest, RegisterResponse, register_account
>>>>>>> origin/main
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
    # EMAIL_TAKEN, USERNAME_TAKEN, INVALID_ACCESS_CODE
    responses=error_responses(HTTPStatus.CONFLICT, HTTPStatus.BAD_REQUEST),
)
def register(request: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> RegisterResponse:
    """Student sign-up: email, username, password, optional access code."""
    user = register_account(request, db)
    db.commit()
<<<<<<< HEAD
    return {"status": "ok"}
=======
    return RegisterResponse(id=user.id, username=user.username, email=user.email, role=user.role)
>>>>>>> origin/main


@auth_router.post(
    "/login",
    tags=["login"],
    status_code=200,
    operation_id="loginUser",
<<<<<<< HEAD
    responses=error_responses(HTTPStatus.UNAUTHORIZED),
)
def login(
=======
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def login(
    response: Response,
>>>>>>> origin/main
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Sign in with email and password (all roles)

    Returns an access token (expiry 60 minutes) and a refresh token (expiry 14 days).
    """
<<<<<<< HEAD
    return login_account(form_data, db)
=======
    tokens = login_account(form_data, db)
    response.set_cookie("refresh_token", tokens["refresh_token"])
    return TokenResponse(access_token=tokens["access_token"])
>>>>>>> origin/main


@auth_router.post(
    "/refresh",
    tags=["refresh"],
    status_code=200,
    operation_id="refreshTokens",
<<<<<<< HEAD
    responses=error_responses(HTTPStatus.UNAUTHORIZED),
)
def refresh(
    db: Annotated[Session, Depends(get_db)], refresh_token: Annotated[str | None, Cookie()] = None
=======
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
)
def refresh(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie()] = None,
>>>>>>> origin/main
) -> TokenResponse:
    """Rotates refresh token of user and returns a new access token and refresh token"""
    if refresh_token is None:
        raise AppError(ErrorCode.NOT_AUTHENTICATED, "Invalid token: please sign in again")
    else:
<<<<<<< HEAD
        return rotate_tokens(refresh_token, db)
=======
        tokens = rotate_tokens(refresh_token, db)
        response.set_cookie("refresh_token", tokens["refresh_token"])
        return TokenResponse(access_token=tokens["access_token"])
>>>>>>> origin/main
