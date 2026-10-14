"""Auth routes reached from an email or before login: verify the address, resend the link,
forget the password (A3). None of them need an access token."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.accounts.email_links import (
    request_password_reset,
    request_verification_email,
    verify_email,
)
from app.accounts.register import email_is_valid
from app.db import get_db

email_links_router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class EmailRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)

    @field_validator("email")
    @classmethod
    def _check_format(cls, value: str) -> str:
        # same check as sign-up; a 422 for a malformed address says nothing about accounts
        value = value.strip().lower()
        if not email_is_valid(value):
            raise ValueError("not a valid email address")
        return value


class StatusResponse(BaseModel):
    status: str = "ok"


@email_links_router.post("/verify-email", operation_id="verifyEmail")
def post_verify_email(
    request: TokenRequest, db: Annotated[Session, Depends(get_db)]
) -> StatusResponse:
    """Confirm the address with the token from the verification email (400 INVALID_TOKEN)."""
    verify_email(request.token, db)
    db.commit()
    return StatusResponse()


@email_links_router.post(
    "/resend-verification",
    operation_id="resendVerificationEmail",
    status_code=HTTPStatus.ACCEPTED,  # queued, sent by send-emails
)
def post_resend_verification(
    request: EmailRequest, db: Annotated[Session, Depends(get_db)]
) -> StatusResponse:
    """Send the link again. Same answer whether or not the address has an account."""
    request_verification_email(request.email, db)
    db.commit()
    return StatusResponse()


@email_links_router.post(
    "/forgot-password",
    operation_id="requestPasswordReset",
    status_code=HTTPStatus.ACCEPTED,
)
def post_forgot_password(
    request: EmailRequest, db: Annotated[Session, Depends(get_db)]
) -> StatusResponse:
    """Queue a reset email. Same answer whether or not the address has an account."""
    request_password_reset(request.email, db)
    db.commit()
    return StatusResponse()
