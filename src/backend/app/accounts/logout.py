"""Module for logging a user out of the system. Logging out revokes all refresh tokens from the
current user."""

from sqlalchemy.orm import Session

from app.accounts.tokens import revoke_refresh_tokens
from app.models.accounts import User

def logout_user(user: User, db: Session):
    revoke_refresh_tokens(user.id, db)