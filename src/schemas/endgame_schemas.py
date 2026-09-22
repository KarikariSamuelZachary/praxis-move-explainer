"""
Wire schemas for the Endgame Trainer MVP endpoints (routers/endgames.py).

EndgamePositionResponse deliberately mirrors the SIX keys of
schemas/puzzle_schemas.PuzzleResponse (id, fen, moves, rating, themes,
gameUrl) so a future endgame page can consume it with the same shape the
Puzzles page already expects, then adds the endgame-specific fields the
drill screen needs (is_winning, topic metadata).

One deliberate semantic difference from PuzzleResponse: `fen` is the drill
START position -- already after the opponent's setup move -- and
`moves[0]` (when present) is the SOLVER's first move, not an opponent setup
move. A client that reuses Puzzles' fetchPuzzleBatch() normalization
(playing moves[0] as the opponent's setup) would be wrong for endgames;
the endgame page must treat the response as "the user is to move in fen".

EndgameMoveResponse is flat by design: it carries the grader's
EndgameMoveResult fields (status, failure_category, resolution,
outcome_*, dtz_*, common_mistake) plus the route-layer additions --
`rating` (old/new/change on a resolved drill) and `review_capture` (the
FAILED path's payload persisted into the endgame Woodpecker queue; see
the field's docstring).

The EndgameWoodpecker* models at the bottom are the separate review
queue's wire types (routers/endgame_woodpecker.py). The queue entry nests
the same EndgamePositionResponse payload GET /next serves, so a future
review page reuses the trainer's drill-screen shape unchanged.
"""
from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from services.endgame_session import (
    EndgameFailureCategory,
    EndgameResolution,
    EndgameStatus,
)


class EndgamePositionResponse(BaseModel):
    """The next rating-matched drill position.

    The first six fields mirror PuzzleResponse exactly -- `id`, `fen`,
    `moves`, `rating`, `themes`, `gameUrl` -- so the shared board/page
    plumbing can consume either mode. The remaining fields are the
    endgame-specific context a drill screen needs.
    """

    id: str
    fen: str
    moves: List[str] = Field(default_factory=list)
    rating: int
    themes: List[str] = Field(default_factory=list)
    gameUrl: Optional[str] = None
    # TRUE = tablebase win for the side to move (convert it);
    # FALSE = tablebase draw (hold it).
    is_winning: bool
    topic_id: str
    topic_name: str
    topic_category: str
    # Provenance for sourced rows; NULL for curated rows.
    source_puzzle_id: Optional[str] = None


class EndgameMoveRequest(BaseModel):
    """One user move in a drill. `move` accepts UCI or SAN; the grader
    verifies legality and cross-checks fen_after against fen_before+move."""

    position_id: UUID
    fen_before: str
    move: str
    fen_after: str
    # How many hints THIS drill attempt has revealed (client-owned: the
    # backend keeps no session state). Read only on a resolving move, where
    # it changes the scored outcome: a hint-assisted SOLVED move is neutral
    # (no rating write in either direction) in the rated loop, and practice
    # carries it for display only. A hint-assisted FAILED move is unchanged
    # -- the failure stands. Negative values are rejected with a 400.
    hints_used: int = 0
    # True when this attempt REPLAYS a drill whose result was already
    # recorded (the panel's "Retry"). The move is graded exactly like a
    # first attempt -- the verdict drives the UI -- but resolution writes
    # nothing: no rating change and no review capture. Retry is practice.
    retry: bool = False


class EndgameHintRequest(BaseModel):
    """Ask for the single best move in the position on the board.

    `fen` is the live drill position with the USER to move; the route
    rejects the defender's turn, which would otherwise be answered with the
    defender's best move. Nothing is submitted and nothing is written: the
    hint is advisory, and the user still plays the move through the
    surface's own grading route.
    """

    position_id: UUID
    fen: str


