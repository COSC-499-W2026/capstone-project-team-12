"""POST /games/{id}/clue-plays (#436). Its own module, so games.py stays under 500 lines."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt
from sqlalchemy.orm import Session

from app.accounts.interface import get_current_user
from app.api.v1.game_actions import play
from app.db import get_db
from app.errors import error_responses
from app.game.clue_plays import record_clue_play
from app.models.accounts import User

clue_plays_router = APIRouter(prefix="/games", tags=["games"])


class CluePlayRequest(BaseModel):
    position: PositiveInt = Field(description="The position of the location whose clue played")
    shuffles_used: NonNegativeInt = Field(
        description="shuffles_used from the current location, so a play from before a shuffle fails"
    )


class CluePlayResponse(BaseModel):
    position: int
    clue_plays: int = Field(description="Plays of this location's clue so far, this one included")


@clue_plays_router.post(
    "/{game_id}/clue-plays",
    operation_id="recordCluePlay",
    response_model=CluePlayResponse,
    responses=error_responses(
        HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.CONFLICT
    ),
)
def clue_play(
    game_id: UUID,
    request: CluePlayRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CluePlayResponse:
    """Count one play of the current location's clue: the first play and every replay.

    Feedback phrases don't count. 409 SESSION_NOT_ACTIVE if the game is paused, ended or
    expired; STATE_CHANGED if `position` or `shuffles_used` is out of date, with the current
    values in the details. 404 for another student's game. 403 FORBIDDEN_ROLE or ACCOUNT_DISABLED.
    """
    plays = play(
        lambda: record_clue_play(user.id, game_id, request.position, request.shuffles_used, db),
        user,
        db,
    )
    return CluePlayResponse(position=request.position, clue_plays=plays)
