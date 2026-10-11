"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.v1 import api_router
from app.errors import add_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Japanese Scavenger Hunt Game API", version="0.1.0")
    app.include_router(api_router)
    add_error_handlers(app)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Liveness check used by Docker and CI. Does not touch the database."""
        return {"status": "ok"}

    return app


app = create_app()
