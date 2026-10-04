"""Version 1 API. Each feature area adds its own router here (auth, me, games, classes, ...)."""

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
