from typing import Optional

from pydantic import BaseModel, Field


class MaiaMoveRequest(BaseModel):
    fen: str = Field(..., description="FEN to evaluate (six-field form).")
    elo: int = Field(1500, ge=400, le=3000, description="Maia-3 Elo rating.")
    temperature: float = Field(
        0.0,
        ge=0.0,
        le=2.0,
        description="Move sampling temperature. 0 = argmax human move.",
    )


class MaiaMoveResponse(BaseModel):
    best_move_uci: str
    best_move_san: str
    elo: int
    temperature: float
    model: str
    inference_ms: float
    wdl: Optional[dict] = None