class EndgameHintResponse(BaseModel):
    """The one theoretically correct move for the side to move.

    Same selection policy as the defender generator (draw-preserving /
    longest-resistance / fastest-mate -- see services/endgame_reply.py),
    applied to the user's side of the board. `source` is "tablebase"
    whenever the local files or the Lichess fallback can reach the position,
    "stockfish" only for the beyond-coverage fallback.

    Read-only by construction: the route has no write path, so revealing a
    hint cannot change a recorded outcome. The surfaces report `hints_used`
    alongside their own moves instead, which is where the scoring
    consequences live.
    """

    position_id: str
    move_uci: str
    move_san: str
    fen_after: str
    source: Literal["tablebase", "stockfish"]


class EndgameRatingUpdate(BaseModel):
    """The endgame_trainer_rating write that accompanies a resolved drill
    (solved/failed). Puzzles splits this into POST /puzzles/rating; the
    endgame endpoint folds it in because the grader needs the rating to
    compute the delta in the first place, and one call keeps the drill
    transition and its rating write in a single transaction."""

    old_rating: int
    new_rating: int
    change: int


class EndgameReviewCapture(BaseModel):
    """FAILED-drill payload prepared for the future review queue.

    The Woodpecker integration is a later step, so nothing is queued yet.
    This block is shaped to match what today's puzzle-failure capture
    actually persists (woodpecker_entries: puzzle_id / theme /
    source_reason / added_at; POST /api/woodpecker/entries with
    source_reason="wrong_answer") while carrying the endgame-specific
    context the queue will need so the response does not have to change
    when the queue table lands:

      * position_id      -> the endgame_positions row (the queue key;
                            mirrors woodpecker_entries.puzzle_id)
      * theme            -> the topic category (mirrors .theme)
      * source_reason    -> "wrong_answer" (mirrors the puzzle fail path)
      * failed_at        -> the timestamp (.added_at's analogue)
      * failure_category -> WHY it failed (the endgame-specific addition)
      * fen / outcomes   -> enough to re-render the drill without
                            re-reading the position row.
    """

    position_id: str
    source_puzzle_id: Optional[str] = None
    topic_id: str
    topic_name: str
    theme: str
    fen: str
    is_winning: bool
    failure_category: EndgameFailureCategory
    resolution: Optional[EndgameResolution] = None
    outcome_before: Optional[str] = None
    outcome_after: Optional[str] = None
    source_reason: Literal["wrong_answer"] = "wrong_answer"
    failed_at: datetime


class EndgameOpponentReply(BaseModel):
    """A generated defender move, applied, so the client can play on.

    Present only on an IN_PROGRESS reply whose position had left the stored
    puzzle line (line exhausted or the user deviated): within the stored
    line the client keeps playing the line returned by GET /next, and the
    server does not generate. `source` is "tablebase" whenever the
    tablebase could reach the position; "stockfish" is the beyond-coverage
    fallback (see services/endgame_reply.py).
    """

    move_uci: str
    move_san: str
    fen_after: str
    source: Literal["tablebase", "stockfish"]


class EndgameMoveResponse(BaseModel):
    """Graded transition plus route-layer side effects.

    Fields up to common_mistake mirror services.endgame_session.
    EndgameMoveResult one-for-one. rating is present only on solved/failed
    (an in_progress move never writes the rating). review_capture is
    present only on failed. opponent_reply is present only on in_progress
    when the drill ran past its stored puzzle line and the server generated
    the defender's move.
    """

    position_id: str
    status: EndgameStatus
    failure_category: Optional[EndgameFailureCategory] = None
    resolution: Optional[EndgameResolution] = None
    outcome_before: Optional[str] = None
    outcome_after: Optional[str] = None
    dtz_before: Optional[int] = None
    dtz_after: Optional[int] = None
    common_mistake: Optional[str] = None
    # Echoed from the request so the resolved panel (and an attempt log) can
    # distinguish an unaided solve from a hint-assisted one.
    hints_used: int = 0
    rating: Optional[EndgameRatingUpdate] = None
    review_capture: Optional[EndgameReviewCapture] = None
    opponent_reply: Optional[EndgameOpponentReply] = None


