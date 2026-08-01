"""
Debug-only Maia-3 endpoint.

POST /api/debug/maia-move  { fen, elo, temperature? } -> Maia-3 suggested move.
GET  /api/debug/maia-health              -> { available, model, started_at? }

Not user-facing: protected by the existing X-Internal-Secret middleware and
intended for verifying the engine + checkpoint wiring at deploy time and
during development. No Clerk user id is required or rate-limited here.
"""
import logging
import time
from typing import Any, Dict

import chess
from fastapi import APIRouter, HTTPException

from engines.maia_engine import (
    MAIA3_DEFAULT_MODEL,
    MaiaUnavailableError,
    get_maia3,
    is_maia_available,
)
from schemas.maia_schemas import MaiaMoveRequest, MaiaMoveResponse

router = APIRouter()
log = logging.getLogger(__name__)


# Timestamp recorded the first time we observe Maia as up. Module-level so
# the health endpoint can report "since when" across this process lifetime
# without needing a DB write. Reset to None on close_maia3 / shutdown via the
# engines module is intentionally NOT done — this is a tiny diagnostic hint,
# not a source of truth.
_maia_started_at: float | None = None


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
    except MaiaUnavailableError as exc:
        # Distinguish "your FEN/request was bad" (400, above) from
        # "Maia is down" (503). 503 keeps the contract clear for ops.
        raise HTTPException(
            status_code=503,
            detail=f"Maia-3 is unavailable: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Maia-3 inference failed for FEN=%s", request.fen)
        raise HTTPException(
            status_code=502,
            detail=f"Maia-3 inference failed: {exc}",
        ) from exc

    return MaiaMoveResponse(**result)


@router.get("/debug/maia-health")
def debug_maia_health() -> Dict[str, Any]:
    """Lightweight Maia-3 health probe.

    Returns immediately without making any inference. `available` mirrors
    the boot-time `is_maia_available()` flag exactly; `model` is the
    configured preset name; `started_at` gives the Unix timestamp of the
    first time this process observed Maia as up (None if it has never been
    up this boot). No auth beyond the existing X-Internal-Secret middleware.
    """
    global _maia_started_at

    available = is_maia_available()
    if available and _maia_started_at is None:
        _maia_started_at = time.time()

    return {
        "available": available,
        "model": MAIA3_DEFAULT_MODEL,
        "started_at": _maia_started_at,
    }
