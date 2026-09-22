"""
Endgame Trainer MVP endpoints (endpoints only; the page ships later).

The loop mirrors the Puzzles page flow point-for-point, with endgame
semantics:

  GET  /api/endgames/next  -> one rating-matched position, in
                              PuzzleResponse-compatible shape.
  POST /api/endgames/move  -> grade one user move; on solved/failed write
                              users.endgame_trainer_rating and, on failed,
                              also create the endgame Woodpecker review card
                              (same transaction) and return the review-capture
                              payload; on in_progress past the stored line,
                              generate the opponent's reply and return the
                              resulting FEN.

The review queue itself (due entries, full-resolution review attempts,
badge count) lives in routers/endgame_woodpecker.py.

OPPONENT REPLIES
================
The stored puzzle line covers only the tactic; to reach the drill's real
resolution the defender may need moves beyond it. The endpoint therefore
answers each in_progress turn with either nothing (the client is still on
the stored line and plays its next move from GET /next) or an
`opponent_reply` block (the line is exhausted or the user deviated -- the
server generated the move). The trigger is positional, not a ply counter:
services/endgame_reply.stored_line_reply matches the client's fen_after
against the replayed stored line and returns the next stored move only when
that move is the opponent's. See that module for the tablebase-first
authority rule, the per-move DTZ selection policy, and the Stockfish
fallback. A generation failure is a retryable 503 with nothing committed
(in_progress never writes the rating), so replaying the same user move is
safe.

STATELESS BY DESIGN (confirmed against Puzzles before building)
================================================================
GET /api/puzzles and POST /api/puzzles/rating keep NO server-side "current
puzzle": the Puzzles page fetches a batch, owns the index client-side, and
after each solve/fail fires the rating POST. There is likewise no endgame
session/current-position table anywhere in the schema (services/
endgame_session.py documents the same precedent). This module therefore
keeps zero per-user drill state: the client remembers the position it just
received and calls GET /next again for the next one. The server-side writes
in the whole loop are the rating and, on a failed drill, the endgame
Woodpecker review card created from the failed response (one transaction,
see services/endgame_woodpecker.py). `position_id` in the move request
is used solely to load the drill's is_winning / topic difficulty from the
DB -- it is not a session lookup.

RATING MATCHING
===============
The window resolution is the exact Puzzles logic, re-expressed against
users.endgame_trainer_rating:
  * rating present -> core.rating.rating_window(rating) (+/-100, clamped
    to [400, 3000]);
  * else skill_level in SKILL_RATING_BANDS -> that band;
  * else -> the same 800-2000 defaults GET /puzzles uses.
The selection QUERY itself cannot reuse Puzzles' random-pivot keyset scan
(puzzle-id shape, per-row rating, themes GIN index); see
services/endgame_library.py's module docstring for the full analysis and
the right-sized `ORDER BY random() LIMIT 1` equivalent.

RATING WRITE + FAILED CAPTURE
=============================
The grader (services/endgame_session.evaluate_endgame_move) is pure and
already returns the rating delta on terminal statuses. This route owns the
clamp + `UPDATE users.endgame_trainer_rating` (same split as
routers/puzzles.py) and the failed-drill enrichment
(attach_common_mistake). A solved/failed move is one transaction: user row
locked FOR UPDATE, rating written, delta returned. An in_progress move
writes nothing and explicitly rolls back to release the row lock.

The FAILED response carries EndgameReviewCapture (position id, failure
reason, timestamp, topic/theme, plus enough context to re-render the
drill). The queue insert itself is not driven by the client: the route
persists the card server-side in the same transaction as the rating write,
using exactly those capture fields, so a FAILED response always means the
review card exists (or already existed -- repeat failures reuse the active
card). The card can never be fabricated by a client, unlike the puzzle
queue's public POST /api/woodpecker/entries.

HINT-ASSISTED SOLVES (hints_used)
=================================
The grader stays pure and never sees a hint count -- exactly as its own
docstring prescribes ("a move cap / hint nudge is a product-UX concern ...
belongs one layer up (route/client)"). This route owns the consequence,
because it is the one place the rating is written:

  * SOLVED + hints_used > 0 -> NEUTRAL. The user did not demonstrate the
    technique unaided, so the rating column is left untouched in both
    directions (change 0, old == new) -- including NOT establishing a first
    rating for an unrated user, whose column stays NULL and whose response
    carries rating=None. The echoed `hints_used` is what lets the resolved
    panel say "Solved (hint used)".
  * FAILED + hints_used > 0 -> unchanged. A failure is a failure: the
    rating write and the review capture below still happen.

The count itself is client-owned (the same statelessness every route here
documents); the hint route (routers/endgame_hint.py) is advisory and
write-free, and the client reports what it revealed.

RETRIES (retry)
===============
The panel's "Retry" replays a drill whose result was already recorded, so a
resolving retry move is graded for the verdict but writes nothing: no
rating change (the first attempt already moved it) and no review capture
(the first failure already queued the card). The verdict still drives the
UI, so the user gets the same solved/failed feedback as a first attempt.
"""
import logging

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor

