"""
Session state machine for the Endgame Trainer (per-move grading).

evaluate_endgame_move(fen_before, move, fen_after) is called for EVERY move
the user plays in a drill and returns one of:

  * in_progress -- the move preserved the drill's outcome (win stayed win /
    draw stayed draw) and the game has not resolved on the board yet. The
    session keeps going; being tablebase-winning but not yet checkmate is
    NOT solved.
  * solved      -- the game actually resolved on the board in the drill's
    favor: checkmate delivered (win drill), or a legitimate board-draw
    reached (draw drill; also counts if the defender over-delivers and
    mates). python-chess resolution, NOT the tablebase verdict: the
    tablebase says what the position is worth, only the board says the
    game ended.
  * failed      -- the move lost the drill: threw away a win, blundered a
    win into a loss, or lost a draw. Fails IMMEDIATELY on the offending
    move, with a failure_category granular enough to key
    endgame_common_mistakes (see attach_common_mistake below) -- never a
    generic "wrong".

STATELESS PRECEDENT (confirmed before building)
===============================================
Same architecture as Opponent Preparation's sparring endpoint and Engine
Sparring's POST /train/engine-sparring-move (src/routers/train.py): the
frontend owns the chess.js game and sends FEN (+ move) per request; there
is NO backend session/game-state table. evaluate_endgame_move() is a pure
transition grader -- every call is self-contained, derivable entirely from
the three FENs/move passed in. This matches the codebase's established
stateless-sparring design; no DB session table is introduced.

WHY RE-PROBE INSTEAD OF TRUSTING is_winning
===========================================
The drill row's is_winning is known, but a buggy or compromised client
supplies the FENs, so this function treats NOTHING as pre-verified:
probe_tablebase(fen_before) re-derives the current position's outcome from
the local Syzygy files, and probe_tablebase(fen_after) re-derives the
post-move state. The stored flag is only used as a cross-check the caller
can run (drill_is_winning guard below), never as the verdict itself.

DRILL-TYPE GUARD (drill_is_winning)
===================================
Optional bool = the drill's endgame_positions.is_winning. When provided:
  * win drill: probe(fen_before) for the user MUST be "win" -- a "draw"
    means the win was already thrown away on some earlier move (the session
    should have failed then), a "loss" likewise. Receiving either means the
    client is replaying/stale/wrong FENs -> ValueError (route maps to
    400/409, the same integrity tier as engine-sparring's terminal/bot-turn
    checks).
  * draw drill: probe(fen_before) must be "draw" or "win" (the opponent may
    have blundered between user moves, handing the user a win -- that is a
    legitimate mid-drill state, hold or convert as you like). A "loss"
    cannot arise from correct grading -> same ValueError.
None (default) skips the guard so the function stays usable ad hoc.

RESOLUTION DETECTION (python-chess, not tablebase)
==================================================
A tablebase "win" that is not yet checkmate is IN_PROGRESS by definition --
the whole point of a Lucena drill is the LAST move too. Resolution checked
on the position after the user's move:
  checkmate            -> the user's side delivered mate -> solved
  stalemate / insufficient material / fifty-move rule (halfmove clock >=
  100) / threefold repetition -> the game is genuinely drawn on the board
  -> solved for a draw drill, failed for a win drill (see
  _terminal_verdict()).
Stalemate, insufficient material and the fifty-move rule are detectable
statelessly (the clock and material ride in the FEN). Threefold repetition
needs the game's history: callers that own the full game pass `start_fen`
plus `history` (the UCI moves played since the drill's stored start
position), and evaluate_endgame_move() rebuilds the board WITH its move
stack so python-chess can count the repetition. Without them the check is
skipped, never guessed -- a stateless single-transition call cannot know.

START FEN + HISTORY (optional, for repetition)
===============================================
`start_fen` is the drill's endgame_positions.fen (the position the client
started from); `history` is every move played since, UCI, oldest first,
NOT including the move being graded. The replay is validated move-by-move
(legality, then the first-4-FEN-fields cross-check against fen_before), so
client-supplied history is never trusted. A mismatch is a client-integrity
ValueError (route -> 400), exactly like a fabricated fen_after.

RATING UPDATE
=============
On solved/failed (never in_progress), if endgame_trainer_rating and
topic_difficulty_rating are supplied, rating_change is computed by the
EXISTING core.rating.calculate_rating_change() -- the exact +/-3/5/8
banding used for puzzles -- no new math. Clamping to [400, 3000] and
writing users.endgame_trainer_rating stays in the route layer (same split
as puzzles.py, which clamps + persists there; no history table exists for
endgames yet either).

COMMON-MISTAKE CONTENT (attach_common_mistake)
==============================================
Failure categories key the endgame_common_mistakes content table
(topic-level, one row per (topic, failure type); schema in
core/migrations.py). The step brief asked to attach that explanation on
this module's FAILED path -- done, but deliberately NOT inside
evaluate_endgame_move(). That function is pure: no DB connection, no
topic, every call derivable from the FENs alone (see STATELESS PRECEDENT
above). Injecting conn + topic into it for a presentational lookup would
reopen exactly that contract, and would make the grading hot path depend
on a DB round-trip. Instead this module exposes:

    attach_common_mistake(conn, result, topic_id) -> EndgameMoveResult

The route grades with evaluate_endgame_move() and then enriches a failed
result with this one explicit call. Semantics:
  * non-failed result -> returned unchanged, no query at all;
  * failed + authored row -> a copy of the result whose common_mistake
    carries the explanation (a missing row -> common_mistake=None, the
    normal case until a topic is authored: content absence NEVER raises);
  * a genuine DB failure propagates (an infrastructure problem, not a
    content gap) -- the same tiering as tablebase.py's fallback.
This keeps the DB ownership in the route layer, matching puzzles.py
(queries in the route, rating math pure), while the caller still receives
one EndgameMoveResult carrying classification + explanation together.

ABANDONED STATUS -- deliberately NOT here
=========================================
See the step report: a move cap / hint nudge is a product-UX concern (how
many moves a user may take, when to offer help). This function sees one
transition and has no move counter to check; the stateless frontend owns
the move count. It belongs one layer up (route/client), not here. The
CLOSEST thing inside this function is the fifty-move rule, which is a real
RULE of chess (the position itself runs out), not a session policy.

MOVE FORMAT
===========
`move` accepts UCI ("e2e4", "e7e8q" -- what chess.js sends natively) or
SAN ("Rd1+"). Legality is verified against fen_before server-side; a legal
FEN+illegal move is a client-integrity error -> ValueError, not a graded
verdict.
fen_after is cross-checked against the position DERIVED from
fen_before+move (first 4 FEN fields: board/turn/castling/ep -- the same
normalization repertoire_positions uses), so a client cannot claim an
outcome for a position the move does not actually produce. The derived
board is authoritative for all grading.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional, Sequence
from uuid import UUID

import chess
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core.rating import calculate_rating_change
from services.tablebase import (
    TablebaseResult,
    TablebaseUnavailableError,
    probe_tablebase,
)

log = logging.getLogger(__name__)


class EndgameStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    SOLVED = "solved"
    FAILED = "failed"


class EndgameFailureCategory(str, Enum):
    """Granular failure kinds -- feeds endgame_common_mistakes later."""

    # Win drill: a move turned a tablebase win into a tablebase draw.
    THREW_AWAY_WIN = "threw_away_win"
    # Win drill: a move turned a tablebase win into a tablebase loss.
    BLUNDERED_INTO_LOSS = "blundered_into_loss"
    # Draw drill: a move turned a tablebase draw into a tablebase loss.
    LOST_THE_DRAW = "lost_the_draw"
    # Win drill: the game reached a board-draw (e.g. the fifty-move clock
    # expired) without the user ever playing a tablebase-losing move -- they
    # shuffled forever instead of converting. Distinct from the three move
    # categories above because no single move was to blame.
    RAN_OUT_OF_MOVES = "ran_out_of_moves"


class EndgameResolution(str, Enum):
    """How the game actually ended on the board (python-chess-detected)."""

    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    INSUFFICIENT_MATERIAL = "insufficient_material"
    FIFTY_MOVE_RULE = "fifty_move_rule"
    # The same position occurred three times (a claimable draw). Detected
    # only when the caller supplied start_fen + history -- see the module
    # docstring's START FEN + HISTORY section.
    THREEFOLD_REPETITION = "threefold_repetition"
    # Degraded drill-end: the user PROMOTED with the win intact while NO
    # tablebase source could grade the follow-up (promotion adds a piece,
    # e.g. KRPvKR -> KQRvKR = 6 men; with the Lichess fallback active the
    # probe usually succeeds and the drill instead continues to real
    # checkmate — this resolution only fires when the fallback is disabled
    # or unreachable, i.e. offline). The tablebase's own DTZ metric
    # measures distance-to-promotion for exactly this reason. NOT used for
    # captures (they stay within local coverage).
    PROMOTION = "promotion"


class EndgameMoveResult(BaseModel):
    status: EndgameStatus
    # None unless status == failed.
    failure_category: Optional[EndgameFailureCategory] = None
    # None until the game resolved on the board (solved/failed verdicts).
    resolution: Optional[EndgameResolution] = None
    # Tablebase verdicts FROM THE USER'S PERSPECTIVE (inverted off the
    # side-to-move of each FEN), with DTZ when stored.
    outcome_before: Optional[str] = None
    outcome_after: Optional[str] = None
    dtz_before: Optional[int] = None
    dtz_after: Optional[int] = None
    # Present only on solved/failed when both ratings were supplied:
    # core.rating.calculate_rating_change() delta (clamping to [400, 3000]
    # is the route's job, same as puzzles.py).
    rating_change: Optional[int] = None
    # Authored endgame_common_mistakes explanation for this (topic, failure
    # type), attached by attach_common_mistake() -- never by the pure
    # grader. None on non-failed results, and on failed results whose
    # topic/failure type has no authored content yet.
    common_mistake: Optional[str] = None


# ValueError phrases from evaluate_endgame_move that mean "the client's
# game state conflicts with the drill" (route -> 409) rather than "the
# request is malformed" (route -> 400) -- the same 400/409 integrity tier
# the engine-sparring route uses. Shared by the trainer route
# (routers/endgames.py) and the review-queue route
# (routers/endgame_woodpecker.py) so the two cannot drift.
STATE_CONFLICT_PHRASES = (
    "already a terminal position",
    "non-winning fen_before",
    "lost fen_before",
)


def _probe_for_user(fen: str, user_is_side_to_move: bool) -> TablebaseResult:
    """probe_tablebase(), reinterpreted FROM THE USER'S PERSPECTIVE.

    The local tablebase answers for the side to move; when grading the
    position AFTER the user's move the side to move is the opponent, so the
    verdict is inverted (win<->loss, draw stays draw).
    """
    result = probe_tablebase(fen)
    if user_is_side_to_move:
        return result
    inverted = {"win": "loss", "draw": "draw", "loss": "win"}[result.outcome]
    result.outcome = inverted
    return result


def _terminal_verdict(
    board: chess.Board, drill_is_winning: Optional[bool]
) -> Optional[tuple[EndgameStatus, Optional[EndgameFailureCategory], EndgameResolution]]:
    """Detect an actually-resolved game on the board and return the drill
    verdict, or None when the game is still live.

    Resolution precedence over transition classification: a move that both
    resolves the game and would otherwise be graded by the transition table
    is judged by the RESOLUTION (e.g. delivering stalemate in a win drill is
    'threw away the win', not 'in progress'). Checkmate always wins the
    session regardless of drill type (a draw-drill user who mates exceeded
    the goal).
    """
    if board.is_checkmate():
        return (EndgameStatus.SOLVED, None, EndgameResolution.CHECKMATE)
    if board.is_stalemate():
        resolution = EndgameResolution.STALEMATE
    elif board.is_insufficient_material():
        resolution = EndgameResolution.INSUFFICIENT_MATERIAL
    elif board.is_fifty_moves():
        resolution = EndgameResolution.FIFTY_MOVE_RULE
    elif board.is_repetition(3):
        # Threefold is a CLAIM in the rules; the trainer auto-adjudicates it
        # (same as lichess/chess.com apply the claim for you). Uses
        # is_repetition(3), not can_claim_threefold_repetition(): the latter
        # also fires when the claim would only become available after one of
        # the side-to-move's moves, which is not the position that resolved.
        # Only ever true on a board rebuilt with its move stack (the
        # start_fen + history path); FEN-built boards return False.
        resolution = EndgameResolution.THREEFOLD_REPETITION
    else:
        return None
    if drill_is_winning is False:
        return (EndgameStatus.SOLVED, None, resolution)
    # Win drill (or guard-free call) reaching a board draw: the win died,
    # but no single move threw it -- the clock/structure did.
    return (EndgameStatus.FAILED, EndgameFailureCategory.RAN_OUT_OF_MOVES, resolution)


def board_from_history(
    start_fen: str, history: Sequence[str], fen_before: str
) -> chess.Board:
    """Rebuild the live board from the drill's start position plus every move
    played since, so repetition is countable.

    Raises ValueError for a malformed start/fen_before, an illegal or
    malformed history move, or a replay that does not end at fen_before
    (first 4 FEN fields, the same normalization as the fen_after
    cross-check). Client-supplied history is never trusted.
    """
    try:
        board = chess.Board(start_fen)
    except ValueError as exc:
        raise ValueError(f"malformed FEN (start) {start_fen!r}: {exc}") from exc
    if not board.is_valid():
        raise ValueError(f"illegal position (start) {start_fen!r}")

    for index, uci in enumerate(history):
        try:
            played = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise ValueError(f"malformed history move #{index} {uci!r}") from exc
        if played not in board.legal_moves:
            raise ValueError(
                f"illegal history move #{index} {uci!r} in {board.fen()!r}"
            )
        board.push(played)

    try:
        claimed = chess.Board(fen_before)
    except ValueError as exc:
        raise ValueError(f"malformed FEN (fen_before) {fen_before!r}: {exc}") from exc
    if not claimed.is_valid():
        raise ValueError(f"illegal position (fen_before) {fen_before!r}")
    replayed_key = " ".join(board.fen().split()[:4])
    claimed_key = " ".join(claimed.fen().split()[:4])
    if replayed_key != claimed_key:
        raise ValueError(
            f"history does not lead to fen_before (replayed {replayed_key!r}, "
            f"claimed {claimed_key!r})"
        )
    return board


def evaluate_endgame_move(
    fen_before: str,
    move: str,
    fen_after: str,
    drill_is_winning: Optional[bool] = None,
    endgame_trainer_rating: Optional[int] = None,
    topic_difficulty_rating: Optional[int] = None,
    start_fen: Optional[str] = None,
    history: Optional[Sequence[str]] = None,
) -> EndgameMoveResult:
    """Grade one user move in an endgame drill. See the module docstring.

    `start_fen` + `history` are optional and only enable threefold-repetition
    detection; without them the function stays exactly as stateless as
    before (see the docstring's START FEN + HISTORY section).
    """
    # --- 1. fen_before: parse, legality, resolution, re-probe guard -------
    try:
        board_before = chess.Board(fen_before)
    except ValueError as exc:
        raise ValueError(f"malformed FEN (fen_before) {fen_before!r}: {exc}") from exc
    if not board_before.is_valid():
        raise ValueError(f"illegal position (fen_before) {fen_before!r}")

    if history:
        if not start_fen:
            raise ValueError(
                "history was supplied without the drill's start position"
            )
        board_before = board_from_history(start_fen, history, fen_before)

    if board_before.is_checkmate() or board_before.is_stalemate():
        raise ValueError(
            f"fen_before {fen_before!r} is already a terminal position; "
            "the previous request must resolve the session instead"
        )

    # --- 2. move: parse (UCI first, SAN fallback) and verify legality -----
    try:
        parsed = chess.Move.from_uci(move)
        if parsed not in board_before.legal_moves:
            raise ValueError(f"illegal move {move!r} in {fen_before!r}")
    except ValueError as exc:
        if "illegal move" in str(exc):
            raise
        try:
            parsed = board_before.parse_san(move)
        except ValueError as exc2:
            raise ValueError(
                f"unrecognized move {move!r} in {fen_before!r}"
            ) from exc2
        # SAN given: re-derive so downstream code always has UCI semantics.

    # copy() keeps the move stack: the position after this move must be able
    # to count repetitions against the whole drill (board_before carries the
    # stack only when start_fen + history were supplied).
    derived_after = board_before.copy()
    derived_after.push(parsed)

    # --- 3. fen_after: cross-check against the derived position -----------
    # First 4 fields only (board/turn/castling/ep), the repertoire_positions
    # normalization, so a counter-only difference cannot fail the check --
    # but a client claiming a DIFFERENT board cannot slip through.
    try:
        claimed = chess.Board(fen_after)
    except ValueError as exc:
        raise ValueError(f"malformed FEN (fen_after) {fen_after!r}: {exc}") from exc
    if not claimed.is_valid():
        raise ValueError(f"illegal position (fen_after) {fen_after!r}")
    claimed_key = " ".join(claimed.fen().split()[:4])
    derived_key = " ".join(derived_after.fen().split()[:4])
    if claimed_key != derived_key:
        raise ValueError(
            f"fen_after {fen_after!r} does not match fen_before+move "
            f"(expected {derived_key})"
        )
    board_after = derived_after

    # --- 4. re-probe fen_before (never trust a flag) ----------------------
    probe_before = _probe_for_user(fen_before, user_is_side_to_move=True)
    if drill_is_winning is True and probe_before.outcome != "win":
        raise ValueError(
            f"win drill received a non-winning fen_before {fen_before!r} "
            f"(tablebase says {probe_before.outcome} for the user); the session "
            "must already have failed -- stale or wrong FEN from the client"
        )
    if drill_is_winning is False and probe_before.outcome == "loss":
        raise ValueError(
            f"draw drill received a lost fen_before {fen_before!r}; the session "
            "must already have failed -- stale or wrong FEN from the client"
        )

    # --- 5a. resolution the OPPONENT's reply may have produced ------------
    # The fifty-move clock always expires on the SECOND mover's halfmove --
    # with the user moving first (clock 0), that is the defender's reply,
    # so a clock-expired position arrives as the NEXT fen_before. Also the
    # structural case where the defender's own move self-resolves. Return
    # the terminal verdict here; the move/fen_after are irrelevant to it
    # (the move was still validated above so garbage can't ride through).
    verdict_before = _terminal_verdict(board_before, drill_is_winning)
    if verdict_before is not None:
        status, failure_category, resolution = verdict_before
        return EndgameMoveResult(
            status=status,
            failure_category=failure_category,
            resolution=resolution,
            outcome_before=probe_before.outcome,
            dtz_before=probe_before.dtz,
            rating_change=_rating_change(status, endgame_trainer_rating, topic_difficulty_rating),
        )

    # --- 6. resolution on the position after the user's move --------------
    verdict = _terminal_verdict(board_after, drill_is_winning)
    if verdict is not None:
        status, failure_category, resolution = verdict
        return EndgameMoveResult(
            status=status,
            failure_category=failure_category,
            resolution=resolution,
            outcome_before=probe_before.outcome,
            dtz_before=probe_before.dtz,
            rating_change=_rating_change(status, endgame_trainer_rating, topic_difficulty_rating),
        )

    # --- 7. transition classification (tablebase, user's perspective) -----
    try:
        probe_after = _probe_for_user(fen_after, user_is_side_to_move=False)
    except TablebaseUnavailableError:
        # With the Lichess fallback active this is rare (offline deploy,
        # API down/rate-limited): the only transition out of local coverage
        # on an unresolved position is a promotion (adds a piece -> 6 men).
        # A promotion from a tablebase win then ends the drill as a
        # degraded SOLVED (see EndgameResolution.PROMOTION); any other
        # coverage gap (e.g. a capture in a draw drill) is a genuine
        # install/availability gap and fails loudly.
        if probe_before.outcome == "win" and parsed.promotion is not None:
            return EndgameMoveResult(
                status=EndgameStatus.SOLVED,
                resolution=EndgameResolution.PROMOTION,
                outcome_before=probe_before.outcome,
                dtz_before=probe_before.dtz,
                rating_change=_rating_change(
                    EndgameStatus.SOLVED, endgame_trainer_rating, topic_difficulty_rating
                ),
            )
        raise

    if probe_before.outcome == "win":
        if probe_after.outcome == "win":
            status, failure_category = EndgameStatus.IN_PROGRESS, None
        elif probe_after.outcome == "draw":
            status, failure_category = (
                EndgameStatus.FAILED,
                EndgameFailureCategory.THREW_AWAY_WIN,
            )
        else:
            status, failure_category = (
                EndgameStatus.FAILED,
                EndgameFailureCategory.BLUNDERED_INTO_LOSS,
            )
    else:  # draw drill in progress
        if probe_after.outcome in ("draw", "win"):
            # "win" is theoretically impossible from the user's own move
            # (a tablebase draw cannot be promoted to a win by one move),
            # but it is not a failure either -- hold / keep going.
            status, failure_category = EndgameStatus.IN_PROGRESS, None
        else:
            status, failure_category = (
                EndgameStatus.FAILED,
                EndgameFailureCategory.LOST_THE_DRAW,
            )

    return EndgameMoveResult(
        status=status,
        failure_category=failure_category,
        outcome_before=probe_before.outcome,
        outcome_after=probe_after.outcome,
        dtz_before=probe_before.dtz,
        dtz_after=probe_after.dtz,
        rating_change=_rating_change(status, endgame_trainer_rating, topic_difficulty_rating),
    )


def evaluate_defender_reply(
    fen_after: str,
    *,
    drill_is_winning: Optional[bool],
    endgame_trainer_rating: Optional[int] = None,
    topic_difficulty_rating: Optional[int] = None,
) -> Optional[EndgameMoveResult]:
    """Adjudicate a generated defender move that ended the game on the board.

    The defender's move is never graded -- the user's move already was, as
    in_progress. But that move can itself end the game: the fifty-move clock
    expires on the SECOND mover's halfmove, and a draw drill's defender can
    stalemate or (rarely) leave insufficient material. The next user move may
    then not exist (stalemate) or never be played, so the routes call this
    right after generating the reply and replace the in_progress result when
    it returns a verdict. None means the game is still live.

    Threefold repetition is deliberately absent here: this board is built
    from a FEN alone and cannot see the drill's history. A repetition the
    defender's move creates is caught by the NEXT move's history-rebuilt
    fen_before (_terminal_verdict step 5a).
    """
    try:
        board = chess.Board(fen_after)
    except ValueError as exc:
        raise ValueError(
            f"malformed FEN (defender reply) {fen_after!r}: {exc}"
        ) from exc
    if not board.is_valid():
        raise ValueError(f"illegal position (defender reply) {fen_after!r}")

    verdict = _terminal_verdict(board, drill_is_winning)
    if verdict is None:
        return None
    status, failure_category, resolution = verdict
    return EndgameMoveResult(
        status=status,
        failure_category=failure_category,
        resolution=resolution,
        rating_change=_rating_change(
            status, endgame_trainer_rating, topic_difficulty_rating
        ),
    )


def attach_common_mistake(
    conn, result: EndgameMoveResult, topic_id: str
) -> EndgameMoveResult:
    """Enrich a FAILED result with its authored common-mistake explanation.

    Called by the route right after evaluate_endgame_move() -- the grader
    is pure and never touches the DB or the topic. No-op (returns `result`
    itself, no query) unless the result is failed with a failure category.
    Otherwise returns a COPY of the result carrying the explanation for
    (topic_id, failure_category) in `common_mistake`, or the copy with
    common_mistake=None when no content row exists yet (the normal case
    until a topic is authored -- content absence never raises).

    Raises ValueError for a non-UUID topic_id (programming error, same
    contract as endgame_library's selectors); a real DB failure propagates
    -- it is an infrastructure problem, not a content gap. See the module
    docstring's COMMON-MISTAKE CONTENT section.
    """
    if result.status != EndgameStatus.FAILED or result.failure_category is None:
        return result
    try:
        parsed_topic_id = UUID(str(topic_id))
    except ValueError as exc:
        raise ValueError(f"topic_id {topic_id!r} is not a valid UUID") from exc

    explanation = _lookup_common_mistake(
        conn, parsed_topic_id, result.failure_category
    )
    return result.model_copy(update={"common_mistake": explanation})


def _lookup_common_mistake(
    conn, topic_id: UUID, failure_category: EndgameFailureCategory
) -> Optional[str]:
    """The authored explanation for (topic, failure type), or None when the
    content has not been written yet. Single-row by the table's
    UNIQUE (topic_id, failure_type) constraint; the topic id is passed as
    text because psycopg2 does not adapt uuid.UUID objects out of the box
    (same note as endgame_library)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT explanation
            FROM endgame_common_mistakes
            WHERE topic_id = %s::uuid
              AND failure_type = %s
            """,
            (str(topic_id), failure_category.value),
        )
        row = cur.fetchone()
    return row["explanation"] if row else None


def _rating_change(
    status: EndgameStatus,
    endgame_trainer_rating: Optional[int],
    topic_difficulty_rating: Optional[int],
) -> Optional[int]:
    """Rating delta on terminal statuses, reusing the puzzles math
    (core.rating.calculate_rating_change). None for in_progress or when the
    caller did not supply both ratings."""
    if status not in (EndgameStatus.SOLVED, EndgameStatus.FAILED):
        return None
    if endgame_trainer_rating is None or topic_difficulty_rating is None:
        return None
    return calculate_rating_change(
        endgame_trainer_rating,
        topic_difficulty_rating,
        solved=(status == EndgameStatus.SOLVED),
    )
