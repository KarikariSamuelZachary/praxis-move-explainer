"""
Opponent reply generation for the Endgame Trainer.

A sourced drill's stored puzzle line is short: it ends where Lichess
considered the tactic solved, not necessarily at checkmate/draw. Once the
user plays past that line (or leaves it), the defender needs a move or the
drill can never reach the resolution the state machine grades against.

AUTHORITY PRINCIPLE (stated so it is not reinvented)
====================================================
Tablebase is the authority wherever it can reach; Stockfish is ONLY a
fallback move-generator for positions too large for tablebase coverage.
Grading (services/endgame_session.evaluate_endgame_move) already trusts the
tablebase verdict over engine eval; the opponent's reply is held to the
same standard, because a suboptimal defender lets the user "solve" a drill
more easily than the real technique demands. Concretely:

  * If probe_tablebase() can answer the position (local Syzygy <=5 men, or
    the Lichess 6-7-man fallback), the reply is chosen by tablebase value -
    never by Stockfish.
  * Stockfish runs only when probe_tablebase() raises
    TablebaseUnavailableError for the position (or for one of its child
    positions -- see below), i.e. beyond the tablebase's reach.

PER-MOVE DTZ: WHY THE MOVE IS FOUND BY PROBING CHILDREN
=======================================================
services/tablebase.py answers POSITION-level questions only:
probe_tablebase(fen) returns the outcome/dtz of the position itself (and
its internal Lichess parser deliberately discards the API's per-move
`moves` array). It does NOT expose a per-move DTZ. The only way to find the
best move with the existing module, reused as-is, is to enumerate every
legal move, probe the resulting child FEN, and rank the children. That is
what _tablebase_candidates() does. The child probe is from the CHILD side
to move (the opponent of the mover), so `_INVERT` flips it back to the
mover's perspective; the child's DTZ is kept from the child side to move's
perspective, which is exactly the quantity the policy below needs.

SELECTION POLICY (DTZ-optimal defense)
======================================
Root outcome is the tablebase verdict for the side to move (the defender in
a drill):
  * loss  -> longest resistance: among moves that still lose, maximize the
    child's DTZ (the winner's plies to the next zeroing move). This is
    Syzygy's own minimization/maximization: the losing side delays zeroing.
  * draw  -> keep the draw: any drawing move is game-theoretically equal,
    so the tie-break is deterministic (lowest UCI). Moves that end the game
    on the board (stalemate / insufficient material) are used only when no
    live drawing move exists: a terminal reply would leave the stateless
    grader with no position to grade on the next call.
  * win   -> fastest conversion (this side is the winner only when the
    user has already failed; still supported for a coherent service):
    immediate mate first, otherwise minimize the magnitude of the child's
    DTZ.
Ties are broken by lowest UCI order so the service is deterministic. A DTM
(distance-to-mate) table is not installed and is not pretended: DTZ is the
metric, exactly as requested ("best DTZ"). Cursed wins/blessed losses are
outside the seeded clean content and are treated as win/loss by
probe_tablebase's mapping; callers should not expect 50-move-rule-perfect
play in those rare positions.

If probing ANY child raises TablebaseUnavailableError (e.g. a defender
promotion pushes past coverage), the whole selection falls back to
Stockfish rather than ranking an incomplete candidate set.

STOCKFISH FALLBACK
==================
Reuses the dedicated endgame singleton (engines.stockfish_engine): one
full-strength, process-lifetime engine, never a spawn per call. It is a
separate process from review's and sparring's for failure-domain and
latency isolation (see the singleton block's rationale). The call is capped
at ENDGAME_REPLY_DEPTH (default 18) and ENDGAME_REPLY_TIME_SECONDS
(default 1.0): tablebase is the real authority in this feature, so the
fallback only has to be a strong practical defender at bounded latency;
depth 18 matches the deployment's review strength, and the 1.0s cap keeps
one reply from stalling the request. Engine errors reset the singleton so
the next request starts fresh instead of retrying into a poisoned process.

TERMINAL POSITIONS
==================
generate_opponent_reply() raises TerminalPositionError (a ValueError
subclass) when the input is already checkmate/stalemate/insufficient
material/fifty-move/seventy-five-move. A drill never calls it there: the
move endpoint only generates after evaluate_endgame_move returned
IN_PROGRESS, which by contract means the position is live. The error exists
so a standalone or future caller cannot silently get nonsense.

STORED-LINE DETECTION
=====================
stored_line_reply() is the trigger helper for the /move endpoint. Given the
drill's stored FEN, the source puzzle's full move list and the client's
fen_after, it replays the line (moves[1:], since moves[0] is the setup move
already baked into the stored FEN) and returns the next stored move ONLY
when fen_after is still on the line and that next move is the opponent's
(an odd number of line plies has been played). It returns None -- i.e.
"generator territory" -- when the line is exhausted, the position has left
the line, or the next stored move would be the user's. Detection is by
POSITION (first 4 FEN fields), not by ply counters or move matching, so it
survives counter-only differences and transpositions.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Literal, Optional, Sequence

import chess
from pydantic import BaseModel

from engines.stockfish_engine import (
    get_endgame_stockfish,
    reset_endgame_stockfish,
)
from services.tablebase import TablebaseUnavailableError, probe_tablebase

log = logging.getLogger(__name__)

_INVERT = {"win": "loss", "draw": "draw", "loss": "win"}

# Fallback search budget. Depth 18 is the deployment's full-strength review
# depth; 1.0s is a hard latency ceiling for one reply in a live drill.
DEFAULT_REPLY_DEPTH = 18
DEFAULT_REPLY_TIME_SECONDS = 1.0


class TerminalPositionError(ValueError):
    """The input position is already over; there is no reply to generate."""


class OpponentReplyUnavailableError(RuntimeError):
    """Neither tablebase nor Stockfish could produce a reply. The caller
    should treat this as a retryable infrastructure gap, never as a verdict."""


class OpponentReply(BaseModel):
    """One generated defender move, applied and ready to hand to the client."""

    move_uci: str
    move_san: str
    fen_after: str
    source: Literal["tablebase", "stockfish"]
    # Tablebase verdict for the input position (side to move), when the
    # tablebase answered. None on the Stockfish fallback.
    outcome: Optional[str] = None
    dtz: Optional[int] = None


@dataclass(frozen=True)
class _Candidate:
    move: chess.Move
    # The MOVER's outcome after playing `move`.
    outcome: str
    # DTZ from the child side to move's (the opponent's) perspective.
    child_dtz: Optional[int]
    # The child ends the game on the board (mate/stalemate/insufficient).
    ends_game: bool = False
    is_mate: bool = False
    # The move resets the 50-move clock (capture or pawn move). DTZ's
    # zeroing move; only these can be optimal when root DTZ == 1.
    is_zeroing: bool = False


def _reply_depth() -> int:
    try:
        return int(os.getenv("ENDGAME_REPLY_DEPTH", str(DEFAULT_REPLY_DEPTH)))
    except ValueError:
        return DEFAULT_REPLY_DEPTH


def _reply_time_seconds() -> float:
    try:
        return float(
            os.getenv("ENDGAME_REPLY_TIME_SECONDS", str(DEFAULT_REPLY_TIME_SECONDS))
        )
    except ValueError:
        return DEFAULT_REPLY_TIME_SECONDS


def _position_key(fen: str) -> str:
    """First 4 FEN fields (board/turn/castling/ep): the same normalization
    repertoire_positions and endgame_session's fen_after cross-check use."""
    return " ".join(fen.split()[:4])


