"""The limit every /api/v1 request counts towards, routes with their own limits included."""

import math
import time

from fastapi import Request
from limits import parse
from slowapi.util import get_remote_address

from app.accounts.interface import user_id_from_token
from app.config import get_settings
from app.errors import AppError, ErrorCode
from app.services.limiter import limiter


def global_rate_limit(request: Request) -> None:
    """Counts per user with a valid access token, otherwise per IP, so a client can't pick or
    make up another user's bucket."""
    key = _caller_key(request)
    global_limit = parse(f"{get_settings().global_rate_limit}/minute")
    if not limiter.limiter.hit(global_limit, "global", key):
        reset_at, _ = limiter.limiter.get_window_stats(global_limit, "global", key)
        retry_after = max(1, math.ceil(reset_at - time.time()))
        raise AppError(
            ErrorCode.RATE_LIMITED,
            "Too many requests. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def _caller_key(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        user_id = user_id_from_token(token)
        if user_id is not None:
            return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"
