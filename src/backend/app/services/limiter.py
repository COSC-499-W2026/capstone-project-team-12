"""Rate-limit counters, kept in memory: the spec runs one API process.

Each limit is a fixed one-minute window per (name, key). Routes reach them through the
dependencies in app.api.rate_limit.
"""

import math
import time

from fastapi import Request
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter

from app.errors import AppError, ErrorCode

_storage = MemoryStorage()
_counter = FixedWindowRateLimiter(_storage)


def count_request(name: str, per_minute: int, key: str) -> None:
    """Count one request against the limit; 429 RATE_LIMITED with Retry-After once it's over."""
    limit = parse(f"{per_minute}/minute")
    if _counter.hit(limit, name, key):
        return
    reset_at, _ = _counter.get_window_stats(limit, name, key)
    retry_after = max(1, math.ceil(reset_at - time.time()))
    raise AppError(
        ErrorCode.RATE_LIMITED,
        "Too many requests. Try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def reset_counts() -> None:
    _storage.reset()
