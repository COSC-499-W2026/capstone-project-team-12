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


class PointsSourceType(StrEnum):
    """Sources that can award points to a student."""

    LOCATION = "location"
    DAILY_CHALLENGE = "daily_challenge"
    STREAK_MILESTONE = "streak_milestone"


class Difficulty(IntEnum):
    BASIC = 1  # numbers only
    ADVANCING = 2  # numbers and a simple description
    CHALLENGING = 3  # description only


class FeedbackCode(StrEnum):
    NAV_START = "NAV_START"
    NAV_GETTING_CLOSER = "NAV_GETTING_CLOSER"
    NAV_GETTING_FARTHER = "NAV_GETTING_FARTHER"
    NAV_KEEP_GOING = "NAV_KEEP_GOING"
    NAV_VERY_CLOSE = "NAV_VERY_CLOSE"
    NAV_ARRIVED = "NAV_ARRIVED"
    NAV_GPS_WEAK = "NAV_GPS_WEAK"
    VERIFY_START = "VERIFY_START"
    VERIFY_SUCCESS = "VERIFY_SUCCESS"
    VERIFY_UNCLEAR = "VERIFY_UNCLEAR"
    VERIFY_NO_MATCH = "VERIFY_NO_MATCH"
    VERIFY_EXHAUSTED = "VERIFY_EXHAUSTED"
    SESSION_COMPLETE = "SESSION_COMPLETE"


class VerificationType(StrEnum):
    """How Stage 2 checks that the student found a location; other types are Future Work."""

    ROOM_NUMBER = "room_number"  # OCR reads the room-number sign


class AudioSourceType(StrEnum):
    UPLOADED = "uploaded"  # recorded and uploaded by a content manager
    AI_GENERATED = "ai_generated"  # TTS when the location is drawn (Future Work, spec AudioService)


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
    # no game action for SESSION_IDLE_EXPIRY_HOURS
    EXPIRED = "expired"
    CLASS_CLOSED = "class_closed"  # the class was archived, or the student left or was removed


class LocationStatus(StrEnum):
    PENDING = "pending"
    NAVIGATING = "navigating"  # Stage 1
    VERIFYING = "verifying"  # Stage 2
    COMPLETED = "completed"
    FAILED = "failed"  # out of photo attempts
    SKIPPED = "skipped"  # see SkipReason


class SkipReason(StrEnum):
    SESSION_ENDED = "session_ended"  # quit, reset, expiry or class closed before it was played
    DEACTIVATED = "deactivated"  # a content manager deactivated the location


class ArrivalOverride(StrEnum):
    GPS_UNAVAILABLE = "gps_unavailable"


class EmailKind(StrEnum):
    """Mail the outbox can send. Link tokens use the same names, except notice and invitation."""

    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"
    SET_PASSWORD = "set_password"
    CHANGE_EMAIL = "change_email"
    EMAIL_CHANGED_NOTICE = "email_changed_notice"
    TA_INVITATION = "ta_invitation"


class EmailStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
