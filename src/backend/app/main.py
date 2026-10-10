"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.v1 import api_router
from app.errors import add_error_handlers
from app.accounts.interface import student_register, RegisterRequest

def create_app() -> FastAPI:
    app = FastAPI(title="Japanese Scavenger Hunt Game API", version="0.1.0")
    app.include_router(api_router)
    add_error_handlers(app)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness check used by Docker and CI. Does not touch the database."""
        return {"status": "ok"}

    @app.get("/auth/register", tags=["register"])
    def register(request: RegisterRequest) -> dict[str, str]:
        """Student sign-up: email, username, password, optional access code. Sends a verification email; 
        the student can play right away"""
        student_register(request)
        return {"status": "ok"}

    return app


app = create_app()
