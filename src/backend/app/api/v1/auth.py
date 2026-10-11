from fastapi import APIRouter, Depends

from app.accounts.register import RegisterRequest, register_account
from app.db import get_db

router = APIRouter(prefix="/auth")


@router.post("/register", tags=["register"], status_code=201)
def register(request: RegisterRequest) -> dict[str, str]:
    """Student sign-up: email, username, password, optional access code."""
    register_account(request, Depends(get_db))
    return {"status": "ok"}
