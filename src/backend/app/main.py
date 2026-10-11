"""FastAPI application factory."""

from fastapi import FastAPI, Response

from app.accounts.interface import (
    AuthRequest,
    rotate_token,
    student_register,
    validate_login,
)
from app.api.v1 import api_router
from app.db import get_db
from app.errors import add_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Japanese Scavenger Hunt Game API", version="0.1.0")
    app.include_router(api_router)
    add_error_handlers(app)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness check used by Docker and CI. Does not touch the database."""
        return {"status": "ok"}

    @app.post("/auth/register", tags=["register"], status_code=201)
    def register(request: AuthRequest.RegisterRequest, response: Response) -> dict[str, str]:
        """Student sign-up: email, username, password, optional access code."""
        student_register(request, next(get_db()))
        return {"status": "ok"}

    @app.post("/auth/login", tags=["login"], status_code=200)
    def login(request: AuthRequest.LoginRequest, response: Response) -> dict[str, str | bytes]:
        """Sign in with email and password (all roles)"""
        tokens = validate_login(request, next(get_db()))
        return tokens

    @app.post("/auth/refresh", tags=["refresh"], status_code=200)
    def refresh(
        request: AuthRequest.RefreshRequest, response: Response
    ) -> dict[str, str | bytes] | None:
        """Rotates refresh token of user and returns a new access token and refresh token"""
        tokens = rotate_token(request, next(get_db()))
        return tokens

    return app


app = create_app()
