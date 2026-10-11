from fastapi import APIRouter, Depends, Response

from app.accounts.login import LoginRequest, login_account
from app.accounts.register import RegisterRequest, register_account
from app.accounts.tokens import RefreshRequest, rotate_refresh_token
from app.db import get_db

router = APIRouter(prefix="/auth")


@router.post("/register", tags=["register"], status_code=201)
def register(request: RegisterRequest) -> dict[str, str]:
    """Student sign-up: email, username, password, optional access code."""
    register_account(request, Depends(get_db))
    return {"status": "ok"}


@router.post("/login", tags=["login"], status_code=200)
def login(request: LoginRequest, response: Response) -> dict[str, str | bytes]:
    """Sign in with email and password (all roles)"""
    tokens = login_account(request, Depends(get_db))
    return tokens


@router.post("/refresh", tags=["refresh"], status_code=200)
def refresh(request: RefreshRequest, response: Response) -> dict[str, str | bytes] | None:
    """Rotates refresh token of user and returns a new access token and refresh token"""
    tokens = rotate_refresh_token(request, Depends(get_db))
    return tokens
