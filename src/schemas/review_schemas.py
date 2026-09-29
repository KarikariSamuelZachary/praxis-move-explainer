from typing import Literal, Optional

from pydantic import BaseModel


MoveClassification = Literal[
    "book",
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
]
TargetColor = Literal["white", "black", "both"]


class ReviewRequest(BaseModel):
    pgn: str
    target_color: TargetColor = "both"


class ReviewExplanation(BaseModel):
    explanation: str
    concept: Optional[str] = None
    tip: Optional[str] = None


class ReviewMoveResponse(BaseModel):
    fen: str
    san: str
    color: Literal["white", "black"]
    classification: MoveClassification
    cp_loss: int
    # Expected-points loss (Chess.com Classification V2 model). 0.0 for book
    # moves; kept alongside cp_loss so clients can show/debug both views.
    ep_loss: float = 0
    eval_cp: float = 0
    eval_mate: Optional[int] = None
    best_move_san: Optional[str] = None
    best_move_uci: Optional[str] = None
    explanation: Optional[ReviewExplanation] = None