from core.database import get_db
from core.rate_limit import limit_by_clerk_user_id
from core.rating import DEFAULT_TRAINER_RATING, clamp_rating, rating_window
from routers.puzzles import SKILL_RATING_BANDS
from schemas.endgame_schemas import (
    EndgameMoveRequest,
    EndgameMoveResponse,
    EndgameOpponentReply,
    EndgamePositionResponse,
    EndgameRatingUpdate,
    EndgameReviewCapture,
)
from services.endgame_library import select_rating_matched_position
from services.endgame_reply import (
    OpponentReplyUnavailableError,
    TerminalPositionError,
    opponent_reply_for_move,
)
from services.endgame_session import (
    STATE_CONFLICT_PHRASES,
    EndgameStatus,
    attach_common_mistake,
    evaluate_endgame_move,
)
from services.endgame_woodpecker import queue_failed_drill
from services.tablebase import TablebaseUnavailableError

router = APIRouter()
log = logging.getLogger(__name__)

# Same defaults as routers/puzzles.py's Query(800, 2000): used only when
# the user has neither an endgame_trainer_rating nor a known skill_level.
DEFAULT_MIN_RATING = 800
DEFAULT_MAX_RATING = 2000


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


@router.get("/next", response_model=EndgamePositionResponse)
def get_next_position(
    request: Request,
    # 120/min: the client fetches one position per drill, with retries when
    # the served drill is unsuitable (e.g. the page may discard a position
    # whose full-resolution line it cannot precompute). Generous enough
    # that page-driven retries never 429 a real session.
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """One rating-matched position, or 404 when the window is empty.

    Mirrors GET /puzzles' behavior of 404ing (not returning null) when no
    row matches, and its authenticated-user rating override -- here read
    from users.endgame_trainer_rating. No category/theme picker in this
    mode: the rating window is the only filter.
    """
    clerk_id = _require_clerk_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT skill_level, endgame_trainer_rating
            FROM users
            WHERE clerk_id = %s
            """,
            (clerk_id,),
        )
        user = cur.fetchone()

    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user["endgame_trainer_rating"] is not None:
        min_rating, max_rating = rating_window(user["endgame_trainer_rating"])
    elif user["skill_level"] in SKILL_RATING_BANDS:
        min_rating, max_rating = SKILL_RATING_BANDS[user["skill_level"]]
    else:
        min_rating, max_rating = DEFAULT_MIN_RATING, DEFAULT_MAX_RATING

    selection = select_rating_matched_position(
        conn, min_rating=min_rating, max_rating=max_rating
    )
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
def submit_move(
    body: EndgameMoveRequest,
    request: Request,
    # 120/min: a drill can take many moves (the win must be converted all
    # the way to checkmate/draw), and a fast player can move every ~2s.
    # Puzzles has no limit on its win/loss flow; this stays a bound, not a
    # throttle a normal session can hit.
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """Grade one user move and apply the rating side effects.

    Error contract:
      * unknown position_id                -> 404
      * unknown user                       -> 404
      * malformed/illegal move or FEN      -> 400 (client bug)
      * negative hints_used                -> 400 (client bug)
      * stale/wrong drill state (terminal fen_before, win drill fed a
        non-winning position, draw drill fed a lost one) -> 409
      * tablebase cannot answer right now  -> 503 (retryable; nothing was
        written)
      * opponent reply cannot be generated (tablebase + Stockfish both
        unavailable) -> 503 (retryable; nothing was written)
    """
    clerk_id = _require_clerk_id(request)
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

    # FOR UPDATE serializes concurrent submissions for this user so two
    # in-flight moves cannot both read the same old rating (same lock as
    # routers/puzzles.py's update path).
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT endgame_trainer_rating
            FROM users
            WHERE clerk_id = %s
            FOR UPDATE
            """,
            (clerk_id,),
        )
        user = cur.fetchone()
    if user is None:
        conn.rollback()
        raise HTTPException(status_code=404, detail="User not found")

    stored_rating = user["endgame_trainer_rating"]
    old_rating = (
        stored_rating if stored_rating is not None else DEFAULT_TRAINER_RATING
    )

    try:
        result = evaluate_endgame_move(
            body.fen_before,
            body.move,
            body.fen_after,
            drill_is_winning=position["is_winning"],
            endgame_trainer_rating=old_rating,
            topic_difficulty_rating=position["topic_difficulty_rating"],
        )
    except TablebaseUnavailableError as exc:
        conn.rollback()
        log.error("endgame move ungradeable (tablebase unavailable): %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "This position could not be verified right now. "
                "Please retry the move."
            ),
        ) from exc
    except ValueError as exc:
        conn.rollback()
        status_code = (
            409
            if any(phrase in str(exc) for phrase in STATE_CONFLICT_PHRASES)
            else 400
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    if result.status == EndgameStatus.FAILED:
        result = attach_common_mistake(conn, result, str(position["topic_id"]))

    # Built before the write below because the endgame Woodpecker capture
    # (same transaction as the rating) persists these exact fields. A retry
    # never captures: the drill's first failure already queued the card.
    review_capture = None
    if (
        not body.retry
        and result.status == EndgameStatus.FAILED
        and result.failure_category is not None
    ):
        review_capture = EndgameReviewCapture(
            position_id=str(body.position_id),
            source_puzzle_id=position["source_puzzle_id"],
            topic_id=str(position["topic_id"]),
            topic_name=position["topic_name"],
            theme=position["topic_category"],
            fen=position["fen"],
            is_winning=position["is_winning"],
            failure_category=result.failure_category,
            resolution=result.resolution,
            outcome_before=result.outcome_before,
            outcome_after=result.outcome_after,
            source_reason="wrong_answer",
            failed_at=datetime.now(timezone.utc),
        )

    # A hint-assisted solve is neutral (module docstring HINT-ASSISTED
    # SOLVES): no rating write in either direction, and an unrated user
    # stays unrated. Failures are unaffected -- hints never soften them.
    neutral_solve = (
        result.status == EndgameStatus.SOLVED and body.hints_used > 0
    )
    # A RETRY replays a drill whose result was already recorded, so its
    # resolution is unrated for the same reason: nothing may move the rating
    # a second time. The verdict still comes back for the UI.
    unrated = body.retry or neutral_solve

    rating_update = None
    if result.rating_change is None:
        # in_progress: release the FOR UPDATE lock without writing anything.
        conn.rollback()
    elif unrated:
        # Nothing is written and the lock is released. The rating block is
        # returned for the panel's "±0" only when a rating actually exists;
        # an unrated user's first rating is not established by a hinted or
        # retried solve, so their response carries rating=None.
        conn.rollback()
        if stored_rating is not None:
            rating_update = EndgameRatingUpdate(
                old_rating=stored_rating,
                new_rating=stored_rating,
                change=0,
            )
    else:
        new_rating = clamp_rating(old_rating + result.rating_change)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET endgame_trainer_rating = %s
                WHERE clerk_id = %s
                """,
                (new_rating, clerk_id),
            )
        # The failed-drill capture rides the SAME transaction as the rating
        # write (the user row lock is still held): a FAILED response that
        # promises a queued review card can never commit the rating without
        # the card, nor the card without the rating. A repeated failure of
        # the same still-active position returns the existing card instead
        # of duplicating it -- see services/endgame_woodpecker.py.
        if review_capture is not None:
            queued = queue_failed_drill(
                conn,
                clerk_id=clerk_id,
                position_id=review_capture.position_id,
                theme=review_capture.theme,
                source_reason=review_capture.source_reason,
            )
            log.info(
                "endgame review card %s for position %s (failure=%s)",
                "created" if queued.created else "reused",
                review_capture.position_id,
                review_capture.failure_category,
            )
        conn.commit()
        rating_update = EndgameRatingUpdate(
            old_rating=old_rating,
            new_rating=new_rating,
            change=result.rating_change,
        )

    # --- opponent reply ---------------------------------------------------
    # Within the stored puzzle line the client plays the line from GET /next
    # (no server generation). Once the line is exhausted -- or the user has
    # deviated off it -- the server generates the defender's move and hands
    # the resulting FEN back so the drill can continue to resolution. The
    # same composition answers reviews in routers/endgame_woodpecker.py.
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
            # Unreachable for a graded IN_PROGRESS move; surface as an
            # internal integrity error rather than returning no reply.
            log.error("opponent reply requested for a terminal position: %s", exc)
            raise HTTPException(
                status_code=500, detail="Internal drill state error"
            ) from exc
        except OpponentReplyUnavailableError as exc:
            log.error("opponent reply unavailable for %s: %s", body.fen_after, exc)
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
        rating=rating_update,
        review_capture=review_capture,
        opponent_reply=opponent_reply,
    )
