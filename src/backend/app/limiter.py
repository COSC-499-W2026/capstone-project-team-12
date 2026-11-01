"""API rate limiter module.
All routes are limited to 100 requests per minute per IP. Import the limiter and explicitly
decorate a route to override or add on to this default.
"""

from fastapi import FastAPI
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

# Limits requests to all endpoints to 100 requests per minute per IP.
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"], headers_enabled=True)


def add_limiter(app: FastAPI) -> None:
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
