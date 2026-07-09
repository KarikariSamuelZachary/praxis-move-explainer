import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from integrations.chess_com import (
    ChessComError,
    ChessComUserNotFound,
    fetch_recent_chesscom_games,
)
from schemas.import_schemas import ChessComGameSummary
from schemas.import_schemas import LichessGameSummary
from integrations.lichess import (
    LichessError,
    LichessRateLimited,
    LichessUserNotFound,
    fetch_recent_lichess_games,
)
from core.rate_limit import limit_by_clerk_user_id

router = APIRouter()
log = logging.getLogger(__name__)


@router.get(
    "/import/chesscom/{username}",
    response_model=List[ChessComGameSummary],
)
def import_chesscom_games(
    request: Request,
    username: str = Path(
        ...,
        min_length=1,
        max_length=100,
        description="Chess.com username to import recent games for.",
    ),
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="Maximum number of recent games to return.",
    ),
    _: None = Depends(limit_by_clerk_user_id(limit=5, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(
            status_code=400,
            detail="Missing X-Clerk-User-Id header",
        )

    try:
        games = fetch_recent_chesscom_games(username=username, limit=limit)
    except ChessComUserNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChessComError as exc:
        log.exception("Chess.com import failed for username '%s'", username)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Unexpected error importing Chess.com games for '%s'", username)
        raise HTTPException(
            status_code=500,
            detail="Failed to import Chess.com games",
        ) from exc

    return games


@router.get(
    "/import/lichess/{username}",
    response_model=List[LichessGameSummary],
)
def import_lichess_games(
    request: Request,
    username: str = Path(
        ...,
        min_length=1,
        max_length=100,
        description="Lichess username to import recent games for.",
    ),
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="Maximum number of recent games to return.",
    ),
    _: None = Depends(limit_by_clerk_user_id(limit=5, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(
            status_code=400,
            detail="Missing X-Clerk-User-Id header",
        )

    try:
        games = fetch_recent_lichess_games(username=username, limit=limit)
    except LichessUserNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LichessRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LichessError as exc:
        log.exception("Lichess import failed for username '%s'", username)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Unexpected error importing Lichess games for '%s'", username)
        raise HTTPException(
            status_code=500,
            detail="Failed to import Lichess games",
        ) from exc

    return games