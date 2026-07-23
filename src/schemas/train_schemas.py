from typing import Literal, Optional

from pydantic import BaseModel, Field


class OpponentImportRequest(BaseModel):
    lichess_username: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Public Lichess username to import as an opponent profile.",
    )
    chesscom_username: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Public Chess.com username to import as an opponent profile.",
    )
    limit: int = Field(
        100,
        ge=1,
        le=500,
        description="Maximum games per provider to fetch in the background job.",
    )


class OpponentImportStartResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
    lichess_username: Optional[str] = None
    chesscom_username: Optional[str] = None
    limit: int


class OpponentImportJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    lichess_username: Optional[str] = None
    chesscom_username: Optional[str] = None
    requested_limit: int
    imported_count: int
    error_message: Optional[str] = None


class WeaknessProfileRequest(BaseModel):
    source_type: Literal["opponent", "user"] = "opponent"
    provider: Optional[Literal["lichess", "chesscom"]] = None
    opponent_username: Optional[str] = Field(None, min_length=1, max_length=100)
    limit: int = Field(
        50,
        ge=1,
        le=200,
        description="Maximum games to analyze from the selected corpus.",
    )


class WeaknessProfileStartResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
    source_type: Literal["opponent", "user"]
    provider: Optional[str] = None
    opponent_username: Optional[str] = None
    limit: int


class WeaknessProfileJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    source_type: Literal["opponent", "user"]
    provider: Optional[str] = None
    opponent_username: Optional[str] = None
    requested_limit: int
    analyzed_games_count: int
    analyzed_moves_count: int
    mistake_count: int
    blunder_count: int
    summary: dict
    error_message: Optional[str] = None


class OpponentProfileResponse(BaseModel):
    provider: Literal["lichess", "chesscom"]
    opponent_username: str
    game_count: int
    rating: int


class OpponentProfileListResponse(BaseModel):
    opponents: list[OpponentProfileResponse]


class SparringMoveRequest(BaseModel):
    provider: Literal["lichess", "chesscom"]
    opponent_username: str = Field(..., min_length=1, max_length=100)
    fen: str = Field(..., min_length=1, max_length=200)
    bot_color: Literal["white", "black"]
    catastrophic_loss_cp: int = Field(300, ge=100, le=2000)
    maia_temperature: float = Field(0.15, ge=0, le=2)


class SparringMoveResponse(BaseModel):
    move_uci: str
    move_san: str
    source: Literal["in_book", "playing_naturally", "correcting_blunder"]
    opponent_elo: int
    repertoire_frequency: Optional[int] = None
    cp_loss: int = 0
    best_move_uci: Optional[str] = None
    best_move_san: Optional[str] = None
