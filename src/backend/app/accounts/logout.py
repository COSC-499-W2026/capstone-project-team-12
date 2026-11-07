"""Module for logging a user out of the system. Logging out revokes all refresh tokens of the
current user, which signs them out on every device.

Access tokens are stateless and cannot be revoked: one that is already issued keeps working until
it expires (up to 60 minutes), so clients must discard it when logging out."""

from sqlalchemy.orm import Session

from app.accounts.tokens import revoke_refresh_tokens
from app.models.accounts import User


def logout_user(user: User, db: Session) -> None:
    """Revokes every refresh token of the user, so none of their sessions can be refreshed."""
    revoke_refresh_tokens(user.id, db)
