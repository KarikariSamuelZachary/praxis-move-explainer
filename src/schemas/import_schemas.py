from typing import Any

from pydantic import BaseModel


class ChessComGameSummary(BaseModel):
    url: str
    pgn: str
    white: dict[str, Any]
    black: dict[str, Any]
    result: str
    end_time: int
    time_class: str


class LichessGameSummary(BaseModel):
    url: str
    pgn: str
    white: dict[str, Any]
    black: dict[str, Any]
    result: str
    end_time: int
    time_class: str