"""Route dependencies for the rate limits (spec, Rate Limits).

Every /api/v1 request counts towards the global limit, routes with their own limits included.
"""

import hashlib
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from app.accounts.interface import user_id_from_token
from app.config import get_settings
from app.services.limiter import count_request

AUTH_PATH = "/api/v1/auth/"


# A router dependency, not middleware: slowapi's middleware couldn't find the endpoints of
# included routers in FastAPI 0.142, so it never limited any /api/v1 route
def global_rate_limit(request: Request) -> None:
    """Counts per user with a valid access token, otherwise per IP, so a client can't pick or
    make up another user's bucket. The auth routes always count per IP."""
    count_request("global", get_settings().global_rate_limit, _caller_key(request))


def register_rate_limit(request: Request) -> None:
    count_request("register", get_settings().register_attempts_per_ip, _client_ip(request))


def login_rate_limit(
    request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> None:
    """Per IP, and per (email, IP) so one address can't be guessed at from one place. The email
    is hashed so the counter keys don't store it."""
    settings = get_settings()
    ip = _client_ip(request)
    email = hashlib.sha256(form.username.strip().casefold().encode()).hexdigest()
    count_request("login", settings.login_attempts_per_ip, ip)
    count_request("login-email", settings.login_attempts_per_email, f"{ip}:{email}")


def _caller_key(request: Request) -> str:
    ip_key = f"ip:{_client_ip(request)}"
    if request.url.path.startswith(AUTH_PATH):
        return ip_key
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        user_id = user_id_from_token(token)
        if user_id is not None:
            return f"user:{user_id}"
    return ip_key


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