def _over_reason(board: chess.Board) -> Optional[str]:
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "insufficient material"
    if board.is_seventyfive_moves():
        return "seventy-five-move rule"
    if board.is_fivefold_repetition():
        return "fivefold repetition"
    if board.is_fifty_moves():
        return "fifty-move rule"
    return None


def _parse_board(fen: str) -> chess.Board:
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"malformed FEN {fen!r}: {exc}") from exc
    if not board.is_valid():
        raise ValueError(f"illegal position {fen!r}")
    return board


def _build_reply(
    board: chess.Board,
    move: chess.Move,
    *,
    source: Literal["tablebase", "stockfish"],
    outcome: Optional[str] = None,
    dtz: Optional[int] = None,
) -> OpponentReply:
    child = board.copy(stack=False)
    child.push(move)
    return OpponentReply(
        move_uci=move.uci(),
        move_san=board.san(move),
        fen_after=child.fen(),
        source=source,
        outcome=outcome,
        dtz=dtz,
    )


def generate_opponent_reply(fen: str) -> OpponentReply:
    """Generate the defender's best move in `fen` (tablebase-first).

    Raises ValueError for malformed/illegal FENs, TerminalPositionError when
    the position is already over, and OpponentReplyUnavailableError when the
    tablebase cannot reach it AND Stockfish cannot produce a move.
    """
    board = _parse_board(fen)
    reason = _over_reason(board)
    if reason is not None:
        raise TerminalPositionError(
            f"position {fen!r} is already over ({reason}); no opponent reply exists"
        )

    try:
        return _reply_from_tablebase(board)
    except TablebaseUnavailableError as exc:
        log.info(
            "endgame reply: tablebase cannot reach %s (%s) - using Stockfish",
            fen,
            exc,
        )
        return _reply_from_stockfish(board)


