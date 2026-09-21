"""
"Play it out" continuation for a FAILED drill -- shared by all three
Endgame Trainer surfaces (rated trainer, Woodpecker review, practice).

  POST /api/endgames/playout/reply  -> the defender's next move, read-only
  POST /api/endgames/playout/finish -> fast-forward to the final position/verdict

WHY THIS EXISTS
===============
A FAILED verdict is final the instant services.endgame_session
.evaluate_endgame_move() returns it. The rated route has already written
the rating, and the review route has already scheduled FSRS and logged its
attempt, in the same transaction as the graded move. "Play it out" is an
additional continuation layered on top: the user watches the already-decided
ending unfold against the same defender generator
(services.endgame_reply.opponent_reply_for_move -- no new opponent logic).

`/finish` is the same continuation with the length control: it plays the
generator's optimal line for BOTH sides server-side and returns the terminal
position, so a drawn shuffle that would take 50 moves to explore is one
click. When no cheap concrete line exists (material beyond the local Syzygy
files costs 3-13s PER PLY on the Lichess fallback), it returns the tablebase
verdict instead -- still a final answer, just without a position to jump to.
Neither shape can change the recorded result.

NO WRITE PATH
=============
This router deliberately has no way to change anything that was recorded:
  * it never calls evaluate_endgame_move (the user's continuation moves are
    not graded and cannot produce a second verdict);
  * it never writes users.endgame_trainer_rating;
  * it never calls queue_failed_drill, because there is no transaction to
    join -- omitting the capture is the absence of a call, not a toggle.
Whatever the continuation does -- including steering the game to a
different final position than the tablebase-optimal line would have reached
on its own -- leaves the recorded partial outcome untouched.

TERMINAL POSITIONS
==================
A playout ends at a real board ending (checkmate / stalemate / a draw by
rule). The client detects that in its own chess.js game and stops asking.
When it does ask about an already-over position, the generator's
TerminalPositionError is mapped to `opponent_reply=None` rather than a 500:
"the game is over" is the normal end of a playout, not an internal error.
`None` also covers the stored-line case (the client already holds the next
stored move, exactly like a graded replay).

SCOPE
=====
Only the defender's reply is generated. The route does not need the user's
move, so it cannot mistype it into a fresh grading call: the request body is
(position_id, fen_after).
"""
import logging
import time

import chess
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor

from core.database import get_db
from core.rate_limit import limit_by_clerk_user_id
from schemas.endgame_schemas import (
    EndgameOpponentReply,
    EndgamePlayoutFinishRequest,
    EndgamePlayoutFinishResponse,
    EndgamePlayoutReplyRequest,
    EndgamePlayoutReplyResponse,
)
from services.endgame_reply import (
    OpponentReplyUnavailableError,
    TerminalPositionError,
    generate_opponent_reply,
    opponent_reply_for_move,
    terminal_reason,
)
from services.tablebase import TablebaseUnavailableError, probe_tablebase

router = APIRouter()
log = logging.getLogger(__name__)

# Fast-forward bounds. Mate/conversion lines measured 13-25 plies and drawn
# shuffles 18-20, so the ply cap is far beyond anything a rational
# continuation needs -- it only exists so a pathological policy cycle cannot
# run away. The wall clock is belt-and-braces on top (locally-covered
# positions cost ~20ms/ply).
MAX_FASTFORWARD_PLIES = 120
FASTFORWARD_BUDGET_SECONDS = 10.0
# The local Syzygy set covers 3-5 men. Beyond that every probe becomes a
# Lichess API round trip (measured 3-13s PER PLY for 6 men), so the
# fast-forward stops and the tablebase verdict becomes the answer instead.
LOCAL_COVERAGE_MEN = 5

# python-chess rule names -> the wire's ending literals.
_ENDING_BY_REASON = {
    "checkmate": "checkmate",
    "stalemate": "stalemate",
    "insufficient material": "insufficient_material",
    "fifty-move rule": "fifty_move_rule",
    "seventy-five-move rule": "seventy_five_move_rule",
    "fivefold repetition": "fivefold_repetition",
}

# Tablebase verdicts are from the side-to-move's perspective.
_INVERT = {"win": "loss", "loss": "win", "draw": "draw"}


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


def _fast_forward(fen: str):
    """Play the tablebase-optimal line for both sides to a terminal position.

    Returns (board, reason, plies). `reason` is None when the loop stopped
    early -- beyond local coverage, out of budget, or the generator could not
    answer -- in which case the caller falls back to the verdict. The
    returned board's move_stack IS the played line, in order: the board is
    built from `fen` (empty stack) and every pushed move is appended, so
    [m.uci() for m in board.move_stack] is exactly what /finish hands the
    client to step through. It is empty when nothing was played (terminal
    input) and must not be exposed on the verdict path (no final position to
    step to).

    Uses the same generator the live playout uses (DTZ-optimal conversion,
    longest-resistance defense, deterministic lowest-UCI draw policy), so the
    skipped line is exactly the line a user playing both sides optimally
    would have produced. No grading, no writes.
    """
    board = chess.Board(fen)
    reason = terminal_reason(fen)
    if reason is not None:
        return board, reason, 0

    deadline = time.monotonic() + FASTFORWARD_BUDGET_SECONDS
    plies = 0
    while plies < MAX_FASTFORWARD_PLIES and time.monotonic() < deadline:
        if len(board.piece_map()) > LOCAL_COVERAGE_MEN:
            return board, None, plies
        try:
            reply = generate_opponent_reply(board.fen())
        except OpponentReplyUnavailableError:
            return board, None, plies
        board.push(chess.Move.from_uci(reply.move_uci))
        plies += 1
        reason = terminal_reason(board.fen())
        if reason is not None:
            return board, reason, plies
    return board, None, plies


