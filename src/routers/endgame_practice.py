"""
Category-picker practice mode -- the third Endgame Trainer surface.

  GET  /api/endgames/practice/categories -> present categories + counts
  GET  /api/endgames/practice/next?category=... -> one position in that category
  POST /api/endgames/practice/move -> grade one move; NO rating, NO capture

This is pure practice/exploration: unlike the rated loop
(routers/endgames.py), a solved/failed move here writes NOTHING --
users.endgame_trainer_rating is never touched, and a failed position is
not captured into the endgame Woodpecker queue.

WHY SOURCED-ONLY
================
The curated Lucena Position topic (10 rows) lives in category "rook"
alongside the 9,219 sourced rook positions, but conceptually belongs to
the nav-library path as a hand-authored teaching set. The practice picker
is a material-category browser over the imported pool:
  * list counts and draws must agree (a count of 9,219 that can hand back
    one of 10 hidden curated rows is wrong);
  * the curated topic has no source puzzle line, so it is the only rook
    content without the stored-line/explanatory flow this mode offers;
  * the task framing places curated content with the library, not here.
Both the list and the draw therefore filter on
`source_puzzle_id IS NOT NULL` (the reliable sourced marker: every one of
the 22,378 imported rows has it, and all 10 curated rows have it NULL).
The library selector keeps the flag off by default, so the nav-library
category path still includes Lucena.

WHY NO WOODPECKER CAPTURE ON FAILED
===================================
The rated route's capture is not embedded in grading or in a shared
helper: evaluate_endgame_move() is pure and the capture is route-layer
code that runs inside the SAME transaction as the rating write
(routers/endgames.py). Practice mode writes no rating, so there is no
transaction to join -- omitting the capture is simply not calling
queue_failed_drill(), not a toggle.
The product reason stands on its own: practice is deliberately low-stakes
exploration. A user opening "pure_pawn" and blundering through five
positions while exploring would create five FSRS review obligations from
what was not training. The rated loop is the thing that "counts", and it
already captures its failures; practice failures must not silently seed
the review queue. The FAILED response still carries the full grader block
and the authored common-mistake explanation (a read-only enrichment), just
no rating and no capture.

HINTS (hints_used) -- tracking only, no scoring
================================================
"Get solution" is available here like the other two surfaces, and the move
request carries the same client-owned hints_used count. Practice has no
rating and no FSRS card, so there is nothing for a hint to neutralize: the
field is accepted (the shared EndgameMoveRequest keeps one shape) and
echoed on the response purely so the panel can show "Solved (hint used)".
Nothing is persisted, like every other practice outcome.

AUTH / USER ROW
===============
Same Clerk pattern as the other endgame endpoints (internal-secret
middleware + X-Clerk-User-Id). Deliberately no users-row lookup: these
routes read and write nothing user-scoped, so requiring a users row would
be an empty round-trip; a valid Clerk session can practice even if the
local row is missing or stale. An absent header is still a 400.

GRADING / FULL RESOLUTION
=========================
Identical to the rated route: evaluate_endgame_move() (drill-type guard,
python-chess resolution detection) plus the shared
opponent_reply_for_move() generator so a drill can be played past the
stored line to actual checkmate/draw, and attach_common_mistake() on
FAILED. Positions are not restricted to the sourced pool at grading time:
the endpoint grades whatever position_id it is given, exactly like the
rated move endpoint, and nothing persists either way.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg2.extras import RealDictCursor

from core.database import get_db
from core.rate_limit import limit_by_clerk_user_id
from schemas.endgame_schemas import (
    EndgameMoveRequest,
    EndgameMoveResponse,
    EndgameOpponentReply,
    EndgamePositionResponse,
    EndgamePracticeCategory,
)
from services.endgame_library import list_categories, select_drill_position
from services.endgame_reply import (
    OpponentReplyUnavailableError,
    TerminalPositionError,
    opponent_reply_for_move,
)
from services.endgame_session import (
    STATE_CONFLICT_PHRASES,
    EndgameStatus,
    attach_common_mistake,
    evaluate_defender_reply,
    evaluate_endgame_move,
)
from services.tablebase import TablebaseUnavailableError

router = APIRouter()
log = logging.getLogger(__name__)


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


@router.get("/categories", response_model=List[EndgamePracticeCategory])
def get_categories(request: Request, conn=Depends(get_db)):
    """Categories that actually have sourced positions, with counts.

    Dynamic: the DB is the source of truth, so the three unseeded CHECK
    values are absent and new sourced content appears without a code
    change. Ordered by category name.
    """
    _require_clerk_id(request)
    return list_categories(conn, sourced_only=True)


@router.get("/next", response_model=EndgamePositionResponse)
def get_next_practice_position(
    request: Request,
    category: str = Query(..., min_length=1),
    # Same 120/min bound as the rated GET /next: the page may discard draws
    # it cannot precompute and retry, and that is normal use.
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """One random sourced position from the chosen category.

    Error contract:
      * unknown category (not a CHECK value) -> 400
      * valid category with no sourced rows  -> 404
    """
    _require_clerk_id(request)

    try:
        selection = select_drill_position(
            conn, category=category, sourced_only=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if selection.status == "empty" or selection.position is None:
        raise HTTPException(status_code=404, detail=selection.reason)

    position = selection.position
    return EndgamePositionResponse(
        id=str(position.position_id),
        fen=position.fen,
        moves=position.solution_moves,
        rating=position.topic_difficulty_rating,
        themes=[position.topic_category, position.topic_name],
        gameUrl=(
            f"https://lichess.org/training/{position.source_puzzle_id}"
            if position.source_puzzle_id
            else None
        ),
        is_winning=position.is_winning,
        topic_id=str(position.topic_id),
        topic_name=position.topic_name,
        topic_category=position.topic_category,
        source_puzzle_id=position.source_puzzle_id,
    )


@router.post("/move", response_model=EndgameMoveResponse)
def submit_practice_move(
    body: EndgameMoveRequest,
    request: Request,
    # Same 120/min bound as the rated POST /move: a win must be converted
    # all the way to checkmate, and a fast player can move every ~2s.
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """Grade one practice move; never write rating or queue state.

    Error contract mirrors the rated move endpoint:
      * unknown position_id                -> 404
      * malformed/illegal move or FEN      -> 400
      * stale/wrong drill state            -> 409
      * tablebase cannot answer right now  -> 503
      * opponent reply cannot be generated -> 503

    Response is the shared EndgameMoveResponse with `rating` and
    `review_capture` always None (nothing was written) -- see the module
    docstring for why practice failures are not captured into Woodpecker.
    `hints_used` is accepted and echoed for display only: practice has no
    scoring, so there is no consequence to neutralize (and nothing is
    persisted either way).
    """
    _require_clerk_id(request)
    if body.hints_used < 0:
        raise HTTPException(status_code=400, detail="hints_used cannot be negative")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT p.fen,
                   p.is_winning,
                   p.source_puzzle_id,
                   t.id AS topic_id,
                   t.name AS topic_name,
                   t.category AS topic_category,
                   t.difficulty_rating AS topic_difficulty_rating,
                   z.moves AS source_moves
            FROM endgame_positions p
            JOIN endgame_topics t ON t.id = p.topic_id
            LEFT JOIN puzzles z ON z.id = p.source_puzzle_id
            WHERE p.id = %s::uuid
            """,
            (str(body.position_id),),
        )
        position = cur.fetchone()
    if position is None:
        raise HTTPException(status_code=404, detail="Endgame position not found")

    try:
        result = evaluate_endgame_move(
            body.fen_before,
            body.move,
            body.fen_after,
            drill_is_winning=position["is_winning"],
            start_fen=position["fen"],
            history=body.history,
        )
    except TablebaseUnavailableError as exc:
        log.error("practice move ungradeable (tablebase unavailable): %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "This position could not be verified right now. "
                "Please retry the move."
            ),
        ) from exc
    except ValueError as exc:
        status_code = (
            409
            if any(phrase in str(exc) for phrase in STATE_CONFLICT_PHRASES)
            else 400
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    opponent_reply = None
    if result.status == EndgameStatus.IN_PROGRESS:
        source_moves = (
            position["source_moves"].split() if position["source_moves"] else None
        )
        try:
            generated = opponent_reply_for_move(
                position["fen"], source_moves, body.fen_after
            )
        except TerminalPositionError as exc:
            log.error("practice reply requested for a terminal position: %s", exc)
            raise HTTPException(
                status_code=500, detail="Internal drill state error"
            ) from exc
        except OpponentReplyUnavailableError as exc:
            log.error("practice opponent reply unavailable for %s: %s", body.fen_after, exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not generate the opponent's reply right now. "
                    "Please retry the move."
                ),
            ) from exc
        if generated is not None:
            opponent_reply = EndgameOpponentReply(
                move_uci=generated.move_uci,
                move_san=generated.move_san,
                fen_after=generated.fen_after,
                source=generated.source,
            )
            # The defender's own move can end the game (fifty-move clock on
            # the second mover's halfmove; stalemate/insufficient in a draw
            # drill). Practice writes nothing, but the verdict must not wait
            # for a next user move that may not exist.
            defender_result = evaluate_defender_reply(
                generated.fen_after,
                drill_is_winning=position["is_winning"],
            )
            if defender_result is not None:
                result = defender_result.model_copy(
                    update={
                        "outcome_before": result.outcome_before,
                        "outcome_after": result.outcome_after,
                        "dtz_before": result.dtz_before,
                        "dtz_after": result.dtz_after,
                    }
                )

    # Enriched after the defender's move is adjudicated: that move is what
    # turned the drill into a FAILED ran-out-of-moves result here.
    if result.status == EndgameStatus.FAILED:
        result = attach_common_mistake(conn, result, str(position["topic_id"]))

    return EndgameMoveResponse(
        position_id=str(body.position_id),
        status=result.status,
        failure_category=result.failure_category,
        resolution=result.resolution,
        outcome_before=result.outcome_before,
        outcome_after=result.outcome_after,
        dtz_before=result.dtz_before,
        dtz_after=result.dtz_after,
        common_mistake=result.common_mistake,
        hints_used=body.hints_used,
        rating=None,
        review_capture=None,
        opponent_reply=opponent_reply,
    )
