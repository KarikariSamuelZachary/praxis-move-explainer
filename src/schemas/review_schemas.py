from typing import Literal, Optional

from pydantic import BaseModel


MoveClassification = Literal[
    "book",
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "mistake",
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
    explanation: Optional[ReviewExplanation] = None
