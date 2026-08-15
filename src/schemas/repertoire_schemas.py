from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel

# Mirrors the CHECK (color IN ('white', 'black')) constraint on
# repertoires.color at the DB layer.
RepertoireColor = Literal["white", "black"]


class Repertoire(BaseModel):
    id: UUID
    user_id: str
    name: str
    color: RepertoireColor
    created_at: datetime
    updated_at: datetime


class RepertoireTrainingSession(BaseModel):
    # Row shape for repertoire_training_sessions. Mirrors the columns
    # created by the migration in src/core/migrations.py. This is a
    # session-level concept (no equivalent in woodpecker, which logs
    # per-attempt rows only); `mode` distinguishes a full re-train pass
    # ('train') from a spaced-review pass ('review'), and
    # `completed_at` is NULL while a session is in-progress/abandoned.
    id: UUID
    repertoire_id: UUID
    mode: Literal["review", "train"]
    positions_total: int
    positions_correct: int
    started_at: datetime
    completed_at: Optional[datetime] = None


class RepertoireSummary(BaseModel):
    # List-item shape returned by GET /api/repertoires. NOT the plain
    # Repertoire row shape: it bundles the repertoire's own fields with
    # the most recent completed training session's `completed_at`
    # (last_trained_at), the count of completed sessions
    # (times_trained), and the latest session's score as a percentage
    # (last_score_percent). A repertoire that has never had a completed
    # session appears with last_trained_at=null, times_trained=0, and
    # last_score_percent=null — it is NOT excluded from the list.
    id: UUID
    name: str
    color: RepertoireColor
    created_at: datetime
    updated_at: datetime
    last_trained_at: Optional[datetime] = None
    times_trained: int = 0
    last_score_percent: Optional[float] = None


class RepertoirePosition(BaseModel):
    # Row shape for repertoire_positions. Mirrors the columns created by
    # the migration in src/core/migrations.py; FSRS-style scheduling fields
    # are stored raw (no FSRS logic in this layer — see core/fsrs.py for the
    # card reconstruction helpers).
    id: UUID
    repertoire_id: UUID
    # Normalized to the first 4 FEN fields only (board, side-to-move,
    # castling rights, en passant square). Halfmove clock and fullmove
    # number are stripped by the writer before INSERT.
    fen: str
    # UCI format (e.g. "e2e4", "e7e8q"); NOT SAN.
    move: str
    due: datetime
    stability: Optional[float] = None
    difficulty: Optional[float] = None
    state: str
    step: Optional[int] = None
    reps: int
    lapses: int
    last_review: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# --- Gap analysis schemas --------------------------------------------
# These are NOT row shapes from the DB; they are derived records produced
# by services/repertoire_gaps.py's find_repertoire_gaps(). See that module
# for how the fields are computed. The three schemas form one report:
# RepertoireGapReport { gaps, unchecked_positions }.

class RepertoireGap(BaseModel):
    """A single opponent reply the user has no PREPARED RESPONSE to.

    `parent_*` locate the user's existing position that the opponent's
    reply transposes away from; `opponent_move_*` and `frequency_percent`
    describe the reply the gap-finder noticed was unprepared; and
    `resulting_fen` is the 4-field normalized FEN of the position the
    user would need to add a move to.

    Semantics worth nailing down (so this doesn't get quietly read
    downstream as something broader than it is):

      * "Gap" means the user has not PICKED A RESPONSE — i.e. there
        is no `repertoire_positions` row at `resulting_fen`. It is
        NOT a claim about whether the user has ever PLAYED a game
        reaching that position. The coverage set is built from rows
        the user has deliberately authored, so a gap is specifically
        an "un-prepared user-turn position," not an "unseen
        position."

      * Coverage is move-quality-blind: a row exists at
        `resulting_fen` regardless of whether the stored move there
        is statistically strong. "Gap" specifically means "no row
        exists," not "the row that exists is weak"; a separate
        quality-of-prepared-response check would be a different
        feature with a different output schema.

    Type asymmetry vs RepertoirePosition: there is no `id` here (a
    gap has no database row — by definition), and `resulting_fen` is
    normalized to 4 fields by the gap-finder so it can be compared
    against the stored fens.
    """
    parent_position_id: UUID
    parent_fen: str
    opponent_move_uci: str
    opponent_move_san: str
    frequency_percent: float
    resulting_fen: str


class UncheckedPosition(BaseModel):
    """A repertoire_positions row that the gap-finder SKIPPED rather
    than blanked on. Two failure modes hit this:

      * Lichess Explorer API was unreachable or returned a non-2xx HTTP
        status for this position's post-user-move FEN; the rest of the
        repertoire was still analyzed.
      * A stored (fen, move) was unpushable — the move is illegal on
        its fen. This shouldn't happen (upsert validates via
        board.parse_uci) but if a row is somehow stale/corrupt, the
        report still completes for the rest.

    Either way the client can list these to surface "couldn't check
    N positions" rather than letting the gaps quietly under-report.
    """
    position_id: UUID
    fen: str
    reason: str


class RepertoireGapReport(BaseModel):
    """The full response envelope returned by GET /gaps and
    find_repertoire_gaps(). `gaps` is the list of unprepared opponent
    replies, ordered by parent position (storage order) then by
    Explorer rank within each position. `unchecked_positions` is the
    list of positions the gap-finder skipped — empty on a fully
    successful run.
    """
    gaps: List[RepertoireGap] = []
    unchecked_positions: List[UncheckedPosition] = []