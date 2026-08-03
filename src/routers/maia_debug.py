"""
Debug-only Maia-3 endpoints.

POST /api/debug/maia-move       { fen, elo, temperature? } -> single best move.
POST /api/debug/maia-candidates { fen, multipv?, self_elo?, oppo_elo? }
                                -> ranked candidate list via analyse(multipv=N).
GET  /api/debug/maia-health      -> { available, model, started_at? }

Not user-facing: protected by the existing X-Internal-Secret middleware and
intended for verifying the engine + checkpoint wiring at deploy time and
during development. No Clerk user id is required or rate-limited here.
"""
import logging
import time
from typing import Any, Dict, List, Optional

import chess
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engines.maia_engine import (
    MAIA3_DEFAULT_MODEL,
    MaiaUnavailableError,
    get_maia3,
    is_maia_available,
)
from schemas.maia_schemas import MaiaMoveRequest, MaiaMoveResponse


class MaiaCandidatesRequest(BaseModel):
    fen: str = Field(..., description="FEN to evaluate (six-field form).")
    multipv: int = Field(5, ge=1, le=20, description="Number of candidate moves.")
    self_elo: Optional[int] = Field(
        None, ge=0, le=5000, description="Side-to-move Elo (default 1500)."
    )
    oppo_elo: Optional[int] = Field(
        None, ge=0, le=5000, description="Other-side Elo (default 1500)."
    )


class MaiaCandidate(BaseModel):
    move: str
    score: Optional[int] = None
    wdl: Optional[Dict[str, float]] = None


class MaiaCandidatesResponse(BaseModel):
    candidates: List[MaiaCandidate]
    multipv: int
    model: str
    inference_ms: float

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

    Returns immediately without making any inference. `available` reflects
    the live state of the shared Maia-3 singleton: is_maia_available() probes
    the underlying subprocess's returncode on every call (not a cached
    boot-time flag), so it returns False both when Maia never started AND
    when it crashed mid-session and was marked dead by best_move(). `model`
    is the configured preset name; `started_at` gives the Unix timestamp of
    the first time this process observed Maia as up (None if it has never
    been up this boot). No auth beyond the existing X-Internal-Secret
    middleware.
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


@router.post("/debug/maia-candidates", response_model=MaiaCandidatesResponse)
def debug_maia_candidates(request: MaiaCandidatesRequest) -> MaiaCandidatesResponse:
    """Ranked candidate moves via best_move_candidates(multipv=N).

    Same liveness contract as /debug/maia-move: MaiaUnavailableError -> 503,
    other engine errors -> 502, bad FEN -> 400, bad multipv/Elo -> 400.
    Timing is measured in-process (perf_counter) and reported as
    `inference_ms`; the engine method also logs its own inference_ms at DEBUG.
    Debug-only, X-Internal-Secret gated, not user-facing.
    """
    try:
        board = chess.Board(request.fen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid FEN: {exc}") from exc

    engine = get_maia3()
    started = time.perf_counter()
    try:
        candidates_raw = engine.best_move_candidates(
            board,
            multipv=request.multipv,
            self_elo=request.self_elo,
            oppo_elo=request.oppo_elo,
        )
    except MaiaUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"Maia-3 is unavailable: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad request: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Maia-3 candidates failed for FEN=%s", request.fen)
        raise HTTPException(status_code=502, detail=f"Maia-3 candidates failed: {exc}") from exc

    inference_ms = (time.perf_counter() - started) * 1000
    return MaiaCandidatesResponse(
        candidates=[MaiaCandidate(**c) for c in candidates_raw],
        multipv=request.multipv,
        model=MAIA3_DEFAULT_MODEL,
        inference_ms=round(inference_ms, 1),
    )
