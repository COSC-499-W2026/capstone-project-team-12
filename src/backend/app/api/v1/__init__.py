"""Version 1 API. Each feature area adds its own router here (auth, me, games, classes, ...)."""

from http import HTTPStatus

from fastapi import APIRouter

from app.api.body_limit import document_body_limit
from app.api.v1.admin import admin_router
from app.api.v1.auth import auth_router
from app.api.v1.classes import (
    leaderboard_router,
    teacher_access_codes_router,
    teacher_classes_router,
)
from app.api.v1.clue_plays import clue_plays_router
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
for router in (
    admin_router,
    auth_router,
    me_classes_router,
    leaderboard_router,
    teacher_classes_router,
    teacher_access_codes_router,
    clue_plays_router,
    content_router,
    email_links_router,
    games_router,
    me_router,
    media_router,
    verification_router,
):
    # a route that reads a body can refuse it as too large (app.api.body_limit). Before
    # including: FastAPI before 0.142 copies the routes when it includes a router
    document_body_limit(router.routes)
    api_router.include_router(router)
