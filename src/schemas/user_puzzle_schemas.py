from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class UserPuzzleExtractRequest(BaseModel):
    lichess_username: Optional[str] = None
    chesscom_username: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=50)


class UserPuzzleExtractStartResponse(BaseModel):
    job_id: UUID
    status: str


class UserPuzzleJobResponse(BaseModel):
    job_id: UUID
    status: str
    lichess_username: Optional[str] = None
    chesscom_username: Optional[str] = None
    requested_limit: int = 20
    fetched_games: int = 0
    screened_games: int = 0
    candidates: int = 0
    puzzles_kept: int = 0
    error_message: Optional[str] = None
    summary: dict = Field(default_factory=dict)


class UserPuzzleQueuePuzzle(BaseModel):
    id: UUID
    fen_before: str
    best_move_uci: str
    best_move_san: str
    played_move_san: str
    game_url: str
    move_number: int
    color: str
    side_to_move: str


class UserPuzzleQueueEntry(BaseModel):
    id: UUID
    puzzle_id: UUID
    due: Optional[str] = None
    state: int = 1
    reps: int = 0
    lapses: int = 0
    puzzle: UserPuzzleQueuePuzzle


class UserPuzzleCountResponse(BaseModel):
    due_count: int


class UserPuzzleAttemptRequest(BaseModel):
    entry_id: UUID
    move_uci: str
    time_taken_ms: int = 0
    hints_used: int = 0


class UserPuzzleAttemptResponse(BaseModel):
    entry_id: UUID
    solved: bool
    best_move_uci: str
    best_move_san: str
    scheduling: Optional[dict] = None


class UserPuzzleFeedbackRequest(BaseModel):
    entry_id: UUID


class UserPuzzleFeedbackResponse(BaseModel):
    entry_id: UUID
    excluded: bool = True