@router.post("/reply", response_model=EndgamePlayoutReplyResponse)
def generate_playout_reply(
    body: EndgamePlayoutReplyRequest,
    request: Request,
    # Same 120/min bound as the move routes: a playout is a legal move every
    # couple of seconds at the fastest, on top of the graded replay that
    # preceded it.
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """The defender's reply for a settled drill's continuation.

    Error contract:
      * missing Clerk id                  -> 400
      * malformed fen_after               -> 400
      * unknown position_id               -> 404
      * tablebase/Stockfish cannot reply  -> 503 (nothing written)
      * position already over             -> 200 with opponent_reply=None
      * still inside the stored line      -> 200 with opponent_reply=None
    """
    _require_clerk_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Same position lookup shape as the three move routes: the stored FEN
        # plus the sourced puzzle's move list, which is all
        # opponent_reply_for_move() needs to stay on the stored line.
        cur.execute(
            """
            SELECT p.fen,
                   z.moves AS source_moves
            FROM endgame_positions p
            LEFT JOIN puzzles z ON z.id = p.source_puzzle_id
            WHERE p.id = %s::uuid
            """,
            (str(body.position_id),),
        )
        position = cur.fetchone()
    if position is None:
        raise HTTPException(status_code=404, detail="Endgame position not found")

    source_moves = (
        position["source_moves"].split() if position["source_moves"] else None
    )
    try:
        generated = opponent_reply_for_move(
            position["fen"], source_moves, body.fen_after
        )
    except TerminalPositionError:
        # The playout reached a real ending: there is nothing to answer, and
        # that is a successful end of the continuation, not an error.
        return EndgamePlayoutReplyResponse(opponent_reply=None)
    except ValueError as exc:
        # Malformed/illegal fen_after (TerminalPositionError is caught above;
        # it subclasses ValueError).
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OpponentReplyUnavailableError as exc:
        log.error("playout reply unavailable for %s: %s", body.fen_after, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not generate the defender's reply right now. "
                "Please retry the move."
            ),
        ) from exc

    if generated is None:
        # Still on the stored line: the client already carries the next
        # stored move, exactly like the graded replay, so the server stays
        # quiet.
        return EndgamePlayoutReplyResponse(opponent_reply=None)

    return EndgamePlayoutReplyResponse(
        opponent_reply=EndgameOpponentReply(
            move_uci=generated.move_uci,
            move_san=generated.move_san,
            fen_after=generated.fen_after,
            source=generated.source,
        )
    )


@router.post("/finish", response_model=EndgamePlayoutFinishResponse)
def finish_playout(
    body: EndgamePlayoutFinishRequest,
    request: Request,
    # Heavier than one reply -- it may play a whole line of probes -- so a
    # tighter bound than the move routes: one or two skips per drill is all a
    # user can need.
    _: None = Depends(limit_by_clerk_user_id(limit=30, window=60)),
    conn=Depends(get_db),
):
    """Fast-forward a settled drill's continuation to its final result.

    Error contract:
      * missing Clerk id                  -> 400
      * malformed/illegal `fen`           -> 400
      * unknown position_id               -> 404
      * tablebase cannot resolve it       -> 503 (nothing written)

    Returns either a concrete terminal position (`fen` + `ending` + `plies`)
    or the tablebase verdict (`outcome`) when no cheap line exists. Either
    way the recorded rating / FSRS / capture state is untouched: this route
    never grades and never writes.
    """
    _require_clerk_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Only the drill's stored FEN is needed: its side to move is the
        # user's colour, which is what makes `outcome` speak from the user's
        # perspective no matter whose turn the request position is at.
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

    # Already over (for example a stalemating blunder): the input IS final.
    reason = terminal_reason(board.fen())
    if reason is not None:
        ending = _ENDING_BY_REASON.get(reason)
        if ending is not None:
            return EndgamePlayoutFinishResponse(
                fen=board.fen(), ending=ending, plies=0
            )

    try:
        root = probe_tablebase(board.fen())
    except TablebaseUnavailableError as exc:
        log.error("playout finish could not resolve %s: %s", body.fen, exc)
        raise HTTPException(
            status_code=503,
            detail="Could not resolve this position right now. Please retry.",
        ) from exc

    root_outcome = root.outcome
    if root_outcome not in _INVERT:
        # Defensive: the verdict path promises a known result.
        raise HTTPException(
            status_code=503,
            detail="Could not resolve this position right now. Please retry.",
        )

    user_color = chess.Board(position["fen"]).turn
    outcome = (
        root_outcome if board.turn == user_color else _INVERT[root_outcome]
    )

    final_board, ending_reason, plies = _fast_forward(board.fen())
    if ending_reason is not None:
        ending = _ENDING_BY_REASON.get(ending_reason)
        if ending is not None:
            return EndgamePlayoutFinishResponse(
                outcome=outcome,
                fen=final_board.fen(),
                ending=ending,
                plies=plies,
                # The whole line, in order: the client replays it one move at
                # a time with no further requests. Empty only when the
                # request position was already terminal (handled above).
                line=[move.uci() for move in final_board.move_stack],
            )

    # No cheap concrete line (beyond local coverage, out of budget, or the
    # generator could not answer): the verdict is the result.
    return EndgamePlayoutFinishResponse(outcome=outcome, plies=plies)
