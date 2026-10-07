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
