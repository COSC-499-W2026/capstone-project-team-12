from uuid import UUID

from app.models.enums import UserRole


class User:
    def __init__(self, user_id: UUID, email: str, username: str):
        self.id = user_id
        self.email = email
        self.username = username
        self.role = UserRole