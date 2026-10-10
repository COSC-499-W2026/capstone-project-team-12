"""FastAPI application factory."""

from fastapi import FastAPI, Response

from app.accounts.interface import RegisterRequest, student_register
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
    def register(request: RegisterRequest, response: Response) -> dict[str, str]:
        """Student sign-up: email, username, password, optional access code."""
        student_register(request, next(get_db()))
        return {"status": "ok"}

    return app


app = create_app()
