"""Public interface of app.accounts: the only module other components may import."""
from app.accounts.tokens import revoke_refresh_tokens

def revoke_all_refresh_tokens(user_id: UUID, db: Session) -> None:
    revoke_refresh_tokens(user_id, db)