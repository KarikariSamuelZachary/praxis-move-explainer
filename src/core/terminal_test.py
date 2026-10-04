"""
Terminal synthesis tests (core/terminal.py).

Verified:
  A. The five terminal kinds synthesize the right evaluation.
  B. Threefold repetition and the 50-move rule are claimable, not terminal.
  C. The 75-move rule outranks the 50-move claim once it applies.

Run with: cd src && ../venv/bin/python core/terminal_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from core.terminal import CHECKMATE_CP, draw_claimable, terminal_state

CHECKMATE = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
STALEMATE = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
INSUFFICIENT = "8/8/8/8/8/8/8/K6k w - - 0 1"
FIFTY = "8/8/8/8/8/8/R7/K6k w - - 100 60"
SEVENTYFIVE = "8/8/8/8/8/8/R7/K6k w - - 150 80"


def repetition_board() -> chess.Board:
    board = chess.Board()
    for uci in ("g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8"):
        board.push_uci(uci)
    return board


def test_terminal_kinds():
    mate = terminal_state(chess.Board(CHECKMATE))
    assert mate is not None and mate.kind == "checkmate"
    assert mate.evaluation.mate == 0
    assert mate.evaluation.score_cp == -CHECKMATE_CP

    stale = terminal_state(chess.Board(STALEMATE))
    assert stale is not None and stale.kind == "stalemate"
    assert stale.evaluation.score_cp == 0.0 and stale.evaluation.mate is None

    insufficient = terminal_state(chess.Board(INSUFFICIENT))
    assert insufficient is not None and insufficient.kind == "insufficient_material"

    # The starting position occurs once at ply 0 and once per 4-ply cycle:
    # 16 plies reach the fifth occurrence.
    fivefold_board = chess.Board()
    for _ in range(4):
        for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
            fivefold_board.push_uci(uci)
    assert fivefold_board.is_fivefold_repetition() is True
    fivefold = terminal_state(fivefold_board)
    assert fivefold is not None and fivefold.kind == "fivefold_repetition"

    seventyfive = terminal_state(chess.Board(SEVENTYFIVE))
    assert seventyfive is not None and seventyfive.kind == "seventyfive_moves"
    print("  [PASS] checkmate/stalemate/insufficient/fivefold/75-move synthesize")


def test_claimable_is_not_terminal():
    assert draw_claimable(repetition_board()) is True
    assert terminal_state(repetition_board()) is None

    fifty_board = chess.Board(FIFTY)
    assert draw_claimable(fifty_board) is True
    assert terminal_state(fifty_board) is None
    print("  [PASS] threefold and 50-move are claimable, not terminal")


def test_seventyfive_outranks_fifty():
    board = chess.Board(SEVENTYFIVE)
    assert board.is_fifty_moves() is True
    state = terminal_state(board)
    assert state is not None and state.kind == "seventyfive_moves"
    print("  [PASS] 75-move terminal outranks the 50-move claim")


def run() -> int:
    print("=== Running terminal synthesis tests ===")
    tests = [
        test_terminal_kinds,
        test_claimable_is_not_terminal,
        test_seventyfive_outranks_fifty,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"  [FAIL] {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
    print(f"  {len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
