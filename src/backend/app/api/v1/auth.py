from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts.interface import can_manage_content, require_admin, require_class_role
from app.accounts.register import RegisterRequest, register_account
from app.db import get_db
from app.models.enums import ClassStaffRole

auth_router = APIRouter(prefix="/auth")
test_router = APIRouter(prefix="/test")
auth_router.include_router(test_router)


@auth_router.post("/register", tags=["register"], status_code=201)
def register(request: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """Student sign-up: email, username, password, optional access code."""
    register_account(request, db)
    db.commit()
    return {"status": "ok"}


#  TEST ROUTES
@test_router.post("/assistant/{class_id}", tags=["test"], status_code=200)
def test_assistant_access(
    require_assistant: Annotated[None, Depends(require_class_role(ClassStaffRole.ASSISTANT))],
) -> None:
    return


@test_router.post("/teacher/{class_id}", tags=["test"], status_code=200)
def test_teacher_access(
    require_teacher: Annotated[None, Depends(require_class_role(ClassStaffRole.OWNER))],
) -> None:
    return


@test_router.post("/content", tags=["test"], status_code=200)
def test_content_manager(
    require_content_manager: Annotated[None, Depends(can_manage_content)],
) -> None:
    return


@test_router.post("/admin", tags=["test"], status_code=200)
def test_admin(require_admin: Annotated[None, Depends(require_admin)]) -> None:
    return