class EndgameWoodpeckerQueueEntry(BaseModel):
    """One due card in the endgame review queue.

    The FSRS/entry columns mirror the puzzle queue's woodpecker_entries row
    one-for-one (state is the INTEGER FSRS enum, same as that table), so a
    client that already renders a puzzle queue card has the same fields.
    `position` carries the full drill payload in the exact shape GET
    /api/endgames/next returns, so the review screen can reuse the trainer's
    drill rendering (and its opponent_reply handling) untouched.
    """

    id: str
    user_id: str
    position_id: str
    theme: str
    added_at: datetime
    mastered_at: Optional[datetime] = None
    is_mastered: bool
    source_reason: Optional[str] = None
    due: datetime
    state: int
    step: Optional[int] = None
    stability: Optional[float] = None
    difficulty: Optional[float] = None
    reps: int
    lapses: int
    last_review: Optional[datetime] = None
    position: EndgamePositionResponse


class EndgameWoodpeckerCountResponse(BaseModel):
    """The endgame tab's badge count: due-now, unmastered cards only.

    Deliberately the exact predicate GET /queue uses (and the same one the
    puzzle badge derives from), so "there is something to do" means the
    same thing on both tabs.
    """

    due_count: int


class EndgameWoodpeckerAttemptRequest(BaseModel):
    """One user move of a full-resolution review replay.

    Mirrors EndgameMoveRequest (the trainer's per-move body) plus the queue
    entry being reviewed and the time for the whole replay. The move is
    graded server-side exactly like a trainer move; the FSRS write happens
    only on the resolving move, so intermediate moves carry no scheduling
    result. `time_taken_ms` is read only on resolution (the client
    accumulates it across the replay, like a puzzle solve timer).
    """

    entry_id: UUID
    fen_before: str
    move: str
    fen_after: str
    time_taken_ms: int
    # Hints revealed during THIS replay (client-owned, like the trainer's
    # hints_used). On resolution a hint-assisted pass is scheduled as
    # NOT-CLEAN: FSRS receives the failure rating (Again) instead of Good,
    # so the card cannot graduate and comes back soon -- exactly how a real
    # failed replay is already handled. See routers/endgame_woodpecker.py.
    hints_used: int = 0
    # True when this replay RETRIES a card whose outcome was already
    # recorded (the panel's "Retry"): the move is graded for the verdict,
    # but resolution writes nothing -- no FSRS transition and no second
    # attempt row. Retry is practice.
    retry: bool = False


class EndgameWoodpeckerAttemptResponse(BaseModel):
    """One graded review move plus the FSRS write on resolution.

    Fields up to opponent_reply mirror EndgameMoveResponse's grader block
    (there is no `rating`: a review never touches
    users.endgame_trainer_rating -- FSRS is the review's outcome).
    `opponent_reply` appears on in_progress exactly like the trainer's,
    which is what makes a past-the-line replay reachable to actual mate.
    `attempt` and `scheduling` are present only when the replay resolved
    (solved/failed); `scheduling` is the same block POST
    /api/woodpecker/attempts returns for a puzzle review.
    """

    entry_id: str
    status: EndgameStatus
    failure_category: Optional[EndgameFailureCategory] = None
    resolution: Optional[EndgameResolution] = None
    outcome_before: Optional[str] = None
    outcome_after: Optional[str] = None
    dtz_before: Optional[int] = None
    dtz_after: Optional[int] = None
    common_mistake: Optional[str] = None
    # Echoed from the request: on a resolved replay the panel needs it to
    # say the pass was hint-assisted (and the scheduling above shows the
    # consequence -- an assisted pass is never scheduled as a clean solve).
    hints_used: int = 0
    opponent_reply: Optional[EndgameOpponentReply] = None
    # The endgame_woodpecker_attempts row written on resolution.
    attempt: Optional[dict] = None
    # FSRS transition block, same keys as the puzzle attempts endpoint.
    scheduling: Optional[dict] = None


