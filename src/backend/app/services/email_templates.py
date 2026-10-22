"""The subject and plain-text body of each email the outbox sends.

Links point at pages of the web app (`app_base_url`), which read the token and call the API.
The web app isn't designed yet, so the page paths live in LINK_PATHS and change in one place.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from app.models.enums import EmailKind
from app.services.email import TOKEN_TTL
from app.services.email_sender import OutgoingMail

# page of the web app that each link opens (spec, Frontend: public pages)
LINK_PATHS = {
    EmailKind.VERIFY_EMAIL: "/verify-email",
    EmailKind.RESET_PASSWORD: "/reset-password",
    EmailKind.SET_PASSWORD: "/set-password",
}

_SIGN_OFF = "\n\nIf you didn't expect this email, you can ignore it.\n\n- Japanese Scavenger Hunt\n"


def _lasts(kind: EmailKind) -> str:
    ttl = TOKEN_TTL[kind]  # so the email never promises a different expiry than the link has
    return f"{ttl.days} days" if ttl.days else f"{ttl.seconds // 60} minutes"


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    body: str


def render(mail: OutgoingMail, app_base_url: str) -> RenderedEmail:
    """The email for `mail`, which needs its link token.

    Only the kinds that something queues have a template; the feature that queues another kind
    (email change, TA invitations) adds its own.
    """
    if mail.kind not in LINK_PATHS:
        raise ValueError(f"No template for {mail.kind!r}")
    if mail.token is None:
        raise ValueError(f"{mail.kind} needs a token")
    query = urlencode({"token": mail.token})
    link = f"{app_base_url.rstrip('/')}{LINK_PATHS[mail.kind]}?{query}"
    lasts = _lasts(mail.kind)
    match mail.kind:
        case EmailKind.VERIFY_EMAIL:
            return RenderedEmail(
                "Confirm your email address",
                f"Welcome! Open this link to confirm your email address:\n\n{link}\n\n"
                f"The link works for {lasts}. You can play before confirming, but "
                "password reset links are only sent to a confirmed address." + _SIGN_OFF,
            )
        case EmailKind.RESET_PASSWORD:
            return RenderedEmail(
                "Reset your password",
                f"Open this link to choose a new password:\n\n{link}\n\n"
                f"The link works once, for {lasts}. After that, log in with your new "
                "password." + _SIGN_OFF,
            )
        case _:  # SET_PASSWORD, the only other kind with a page
            return RenderedEmail(
                "Set up your teacher account",
                "An administrator created a teacher account for you. Open this link to choose "
                f"your password:\n\n{link}\n\nThe link works once, for {lasts}." + _SIGN_OFF,
            )
