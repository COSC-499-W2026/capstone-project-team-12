from fastapi import APIRouter, Depends

from sqlalchemy.orm import Session

from app.accounts.register import RegisterRequest, register_account
from app.db import get_db

auth_router = APIRouter(prefix="/auth")


@auth_router.post("/register", tags=["register"], status_code=201)
def register(request: RegisterRequest) -> dict[str, str]:
    """Student sign-up: email, username, password, optional access code."""
    db: Session = Depends(get_db)
    register_account(request, db)
    return {"status": "ok"}
