from typing import List, Literal, Optional

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


class SandboxPrewarmRequest(BaseModel):
    moves: List[str] = []
    expected_mode: Optional[str] = None


class SandboxPrewarmResponse(BaseModel):
    fen: str
    mode: str
    cached: bool = False


class SandboxMoveRequest(BaseModel):
    move: str
    # Path from the game start (UCI or SAN). Preferred: replaying the path
    # restores book contiguity, repetition history and the previous ply's EP
    # loss, so the sandbox label matches batch review exactly.
    moves: List[str] = []
    # Fallback when no path is available (no history-dependent context).
    fen: Optional[str] = None
    player_rating: Optional[int] = None
    # Echo of the batch review's mode string; a mismatch is a stale review.
    expected_mode: Optional[str] = None


class SandboxLine(BaseModel):
    move_uci: Optional[str] = None
    move_san: Optional[str] = None
    eval_cp: Optional[float] = None
    eval_mate: Optional[int] = None
    pv_uci: List[str] = []
    pv_san: List[str] = []


class SandboxMoveResponse(BaseModel):
    classification: MoveClassification
    cp_loss: int
    ep_loss: float
    raw_ep_loss: float
    eval_cp: float
    eval_mate: Optional[int] = None
    color: Literal["white", "black"]
    fen_before: str
    fen: str
    move_san: str
    move_uci: str
    is_book: bool = False
    best: SandboxLine
    second_best: Optional[SandboxLine] = None
    mode: str
    cached: bool = False


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
    # Sandbox extras (only when the analyzer is asked for them).
    fen_before: Optional[str] = None
    player_rating: Optional[int] = None
    raw_ep_loss: Optional[float] = None
    second_best_cp: Optional[float] = None
    second_best_move_uci: Optional[str] = None
    second_best_move_san: Optional[str] = None
    second_best_pv_uci: Optional[List[str]] = None
    # Mode fingerprint this row was produced under (stale-gate echo).
    mode: Optional[str] = None
    explanation: Optional[ReviewExplanation] = None
