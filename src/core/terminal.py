"""
Terminal-position synthesis for the classify path (batch and live alike).

Position-based: the same board plus move history yields the same result in
the review loop and in the future sandbox endpoint, which is required for
mainline-replay label parity.

Terminal (no engine call, synthetic evaluation):
  checkmate, stalemate, insufficient material, fivefold repetition,
  75-move rule.

Claimable but NOT terminal (the engine still evaluates on the replayed
history): threefold repetition and the 50-move rule. `draw_claimable()`
exists so the UI can show "draw claimable" without blocking moves.
"""
from dataclasses import dataclass
from typing import Optional

import chess

from schemas.models import Evaluation

CHECKMATE_CP = 10_000.0


@dataclass(frozen=True)
class TerminalState:
    kind: str
    evaluation: Evaluation


def _draw_evaluation() -> Evaluation:
    return Evaluation(
        score_cp=0.0,
        best_move_uci="",
        best_move_san="(none)",
        mate=None,
        second_best_cp=None,
        principal_variation_uci=[],
    )


def terminal_state(board: chess.Board) -> Optional[TerminalState]:
    """Synthetic evaluation for a terminal position, or None when play goes on.

    Checkmate scores from the side-to-move POV (the mated side): -10000 with
    mate=0, matching what Stockfish reports (Mate(-0)) so the response's
    eval_cp/eval_mate sign convention stays consistent with engine results.
    """
    if board.is_checkmate():
        return TerminalState(
            "checkmate",
            Evaluation(
                score_cp=-CHECKMATE_CP,
                best_move_uci="",
                best_move_san="(none)",
                mate=0,
                second_best_cp=None,
                principal_variation_uci=[],
            ),
        )
    if board.is_stalemate():
        return TerminalState("stalemate", _draw_evaluation())
    if board.is_insufficient_material():
        return TerminalState("insufficient_material", _draw_evaluation())
    if board.is_fivefold_repetition():
        return TerminalState("fivefold_repetition", _draw_evaluation())
    if board.is_seventyfive_moves():
        return TerminalState("seventyfive_moves", _draw_evaluation())
    return None


def draw_claimable(board: chess.Board) -> bool:
    """True when threefold repetition or the 50-move rule can be claimed.

    Not terminal: the sandbox keeps allowing moves, and the engine evaluates
    the position on the replayed history.
    """
    return board.is_repetition(3) or board.is_fifty_moves()
