"""Fixed vocabularies shared by the models and the components.

They live here, in shared code, so a table column or CHECK can use the same values as the API.
"""

from enum import StrEnum


class Language(StrEnum):
    JA = "ja"
    ZH = "zh"
    FR = "fr"
    DE = "de"
    KO = "ko"
    ES = "es"
    