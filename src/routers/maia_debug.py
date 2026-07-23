"""
Debug-only Maia-3 endpoint.

POST /api/debug/maia-move  { fen, elo, temperature? } -> Maia-3 suggested move.

Not user-facing: protected by the existing X-Internal-Secret middleware and
intended for verifying the engine + checkpoint wiring at deploy time and
during development. No Clerk user id is required or rate-limited here.
"""
import logging

import chess
from fastapi import APIRouter, HTTPException

from engines.maia_engine import get_maia3
from schemas.maia_schemas import MaiaMoveRequest, MaiaMoveResponse

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/debug/maia-move", response_model=MaiaMoveResponse)
def debug_maia_move(request: MaiaMoveRequest) -> MaiaMoveResponse:
    try:
        board = chess.Board(request.fen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid FEN: {exc}") from exc

    engine = get_maia3()
    try:
        result = engine.best_move(
            board,
            elo=request.elo,
            temperature=request.temperature,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Maia-3 inference failed for FEN=%s", request.fen)
        raise HTTPException(
            status_code=502,
            detail=f"Maia-3 inference failed: {exc}",
        ) from exc

    return MaiaMoveResponse(**result)
