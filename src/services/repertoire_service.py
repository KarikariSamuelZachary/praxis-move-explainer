"""Insert/upsert path for the Repertoire trainer.

This module owns the write side of `repertoire_positions`: given a
repertoire (which fixes the owner's color) and a linear UCI move
sequence, replay the moves on a python-chess board, normalize the
pre-ply FEN to its 4 canonical fields, and upsert one row per ply
where it was the repertoire owner's turn to move.

The upsert uses (repertoire_id, fen) as the unique conflict target
(see migration in src/core/migrations.py) with
`ON CONFLICT (repertoire_id, fen) DO UPDATE SET move = EXCLUDED.move,
updated_at = NOW()` — so re-adding a transposing line updates the
move choice at that position rather than erroring or silently
no-opping. That is what makes "two different move orders reaching the
same normalized FEN produce ONE row, not two" actually hold.

Pattern note (vs the rest of the codebase):
  * Raw psycopg2, no SQLAlchemy — same as `opponent_repertoire.py`
    and `routers/woodpecker.py`.
  * `conn` is taken as a parameter; the CALLER owns the transaction
    (commit/rollback). This mirrors `index_opponent_game` in
    `services/opponent_repertoire.py`; the router's `get_db()` dep
    supplies the conn and commits on success.
  * Schemas live in `schemas/repertoire_schemas.py` — same layout as
    `schemas/puzzle_schemas.py` / `schemas/review_schemas.py`.

Out of scope (separate task):
  * The HTTP router that calls `upsert_repertoire_positions`.
  * FSRS scheduling (the `due` / `stability` / `difficulty` / `state`
    columns are left at their migration defaults on insert).
"""
import logging
from dataclasses import dataclass
from typing import List, Sequence
from uuid import UUID

import chess
from psycopg2.extras import RealDictCursor

from schemas.repertoire_schemas import (
    Repertoire,
    RepertoireColor,
    RepertoirePosition,
)

log = logging.getLogger(__name__)


class IllegalRepertoireMoveError(ValueError):
    """Raised when a UCI move in the input sequence cannot be legally
    pushed onto the replay board. The message names the offending ply
    index and the move, so the caller can locate the bad input.
    """

    def __init__(self, ply_index: int, uci_move: str, reason: str):
        self.ply_index = ply_index
        self.uci_move = uci_move
        self.reason = reason
        super().__init__(
            f"Illegal UCI move {uci_move!r} at ply {ply_index}: {reason}"
        )


class RepertoireNotFoundError(LookupError):
    """The repertoire_id supplied does not match any row in repertoires."""


@dataclass(frozen=True)
class _PlannedPosition:
    """A single (normalized_fen, move) pair the upsert will write to
    repertoire_positions. Pure helper output — does not touch the DB.

    `fen` is the position BEFORE the move is played, normalized to the
    first 4 FEN fields (board, side-to-move, castling rights, en
    passant square). The halfmove clock and fullmove number are
    stripped by the planner, never stored.
    """
    fen: str
    move: str  # UCI string ("e2e4", "e7e8q"); NOT SAN.


def _normalize_fen(fen: str) -> str:
    """Return the 4-field normalized FEN (board, side-to-move, castling
    rights, en passant square). Halfmove clock and fullmove number
    are stripped.

    Matches the convention used for `opponent_repertoire_moves.position_key`
    (see `opponent_repertoire._position_key`), so the same position
    reached via different transposition orders collapses to the same
    row in `repertoire_positions`.
    """
    return " ".join(fen.split()[:4])


def _replay_and_plan(
    uci_moves: Sequence[str],
    repertoire_color: RepertoireColor,
    start_fen: str = chess.STARTING_FEN,
) -> List[_PlannedPosition]:
    """Replay a linear UCI move sequence on a fresh board, emitting one
    `_PlannedPosition` per ply whose pre-ply position has the
    repertoire owner's color to move. Opponent-turn plies are *still
    pushed* onto the board so the replay can continue (this is what
    lets transpositions through opponent replies resolve), but no
    plan is emitted for them.

    Raises `IllegalRepertoireMoveError` on the first illegal move,
    identifying the offending ply index. No DB state is touched by
    this helper — it is pure — so a raise here cannot leave partial
    rows behind. The caller's `upsert_repertoire_positions` defers
    ALL DB writes until after this returns.
    """
    if not uci_moves:
        return []

    # python-chess accepts a 4-6 field FEN; the standard starting FEN
    # we default to has all 6 fields. Normalizing here would lose
    # clock/fullmove info needed for legal push validation, so we do
    # NOT normalize the start_fen — only the per-ply snapshot below.
    board = chess.Board(start_fen)
    owner_is_white = repertoire_color == "white"
    plans: List[_PlannedPosition] = []

    for ply_index, uci_move in enumerate(uci_moves):
        # Snapshot the position BEFORE the ply is pushed. This is the
        # row we'd write: (repertoire_id, fen=pre_ply, move=uci_move).
        pre_ply_fen = _normalize_fen(board.fen())
        side_to_move_is_white = board.turn == chess.WHITE

        # `board.parse_uci` parses the UCI string AND validates the
        # move against the current position in one call, raising a
        # ValueError subclass (IllegalMoveError for illegal-on-board
        # moves, InvalidMoveError for malformed UCI strings). We use
        # it instead of `chess.Move.from_uci` + `board.push()` because
        # the latter path raises a bare `AssertionError` from push()
        # for not-pseudo-legal moves -- which would bypass this
        # handler. parse_uci keeps every input-validation failure
        # under the ValueError umbrella we can catch uniformly.
        try:
            move = board.parse_uci(uci_move)
        except ValueError as exc:
            raise IllegalRepertoireMoveError(
                ply_index=ply_index,
                uci_move=uci_move,
                reason=str(exc) or "illegal move",
            ) from exc
        # parse_uci has validated legality; push cannot raise here.
        board.push(move)

        # Side-to-move consistency: only write a row when it was the
        # repertoire owner's turn at the pre-ply position. The
        # opponent's reply plies are replayed (so the board keeps
        # advancing) but skipped from the plan.
        if side_to_move_is_white == owner_is_white:
            plans.append(_PlannedPosition(fen=pre_ply_fen, move=uci_move))

    return plans


