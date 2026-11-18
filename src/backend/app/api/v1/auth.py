from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.accounts.login import login_account
from app.accounts.logout import logout_session
from app.accounts.register import RegisterRequest, RegisterResponse, register_account
from app.accounts.tokens import REFRESH_TOKEN_LIFETIME, TokenResponse, rotate_tokens
from app.api.rate_limit import login_rate_limit
from app.config import get_settings
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
    return RegisterResponse(id=user.id, username=user.username, email=user.email, role=user.role)


@auth_router.post(
    "/login",
    tags=["login"],
    status_code=200,
    operation_id="loginUser",
    responses=error_responses(HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN),
    dependencies=[Depends(login_rate_limit)],
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
    db.commit()
    _set_refresh_cookie(response, tokens.refresh_token)
    return TokenResponse(access_token=tokens.access_token)


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
        try:
            tokens = rotate_tokens(refresh_token, db)
            _set_refresh_cookie(response, tokens.refresh_token)
            db.commit()
            return TokenResponse(access_token=tokens.access_token)
        except AppError:
            # a replayed token is refused, but revoking its family must still be saved
            db.commit()
            raise


@auth_router.post(
    "/logout",
    tags=["logout"],
    status_code=204,
    operation_id="logoutUser",
)
def logout(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> None:
    """Signs out this device: revokes the sign-in named by the refresh cookie and clears the cookie.

    No access token is needed, so this works after the access token has expired, and for a
    disabled account. The user's other devices stay signed in. Always returns 204, also when the
    cookie is missing, unknown or already revoked, so a client can always finish logging out.
    """
    logout_session(refresh_token, db)
    db.commit()
    _delete_refresh_cookie(response)


def _refresh_cookie_options() -> dict[str, Any]:
    """Options shared by setting and deleting the refresh cookie. A browser only deletes a cookie
    when the delete matches the cookie it holds (path, domain, secure, samesite), so both must use
    the same options."""
    return {
        "key": "refresh_token",
        "path": "/api/v1/auth",
        "httponly": True,
        "secure": get_settings().refresh_cookie_secure,
        "samesite": "strict",
    }


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        value=token,
        max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()),  # same lifetime as the DB row
        **_refresh_cookie_options(),
    )


def _delete_refresh_cookie(response: Response) -> None:
    response.delete_cookie(**_refresh_cookie_options())