def _tablebase_candidates(board: chess.Board) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        zeroing = board.is_capture(move) or move.promotion is not None
        child = board.copy(stack=False)
        child.push(move)
        if child.is_checkmate():
            candidates.append(
                _Candidate(
                    move,
                    "win",
                    None,
                    ends_game=True,
                    is_mate=True,
                    is_zeroing=zeroing,
                )
            )
            continue
        if child.is_stalemate() or child.is_insufficient_material():
            candidates.append(
                _Candidate(move, "draw", None, ends_game=True, is_zeroing=zeroing)
            )
            continue
        # Position-level probe only; see the module docstring. A failure here
        # (beyond coverage) propagates so the caller can fall back wholesale.
        child_result = probe_tablebase(child.fen())
        candidates.append(
            _Candidate(
                move,
                _INVERT[child_result.outcome],
                child_result.dtz,
                is_zeroing=zeroing,
            )
        )
    return candidates


def _pick_candidate(
    candidates: Sequence[_Candidate],
    root_outcome: str,
    root_dtz: Optional[int] = None,
) -> _Candidate:
    viable = [c for c in candidates if c.outcome == root_outcome]
    if not viable:
        raise OpponentReplyUnavailableError(
            f"tablebase reported {root_outcome!r} but no legal move preserves it"
        )

    if root_outcome == "win":
        mates = [c for c in viable if c.is_mate]
        if mates:
            return mates[0]
        with_dtz = [c for c in viable if c.child_dtz is not None]
        pool = with_dtz or viable
        # DTZ-optimal conversion, phase-aware:
        #   * root DTZ == 1 -> the optimal move is a ZEROING move (capture/
        #     pawn push); prefer one that preserves the win. |child_dtz|
        #     alone is wrong here: after a zeroing move the child starts a
        #     NEW phase, so a non-zeroing waiting move can show a much
        #     smaller |dtz| (KRvKR: waiting rook move child dtz -3 vs the
        #     immediate capture's KRvK child dtz -18) while postponing the
        #     conversion and letting the drill repeat.
        #   * root DTZ > 1 -> no winning zeroing move exists; the optimal
        #     child (loser's DTZ = -(root_dtz - 1)) is the one closest to
        #     the phase's zeroing, i.e. the LEAST negative, which is
        #     min |child_dtz|.
        if root_dtz == 1:
            zeroing = [c for c in pool if c.is_zeroing]
            if zeroing:
                pool = zeroing
        return min(
            pool,
            key=lambda c: (
                abs(c.child_dtz) if c.child_dtz is not None else 0,
                c.move.uci(),
            ),
        )

    if root_outcome == "loss":
        with_dtz = [c for c in viable if c.child_dtz is not None]
        pool = with_dtz or viable
        # max child DTZ == the winner is furthest from a zeroing move.
        return min(
            pool,
            key=lambda c: (
                -(c.child_dtz) if c.child_dtz is not None else 0,
                c.move.uci(),
            ),
        )

    live = [c for c in viable if not c.ends_game]
    pool = live or viable
    return min(pool, key=lambda c: c.move.uci())


