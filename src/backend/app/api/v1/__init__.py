"""Version 1 API. Each feature area adds its own router here (auth, me, games, classes, ...)."""

from http import HTTPStatus

from fastapi import APIRouter

from app.api.v1.auth import auth_router
from app.api.v1.classes import (
    leaderboard_router,
    teacher_access_codes_router,
    teacher_classes_router,
)
from app.api.v1.content import content_router
from app.api.v1.email_links import email_links_router
from app.api.v1.games import games_router
from app.api.v1.me import me_classes_router, me_router
from app.api.v1.media import media_router
from app.api.v1.verification import verification_router
from app.errors import error_responses

# every route can fail validation and hit the global rate limit; this replaces FastAPI's default
# 422 schema with ours
api_router = APIRouter(
    prefix="/api/v1",
    responses=error_responses(HTTPStatus.UNPROCESSABLE_ENTITY, HTTPStatus.TOO_MANY_REQUESTS),
)
api_router.include_router(auth_router)
api_router.include_router(me_classes_router)
api_router.include_router(leaderboard_router)
api_router.include_router(teacher_classes_router)
api_router.include_router(teacher_access_codes_router)
api_router.include_router(content_router)
api_router.include_router(email_links_router)
api_router.include_router(games_router)
api_router.include_router(me_router)
api_router.include_router(media_router)
api_router.include_router(verification_router)
