"""The subject and plain-text body of each email the outbox sends.

Links point at pages of the web app (`app_base_url`), which read the token and call the API.
The web app isn't designed yet, so the page paths live in LINK_PATHS and change in one place.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from app.models.enums import EmailKind
from app.services.email_sender import OutgoingMail

# page of the web app that each link opens (spec, Frontend: public pages)
LINK_PATHS = {
    EmailKind.VERIFY_EMAIL: "/verify-email",
    EmailKind.RESET_PASSWORD: "/reset-password",
    EmailKind.SET_PASSWORD: "/set-password",
    EmailKind.CHANGE_EMAIL: "/confirm-email",  # not in the spec's page list yet
}

_SIGN_OFF = "\n\nIf you didn't expect this email, you can ignore it.\n\n- Japanese Scavenger Hunt\n"


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    body: str


def render(mail: OutgoingMail, app_base_url: str) -> RenderedEmail:
    """The email for `mail`. A kind with a link needs `mail.token`; the others ignore it."""
    if mail.kind in LINK_PATHS:
        if mail.token is None:
            raise ValueError(f"{mail.kind} needs a token")
        query = urlencode({"token": mail.token})
        link = f"{app_base_url.rstrip('/')}{LINK_PATHS[mail.kind]}?{query}"
    match mail.kind:
        case EmailKind.VERIFY_EMAIL:
            return RenderedEmail(
                "Confirm your email address",
                f"Welcome! Open this link to confirm your email address:\n\n{link}\n\n"
                "The link works for 7 days. You can play before confirming. If this address is "
                "wrong, you won't be able to get password reset links." + _SIGN_OFF,
            )
        case EmailKind.RESET_PASSWORD:
            return RenderedEmail(
                "Reset your password",
                f"Open this link to choose a new password:\n\n{link}\n\n"
                "The link works once, for 60 minutes. Resetting your password signs you out "
                "on every device." + _SIGN_OFF,
            )
        case EmailKind.SET_PASSWORD:
            return RenderedEmail(
                "Set up your teacher account",
                "An administrator created a teacher account for you. Open this link to choose "
                f"your password:\n\n{link}\n\nThe link works once, for 7 days." + _SIGN_OFF,
            )
        case EmailKind.CHANGE_EMAIL:
            return RenderedEmail(
                "Confirm your new email address",
                f"Open this link to use this address for your account:\n\n{link}\n\n"
                "The link works for 7 days. Until then your account keeps its old address."
                + _SIGN_OFF,
            )
        case EmailKind.EMAIL_CHANGED_NOTICE:
            return RenderedEmail(
                "Your email address is being changed",
                "Someone asked to move your account to a new email address. If that was you, "
                "there is nothing to do. If it wasn't, change your password and contact your "
                "teacher.\n\n- Japanese Scavenger Hunt\n",
            )
        case EmailKind.TA_INVITATION:
            class_name = mail.params.get("class_name", "a class")
            return RenderedEmail(
                f"You're invited to be a TA for {class_name}",
                f"You've been invited to help teach {class_name}. Log in to the app to accept "
                f"or decline:\n\n{app_base_url.rstrip('/')}/\n\nThe invitation expires after "
                "7 days." + _SIGN_OFF,
            )
    raise ValueError(f"No template for {mail.kind!r}")  # pragma: no cover (match is exhaustive)
