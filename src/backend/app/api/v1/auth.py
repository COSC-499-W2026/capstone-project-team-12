from fastapi import APIRouter, Depends

from app.accounts.register import RegisterRequest, register_account
from app.db import get_db

router = APIRouter(prefix="/auth")


@router.post("/register", tags=["register"], status_code=201)
def register(request: RegisterRequest) -> dict[str, str]:
    """Student sign-up: email, username, password, optional access code."""
    register_account(request, Depends(get_db()))
    return {"status": "ok"}

@router.post("/login", tags=["login"], status_code=200)
def login(request: AuthRequest.LoginRequest, response: Response) -> dict[str, str | bytes]:
    """Sign in with email and password (all roles)"""
    tokens = validate_login(request, next(get_db()))
    return tokens

@router.post("/refresh", tags=["refresh"], status_code=200)
def refresh(
    request: AuthRequest.RefreshRequest, response: Response
) -> dict[str, str | bytes] | None:
    """Rotates refresh token of user and returns a new access token and refresh token"""
    tokens = rotate_token(request, next(get_db()))
    return tokens