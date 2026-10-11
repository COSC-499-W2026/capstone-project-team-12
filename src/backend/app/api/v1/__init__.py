"""Version 1 API. Each feature area adds its own router here (auth, me, games, classes, ...)."""

from fastapi import APIRouter

from auth import auth_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)