"""Fixed vocabularies shared by the models and the components.

They live here, in shared code, so a table column or CHECK can use the same values as the API.
"""

from enum import IntEnum, StrEnum


class Language(StrEnum):
    JA = "ja"
    ZH = "zh"
    FR = "fr"
    DE = "de"
    KO = "ko"
    ES = "es"


class Difficulty(IntEnum):
    BASIC = 1  # numbers only
    ADVANCING = 2  # numbers and a simple description
    CHALLENGING = 3  # description only


class UserRole(StrEnum):
    STUDENT = "student"
    STAFF = "staff"  # teachers; a TA is a class_staff row, not a role
    ADMIN = "admin"


class ClassStaffRole(StrEnum):
    OWNER = "owner"
    ASSISTANT = "assistant"


class EnrollmentEndReason(StrEnum):
    LEFT = "left"  # the student left; they can rejoin later with a code
    REMOVED = "removed"  # by the owner; can't rejoin with a code, but the owner can restore
    ARCHIVED = "archived"


class SessionState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"  # all 5 locations finished
    ABANDONED = "abandoned"  # ended early, see SessionEndReason


class SessionEndReason(StrEnum):
    QUIT = "quit"
    RESET = "reset"
    EXPIRED = "expired"  # no game action for 24 hours
    CLASS_CLOSED = "class_closed"  # the class was archived, or the student left or was removed
