"""FastAPI application factory."""

from fastapi import Depends, FastAPI

from app.api.body_limit import RequestBodyLimit
from app.api.rate_limit import global_rate_limit
from app.api.v1 import api_router
from app.errors import add_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="Japanese Scavenger Hunt Game API", version="0.1.0")
    # every /api/v1 route; /health stays unlimited so health checks are never rejected
    app.include_router(api_router, dependencies=[Depends(global_rate_limit)])
    app.add_middleware(RequestBodyLimit)
    add_error_handlers(app)

    @app.get("/health", tags=["health"], operation_id="getHealth")
    def health() -> dict[str, str]:
        """Liveness check used by Docker and CI. Does not touch the database."""
        return {"status": "ok"}

    return app


app = create_app()
