"""Module for logging a user out of the system. Logging out ends one sign-in: it revokes the
refresh token family named by the refresh cookie, so that device can no longer get new access
tokens. The user's other sign-ins (other devices or browsers) stay signed in.

The session is found from the refresh cookie, not the access token, so logging out still works
after the access token has expired.

Access tokens are stateless and cannot be revoked: one that is already issued keeps working until
it expires (up to 60 minutes), so clients must discard it when logging out."""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.tokens import revoke_token_family
from app.models.accounts import RefreshToken, User


def logout_session(refresh_token: str | None, db: Session) -> None:
    """Revokes the family of the given refresh token, so that sign-in can no longer be refreshed.

    Any token of the family works, including a rotated, expired or already revoked one: the token
    only names the family. A missing or unknown token does nothing.
    This function does not commit: the caller owns the transaction.
    """
    if refresh_token is None:
        return
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    # no lock yet: only which user and family, so the user row can be locked before any token row
    row = db.execute(
        select(RefreshToken.user_id, RefreshToken.family_id).where(
            RefreshToken.token_hash == token_hash
        )
    ).one_or_none()
    if row is None:
        return
    user_id, family_id = row
    # FOR NO KEY UPDATE, as a password reset takes. A refresh holds FOR SHARE on the user while it
    # inserts the family's next token, so this waits for it to commit, and the revoke below then
    # sees that new token. Locking the user first keeps the same lock order as a refresh.
    db.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
    revoke_token_family(family_id, db)