def upsert_repertoire_positions(
    conn,
    *,
    repertoire_id,
    uci_moves: Sequence[str],
    start_fen: str = chess.STARTING_FEN,
) -> List[RepertoirePosition]:
    """Replay `uci_moves` from `start_fen`, then upsert a
    `repertoire_positions` row for each ply where it was the
    repertoire owner's turn to move.

    Opponent-turn plies are replayed (so a position reachable only
    through a specific opponent reply can still be transposed into),
    but no row is written from them.

    The conflict target is the migration's
    `UNIQUE (repertoire_id, fen)` constraint. `DO UPDATE SET
    move = EXCLUDED.move, updated_at = NOW()` means a later write
    that transposes into the same position overrides the prior move
    choice instead of raising or silently no-opping — which is what
    guarantees two different move orders reaching the same normalized
    FEN land as ONE row, not two.

    Args:
        conn: open psycopg2 connection. The caller owns the
            transaction; this function only issues INSERTs and does
            not commit. On exception the caller's `get_db()` rollback
            (or equivalent) drops any uncommitted writes.
        repertoire_id: id of an existing `repertoires` row. May be
            passed as str, UUID, or anything str()-able.
        uci_moves: linear sequence of UCI moves to replay
            (e.g. ["e2e4", "e7e5", "g1f3"]). Empty is allowed and
            returns [] without writing any rows.
        start_fen: FEN to start replay from. Defaults to
            `chess.STARTING_FEN`. python-chess accepts 4-6 fields.

    Returns:
        The post-upsert row state (as `RepertoirePosition` schema
        objects) for every position WRITTEN BY THIS CALL, in ply
        order. Rows that were only updated (already existed from a
        prior call) are still returned — their fresh post-update
        snapshot is what's in the RETURNING set.

    Raises:
        RepertoireNotFoundError:
            `repertoire_id` does not match any row in `repertoires`.
            Raised before any INSERT runs.
        IllegalRepertoireMoveError:
            A UCI move is illegal. The message names the offending
            ply index. The full replay completes BEFORE the first
            INSERT, so a mid-sequence illegal move cannot leave
            partial rows behind.
    """
    rid = str(repertoire_id)

    # Resolve the repertoire's color from the DB. Single source of
    # truth — callers can't accidentally pass a wrong color and
    # pollute the rows. The SELECT runs in the caller's transaction
    # (no separate commit).
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, user_id, name, color, created_at, updated_at
            FROM repertoires
            WHERE id = %s
            """,
            (rid,),
        )
        row = cur.fetchone()

    if row is None:
        raise RepertoireNotFoundError(f"repertoire not found: {rid!r}")
    repertoire = Repertoire(**row)

    # Replay fully and plan every write BEFORE touching the DB. This
    # is what makes "illegal move -> no partial rows" actually hold:
    # if _replay_and_plan raises, we have executed zero INSERTs.
    plans = _replay_and_plan(
        uci_moves=uci_moves,
        repertoire_color=repertoire.color,
        start_fen=start_fen,
    )

    if not plans:
        # Empty input, all-opponent sequence, or a sequence that
        # never hits an owner-turn ply: nothing to write. We return
        # an empty list explicitly rather than an open cursor.
        return []

    written: List[RepertoirePosition] = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for plan in plans:
            # One execute per planned position so each row's RETURNING
            # snapshot is unambiguous (executem would not let us pair
            # rows to inputs deterministically across the conflict
            # split between INSERT and UPDATE).
            #
            # Only the (repertoire_id, fen, move) trio is supplied;
            # every FSRS-shaped column defaults to its migration
            # default (due=NOW(), state='Learning', reps=0, lapses=0,
            # stability/difficulty/step/last_review NULL). Updating
            # move + updated_at on conflict keeps the FSRS scheduling
            # state from a prior learning session intact — that's a
            # feature, not a bug: re-importing a line shouldn't reset
            # the user's progress at that position.
            cur.execute(
                """
                INSERT INTO repertoire_positions (
                    repertoire_id,
                    fen,
                    move
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (repertoire_id, fen) DO UPDATE
                    SET move       = EXCLUDED.move,
                        updated_at = NOW()
                RETURNING
                    id,
                    repertoire_id,
                    fen,
                    move,
                    due,
                    stability,
                    difficulty,
                    state,
                    step,
                    reps,
                    lapses,
                    last_review,
                    created_at,
                    updated_at
                """,
                (rid, plan.fen, plan.move),
            )
            returned = cur.fetchone()
            if returned is not None:
                written.append(RepertoirePosition(**returned))

    return written