class EndgamePracticeCategory(BaseModel):
    """One practice-mode picker row: a category with sourced positions.

    Mirrors services.endgame_library.CategorySummary. Only categories that
    actually have sourced positions appear (three CHECK values are unseeded
    today), the count is the sourced pool only, and draws from that
    category come from the same pool -- so picker count == drawable
    positions.
    """

    category: str
    position_count: int


class EndgamePlayoutReplyRequest(BaseModel):
    """One continuation step of a FAILED drill's playout.

    `fen_after` is where the user's last move (or the grader's failing move)
    left the board, i.e. the position the defender must answer. Deliberately
    no move is submitted: the verdict is already final, so the server is only
    asked for the defender's reply.
    """

    position_id: UUID
    fen_after: str


class EndgamePlayoutReplyResponse(BaseModel):
    """The defender's reply for a settled drill's playout, or None.

    None means either "the request is still inside the stored line" (the
    client applies the stored move itself, exactly like a graded replay) or
    "the position is already over" (the playout reached a real ending). The
    client distinguishes the two from its own game state, and stops asking
    once its game is over -- so the server maps an already-over position to
    None instead of treating it as an internal error.

    Nothing in this payload can change a recorded outcome: the route has no
    write path (no grading, no rating, no Woodpecker capture).
    """

    opponent_reply: Optional[EndgameOpponentReply] = None


class EndgamePlayoutFinishRequest(BaseModel):
    """Fast-forward a settled drill's continuation from the position it is at.

    `fen` is the board the user is looking at (mid-playout or immediately
    after the failure). No move is submitted: the route plays the tablebase-
    optimal line out server-side, or reports the verdict when no cheap
    concrete line exists.
    """

    position_id: UUID
    fen: str


class EndgamePlayoutFinishResponse(BaseModel):
    """The fast-forwarded result of a settled drill.

    Two shapes, both final:
      * a concrete ending -- `fen`/`ending`/`plies` carry a real terminal
        position reached by playing the same generator for both sides (only
        attempted for locally-covered material, where it is ~0.3s; decisive
        lines measured 13-25 plies, drawn ones 18-20). `line` is the whole
        sequence that got there, in UCI order, so the client can STEP it
        move by move with no further requests;
      * a verdict only -- `fen`/`ending` are None, `line` is empty and
        `outcome` is the tablebase result, used when the position is beyond
        the local files (6-7 men fall back to the Lichess API, ~3-13s PER
        PLY, so a line-play there is not a "fast-forward" and there is no
        line to step).

    `line` is the ordered moves of the fast-forward, UCI (the same format
    GET /next's `moves` and opponent_reply.move_uci use, which the board
    already applies via uciToMove). It is non-empty exactly when the
    concrete shape is returned AND the request position was not already
    terminal: the client replays it against the request FEN, so
    line.length == plies and applying every move reproduces `fen`.

    `outcome` is from the USER's colour (the side to move in the drill's
    stored FEN), so the panel's copy never has to reason about whose turn it
    currently is. None only when the request position was already terminal.

    Read-only: this route never grades, never writes a rating and never
    captures into the review queue.
    """

    outcome: Optional[Literal["win", "draw", "loss"]] = None
    fen: Optional[str] = None
    ending: Optional[
        Literal[
            "checkmate",
            "stalemate",
            "insufficient_material",
            "fifty_move_rule",
            "seventy_five_move_rule",
            "fivefold_repetition",
        ]
    ] = None
    plies: int = 0
    # The step-through source of truth: empty on the verdict path and on an
    # already-terminal request position.
    line: List[str] = Field(default_factory=list)
