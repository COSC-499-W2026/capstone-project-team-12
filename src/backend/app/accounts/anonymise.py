"""Deleting an account at an admin's request, which anonymises it.

The row stays, so the games, points and enrolments it took part in still count in class
statistics, but nothing in it identifies or reaches the person any more. The GPS positions are
cleared by game and the photos deleted by verification. Admin accounts are refused, as for
deactivating, since they are managed with the command-line tool. A deletion is recorded in the
admin audit log as delete_user; a refused one records nothing. Nothing here commits.
"""

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.accounts.staff import USER_NOT_FOUND_MESSAGE
from app.accounts.tokens import revoke_refresh_tokens
from app.engagement.interface import list_staff_classes
from app.errors import AppError, ErrorCode
from app.game.interface import clear_positions
from app.models.accounts import User
from app.models.email import EmailOutbox, EmailToken
from app.models.enums import AuditAction, ClassStaffRole, UserRole
from app.services import audit
from app.services.locks import lock_user
from app.verification.interface import delete_user_photos


def anonymise_user(admin_id: UUID, user_id: UUID, typed_username: str, db: Session) -> None:
    """Anonymise the account `user_id` once `typed_username` matches its username exactly; the
    errors are listed on the route. The user is locked first, as deactivating does, so a login
    or refresh in progress commits first and its token is revoked too. The success entry names
    only the role, since the address and username are what is being removed.
    """
    user = _deletable(lock_user(user_id, db), typed_username, db)
    # .invalid is a reserved domain, so nothing can ever be sent to the replaced address
    user.email = f"deleted-{user.id.hex}@deleted.invalid"
    user.username = f"deleted-{user.id.hex[:22]}"  # 30 characters, the longest a username can be
    user.email_verified_at = None
    user.password_hash = None
    user.can_manage_content = False
    user.current_class_id = None
    user.preferred_language = None
    user.last_login_at = None
    user.is_active = False
    db.flush()
    revoke_refresh_tokens(user.id, db)
    db.execute(delete(EmailToken).where(EmailToken.user_id == user.id))
    db.execute(delete(EmailOutbox).where(EmailOutbox.user_id == user.id))
    clear_positions(user.id, db)
    delete_user_photos(user.id, db)
    audit.record(
        admin_id, AuditAction.DELETE_USER, db, target_user_id=user.id, details={"role": user.role}
    )


def _deletable(user: User | None, typed_username: str, db: Session) -> User:
    if user is None:
        raise AppError(ErrorCode.USER_NOT_FOUND, USER_NOT_FOUND_MESSAGE)
    if user.role == UserRole.ADMIN:
        raise AppError(ErrorCode.FORBIDDEN_ROLE, "Admin accounts can't be deleted here.")
    if typed_username != user.username:
        raise AppError(ErrorCode.USERNAME_MISMATCH, "Type the account's username to confirm.")
    owned = [
        school_class.id
        for school_class, role in list_staff_classes(user.id, db)
        if role == ClassStaffRole.OWNER and school_class.archived_at is None
    ]
    if owned:
        raise AppError(
            ErrorCode.OWNS_ACTIVE_CLASS,
            "Transfer or archive each class this account owns first.",
            details=[{"class_id": str(class_id)} for class_id in owned],
        )
    return user
