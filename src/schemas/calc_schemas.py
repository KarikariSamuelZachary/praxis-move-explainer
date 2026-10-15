"""Pydantic schemas for the Blind Calculation trainer (selection + grading)."""

from __future__ import annotations

from pydantic import BaseModel, Field
from uuid import UUID


class CalcNextResponse(BaseModel):
    segment_id: UUID
    offset: int
    n: int
    fen: str
    side_to_move: str
    rating: int | None = None
    exercise_token: str


class CalcSubmitRequest(BaseModel):
    exercise_token: str = Field(min_length=1)
    moves: list[str] = Field(min_length=1)
    time_taken_ms: int | None = Field(default=None, ge=0)


class CalcSubmitResponse(BaseModel):
    solved: bool
    first_divergence_ply: int | None = None
    diverged_on: str | None = None
    correct_prefix_plies: int
    continuation: list[str] = []
    rating_before: int | None = None
    rating_after: int | None = None
    delta: int = 0
    rated: bool = False
    attempt_id: UUID | None = None


class CalcRatingEntry(BaseModel):
    n: int
    rating: int
    attempts: int


class CalcRatingsResponse(BaseModel):
    ratings: list[CalcRatingEntry]


class CalcWoodpeckerQueueItem(BaseModel):
    entry_id: UUID
    fen: str
    side_to_move: str
    n: int
    review_token: str


class CalcWoodpeckerCountResponse(BaseModel):
    due_count: int


class CalcWoodpeckerAttemptRequest(BaseModel):
    review_token: str = Field(min_length=1)
    moves: list[str] = Field(min_length=1)


class CalcWhyRequest(BaseModel):
    attempt_id: UUID
    kind: str = Field(default="solve", pattern="^(solve|review)$")


class CalcWhyEval(BaseModel):
    cp: int | None = None
    mate: int | None = None
    win_prob: float | None = None


class CalcWhyResponse(BaseModel):
    type: str
    headline: str
    line_uci: list[str] = []
    eval_before: CalcWhyEval | None = None
    eval_after: CalcWhyEval | None = None
    truncated_at_ply: int | None = None
    params: dict = {}
