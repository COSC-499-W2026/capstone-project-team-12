from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts.login import LoginRequest, login_account
from app.accounts.register import RegisterRequest, register_account
from app.accounts.tokens import RefreshRequest, rotate_refresh_token
from app.db import get_db

auth_router = APIRouter(prefix="/auth")


@auth_router.post("/register", tags=["register"], status_code=201)
def register(request: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Student sign-up: email, username, password, optional access code."""
    register_account(request, db)
    db.commit()
    return {"status": "ok"}


@router.post("/login", tags=["login"], status_code=200)
def login(request: LoginRequest, response: Response) -> dict[str, str | bytes] | None:
    """Sign in with email and password (all roles)"""
    tokens = login_account(request, Depends(get_db))
    return tokens


@router.post("/refresh", tags=["refresh"], status_code=200)
def refresh(request: RefreshRequest, response: Response) -> dict[str, str | bytes] | None:
    """Rotates refresh token of user and returns a new access token and refresh token"""
    tokens = rotate_refresh_token(request, Depends(get_db))
    return tokens
