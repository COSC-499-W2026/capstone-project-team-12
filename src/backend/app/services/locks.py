"""Row locks that more than one component needs, so none of them reaches into another's table."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounts import User


def lock_user(user_id: UUID, db: Session) -> User | None:
    """Lock the user's row until the transaction ends (SELECT ... FOR NO KEY UPDATE) and return it.

    Whoever changes what a student is allowed to do (starting a game, switching class or Practice
    language) takes this lock first, so two of them can't both pass their check. A User object
    already loaded is reloaded with the locked row's values, so what the caller then compares is
    what is really stored, not what was read before another request committed. Pending changes are
    flushed first (the app's sessions don't autoflush), so none is lost. Returns None if there is
    no such user.

    The lock is key_share=True, so rows that only reference the user, such as a queued email,
    aren't blocked while a game starts. Two callers still wait for each other.
    """
    db.flush()
    return db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
