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
