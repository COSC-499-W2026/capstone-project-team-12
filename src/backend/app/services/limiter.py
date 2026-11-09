"""API rate limiter module.
Every request counts towards one per-IP limit shared by all routes (global_rate_limit per
minute). Import the limiter and decorate a route with @limiter.limit() to add a stricter limit.
"""

import math
import time

from fastapi import Request
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.errors import AppError, ErrorCode

limiter = Limiter(key_func=get_remote_address, headers_enabled=True)

# TODO: #322 says that global rate limit must be keyed per user, not IP.


# An app dependency, not slowapi's middleware: the middleware cannot find the endpoints of
# included routers in FastAPI 0.142, so it never limited any /api/v1 route
def global_rate_limit(request: Request) -> None:
    ip = get_remote_address(request)
    global_limit = parse(f"{get_settings().global_rate_limit}/minute")  # read on each request
    if not limiter.limiter.hit(global_limit, "global", ip):
        reset_at, _ = limiter.limiter.get_window_stats(global_limit, "global", ip)
        retry_after = max(1, math.ceil(reset_at - time.time()))
        raise AppError(
            ErrorCode.RATE_LIMITED,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