def _reply_from_tablebase(board: chess.Board) -> OpponentReply:
    root = probe_tablebase(board.fen())
    candidates = _tablebase_candidates(board)
    chosen = _pick_candidate(candidates, root.outcome, root.dtz)
    return _build_reply(
        board,
        chosen.move,
        source="tablebase",
        outcome=root.outcome,
        dtz=root.dtz,
    )


def _reply_from_stockfish(board: chess.Board) -> OpponentReply:
    depth = _reply_depth()
    time_limit = _reply_time_seconds()
    try:
        engine = get_endgame_stockfish(depth=depth)
        evaluation = engine.evaluate(
            board, depth_limit=depth, time_limit=time_limit
        )
    except Exception as exc:  # noqa: BLE001 -- reset then report typed
        reset_endgame_stockfish()
        raise OpponentReplyUnavailableError(
            f"Stockfish could not generate an endgame reply: {exc}"
        ) from exc

    move_uci = evaluation.best_move_uci
    if not move_uci:
        raise OpponentReplyUnavailableError("Stockfish returned no reply move")
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as exc:
        raise OpponentReplyUnavailableError(
            f"Stockfish returned an invalid move {move_uci!r}"
        ) from exc
    if move not in board.legal_moves:
        raise OpponentReplyUnavailableError(
            f"Stockfish returned an illegal move {move_uci!r} for {board.fen()!r}"
        )
    return _build_reply(board, move, source="stockfish")


def stored_line_reply(
    stored_fen: str,
    puzzle_moves: Optional[Sequence[str]],
    fen_after: str,
) -> Optional[str]:
    """The stored line's next opponent move, or None when the drill has left
    the line (line exhausted, deviation, or the next stored move is the
    user's). See the module docstring's STORED-LINE DETECTION section."""
    if not puzzle_moves or len(puzzle_moves) < 2:
        return None
    line = list(puzzle_moves[1:])
    try:
        board = chess.Board(stored_fen)
    except ValueError:
        return None
    target = _position_key(fen_after)

    for index, uci in enumerate(line):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return None
        if move not in board.legal_moves:
            return None
        board.push(move)
        if _position_key(board.fen()) != target:
            continue
        # `index + 1` line plies have been played; the next line move is the
        # opponent's exactly when that count is odd and still within range.
        next_index = index + 1
        if next_index >= len(line) or next_index % 2 == 0:
            return None
        candidate_uci = line[next_index]
        try:
            candidate = chess.Move.from_uci(candidate_uci)
        except ValueError:
            return None
        if candidate not in board.legal_moves:
            return None
        return candidate_uci
    return None


def opponent_reply_for_move(
    stored_fen: str,
    source_moves: Optional[Sequence[str]],
    fen_after: str,
) -> Optional[OpponentReply]:
    """The defender move to answer a user move with, or None while the
    client is still inside the stored line (it already has the next stored
    move from the drill payload, so the server stays quiet).

    Thin composition of stored_line_reply() and generate_opponent_reply(),
    shared by the trainer route (POST /api/endgames/move) and the review
    route (POST /api/endgames/woodpecker/attempts) so a full-resolution
    replay behaves identically in both. Raises the same
    TerminalPositionError / OpponentReplyUnavailableError the generator
    does; the routes map those to 500 / 503.
    """
    if stored_line_reply(stored_fen, source_moves, fen_after) is not None:
        return None
    return generate_opponent_reply(fen_after)
