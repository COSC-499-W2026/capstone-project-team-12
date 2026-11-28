"""SQLAlchemy models (all of them, because the schema is shared) and the vocabularies they use.

Each component's tables live in app/models/<component>.py. Import every model module here:
Alembic autogenerate only sees imported models and would treat the others as dropped.
"""

from app.models import accounts, audit, content, email, engagement, game, verification

__all__ = ["accounts", "audit", "content", "email", "engagement", "game", "verification"]
