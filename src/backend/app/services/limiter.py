"""The shared slowapi limiter. Decorate a route with @limiter.limit() to give it its own limit;
the limit for every /api/v1 request is app.api.rate_limit.global_rate_limit.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
