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
