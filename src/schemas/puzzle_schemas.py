from pydantic import BaseModel
from typing import Optional

class PuzzleResponse(BaseModel):
    id: str
    fen: str
    moves: list[str]
    rating: int
    themes: list[str]
    gameUrl: Optional[str] = None