"""Version 1 API. Each feature area adds its own router here (auth, me, games, classes, ...)."""

from fastapi import APIRouter

from app.api.v1.auth import auth_router
from app.api.v1.media import media_router
from app.api.v1.teacher_classes import access_code_router, teacher_classes_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(media_router)
api_router.include_router(teacher_classes_router)
api_router.include_router(access_code_router)
