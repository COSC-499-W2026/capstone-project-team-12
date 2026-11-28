"""Response bodies that more than one game route returns.

None of them ever holds a location's name, room number, coordinates or answers.
"""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.me import RecentBadgeResponse
from app.game.current_location import Stage
from app.game.summary import GameSummary
from app.models.enums import (
    Difficulty,
    FeedbackCode,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)


class LocationProgressResponse(BaseModel):
    position: int
    status: LocationStatus
    skip_reason: SkipReason | None
    attempts_used: int
    points_awarded: int


class GameStatusResponse(BaseModel):
    """A game's state and how each location went. Never a location's name, room or
    coordinates."""

    session_id: UUID
    state: SessionState
    end_reason: SessionEndReason | None
    class_id: UUID | None
    language: Language
    difficulty: Difficulty
    current_position: int
    shuffles_used: int
    total_score: int
    started_at: datetime
    paused_at: datetime | None
    locations: list[LocationProgressResponse]


class GameEngagementResponse(BaseModel):
    """What the game added to the student's engagement. A streak field comes with A13 (#87)."""

    level: int = Field(
        ge=1, le=5, description="The level in the game's language as of the game's last points."
    )
    level_up: bool = Field(description="Whether the game's points raised the level.")
    new_badges: list[RecentBadgeResponse] = Field(
        description="Badges the game's locations unlocked, global ones included, oldest first."
    )


class GameSummaryResponse(GameStatusResponse):
    """A game's status, when it ended and what it added to the student's engagement."""

    ended_at: datetime | None = Field(
        description=(
            "When the game was completed, quit, reset, expired or closed with its class. "
            "Null while it is open."
        )
    )
    engagement: GameEngagementResponse


def summary_response(summary: GameSummary) -> GameSummaryResponse:
    return GameSummaryResponse(
        **asdict(summary.status),
        ended_at=summary.ended_at,
        engagement=GameEngagementResponse.model_validate(summary.engagement, from_attributes=True),
    )


class CurrentLocationResponse(BaseModel):
    """The current location of a game. Never its name, room number, coordinates or answers."""

    session_id: UUID
    state: SessionState
    position: int
    stage: Stage
    # the clue of this location, for both stages
    clue_audio_url: str
    attempts_used: int
    attempts_left: int
    # Stage 1 only: when "Continue without GPS" is allowed. Compare it with the current time;
    # don't count down from it, since it can move (see the route description)
    gps_override_available_at: datetime | None


class FeedbackResponse(BaseModel):
    code: FeedbackCode
    audio_url: str
