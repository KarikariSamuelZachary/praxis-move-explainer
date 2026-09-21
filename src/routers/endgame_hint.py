"""
"Get solution" hint endpoint: ONE tablebase-optimal move, read-only.

  POST /api/endgames/hint -> the best move for the side to move in `fen`

Scope (deliberate): the SINGLE theoretically correct move for the CURRENT
position, never a multi-move plan. The selection policy is exactly the
defender generator's (services/endgame_reply.py): draw-preserving if drawn,
longest resistance if lost, fastest conversion if winning -- with DTZ as the
metric. generate_opponent_reply() is side-agnostic (it answers for whoever
is to move), so applying it to the user's side is the same call, not a
reimplementation.

WHY THE HINT IS ADVISORY, NOT A MOVE
====================================
The user plays the hinted move through the surface's own grading route
(POST /api/endgames/move, /practice/move or /woodpecker/attempts), so the
drill keeps exactly one grading path and this route stays write-free: no
rating, no FSRS, no capture, no attempt row. Nothing here can change a
recorded result.

That is also why `hints_used` travels on the MOVE requests instead: the
hint itself is a fact the client owns (the backend keeps no drill session,
the same statelessness every endgame route documents), and the scoring
consequences live where the writes live -- a hint-assisted RATED solve is
neutral (routers/endgames.py) and a hint-assisted REVIEW pass is scheduled
as not-clean (routers/endgame_woodpecker.py). Practice writes nothing and
carries the count for display only.

COST (measured, stated so it is a known trade)
==============================================
Locally-covered material (<=5 men) answers in ~20-40ms. Beyond it the
Lichess fallback probes every legal child (~25-40 round trips), which
measured 3-13s per move in the playout work; the probe cache makes repeat
calls fast. That is the price of the tablebase's answer on 6-7-man content;
the client shows a busy state and reveals nothing until the move lands.

TURN GUARD
==========
`fen` must have the USER to move. The drill's stored FEN is the user-to-move
position (see schemas/endgame_schemas.EndgamePositionResponse), so its side
to move is the user's colour; asking for a hint on the defender's turn would
return the DEFENDER's best move, which is never what the button means.
"""
import logging

import chess
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor

from core.database import get_db
from core.rate_limit import limit_by_clerk_user_id
from schemas.endgame_schemas import EndgameHintRequest, EndgameHintResponse
from services.endgame_reply import (
    OpponentReplyUnavailableError,
    TerminalPositionError,
    generate_opponent_reply,
)

router = APIRouter()
log = logging.getLogger(__name__)


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


@router.post("/hint", response_model=EndgameHintResponse)
def get_hint(
    body: EndgameHintRequest,
    request: Request,
    # 30/min: a hint can cost a whole child-probe sweep beyond local
    # coverage, and a real drill needs at most one per position. Tighter
    # than the move routes on purpose.
    _: None = Depends(limit_by_clerk_user_id(limit=30, window=60)),
    conn=Depends(get_db),
):
    """The best move for the side to move in `fen`, with no write path.

    Error contract:
      * missing Clerk id                   -> 400
      * unknown position_id                -> 404
      * malformed/illegal `fen`            -> 400
      * defender to move in `fen`          -> 409 (a hint would answer for
                                              the defender, not the user)
      * position already over              -> 409 (nothing left to hint)
      * tablebase + Stockfish both fail    -> 503 (retryable; nothing written)
    """
    _require_clerk_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Ties the route to a real drill instead of a public "solve any FEN"
        # oracle; only the stored FEN is read, for the user's colour.
        cur.execute(
            """
            SELECT fen
            FROM endgame_positions
            WHERE id = %s::uuid
            """,
            (str(body.position_id),),
        )
        position = cur.fetchone()
    if position is None:
        raise HTTPException(status_code=404, detail="Endgame position not found")

    try:
        board = chess.Board(body.fen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"malformed FEN: {exc}") from exc
    if not board.is_valid():
        raise HTTPException(status_code=400, detail="illegal position")

    user_color = chess.Board(position["fen"]).turn
    if board.turn != user_color:
        raise HTTPException(
            status_code=409,
            detail=(
                "It is not your turn in that position; a hint would answer "
                "for the defender."
            ),
        )

    try:
        reply = generate_opponent_reply(board.fen())
    except TerminalPositionError as exc:
        # The drill is already over (a checked-in stale FEN, or a resolution
        # the client has not acknowledged): nothing to hint at.
        raise HTTPException(
            status_code=409,
            detail="This position is already over; there is no move to hint.",
        ) from exc
    except OpponentReplyUnavailableError as exc:
        log.error("endgame hint unavailable for %s: %s", body.fen, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not work out the best move right now. Please retry."
            ),
        ) from exc

    return EndgameHintResponse(
        position_id=str(body.position_id),
        move_uci=reply.move_uci,
        move_san=reply.move_san,
        fen_after=reply.fen_after,
        source=reply.source,
    )
