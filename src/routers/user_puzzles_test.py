"""
Test harness for the multi-move solution line in routers/user_puzzles.py.

_solution_line turns the stored engine PV (best_pv_uci) into the quiz:
  - stop at mate or once the solver is up >= a pawn after the opponent's
    reply (even ply), so a temporary capture cannot end the puzzle;
  - otherwise keep the line only while it stays forcing
    (checks/captures, services/puzzle_quality.forcing_flags), capped at
    6 plies = at most 3 player moves;
  - quiet continuations collapse to the single best move.

Run:
    cd src && ../venv/bin/python routers/user_puzzles_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers.user_puzzles import _MAX_SOLUTION_PLIES, _solution_line


def _entry(fen_before, best_move_uci, pv):
    return {
        "fen_before": fen_before,
        "best_move_uci": best_move_uci,
        "best_pv_uci": " ".join(pv),
    }


def test_mate_in_one():
    entry = _entry(
        "7k/8/5K2/8/8/8/6Q1/8 w - - 0 1", "g2g7", ["g2g7"]
    )
    assert _solution_line(entry) == ["g2g7"]
    print("[PASS] mate in 1 is a single-move solution")


def test_mate_in_two_keeps_reply():
    # Scholar's mate: Qh5, ... Nf6??, Qxf7#.
    entry = _entry(
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3",
        "d1h5",
        ["d1h5", "g8f6", "h5f7"],
    )
    assert _solution_line(entry) == ["d1h5", "g8f6", "h5f7"]
    print("[PASS] mate in 2 keeps the opponent reply + finish")


def test_material_win_stops_after_reply():
    # Rook takes the hanging bishop; king steps aside: +330, done.
    entry = _entry(
        "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1",
        "a1a5",
        ["a1a5", "e8d7", "a5a7"],
    )
    assert _solution_line(entry) == ["a1a5", "e8d7"]
    print("[PASS] material win stops once up after the reply")


def test_quiet_collapses_to_best_move():
    entry = _entry(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "g1f3",
        ["g1f3", "d7d5", "f3e5"],
    )
    assert _solution_line(entry) == ["g1f3"]
    print("[PASS] quiet engine continuation collapses to one move")


def test_long_forcing_line_capped():
    # Rook checks chase the lone king: forcing all the way, no mate or
    # material stop, so the line is capped at 6 plies (3 player moves).
    pv = ["a1a8", "e8d7", "a8a7", "d7e6", "a7a6", "e6f5", "a6a5", "f5g4"]
    entry = _entry("4k3/8/8/8/8/8/8/R3K3 w - - 0 1", "a1a8", pv)
    line = _solution_line(entry)
    assert line == pv[:_MAX_SOLUTION_PLIES], line
    assert len(line[::2]) <= 3, "at most 3 player moves"
    print("[PASS] long forcing line capped at 6 plies / 3 player moves")


def test_pv_mismatch_falls_back():
    entry = _entry(
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "d7d5",
        ["e7e5", "g1f3"],
    )
    assert _solution_line(entry) == ["d7d5"]
    print("[PASS] PV not starting with the best move falls back")


def test_illegal_best_move():
    entry = _entry(
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "e2e4",
        ["e2e4"],
    )
    assert _solution_line(entry) == ["e2e4"]
    print("[PASS] illegal best move degrades to the raw UCI")


if __name__ == "__main__":
    test_mate_in_one()
    test_mate_in_two_keeps_reply()
    test_material_win_stops_after_reply()
    test_quiet_collapses_to_best_move()
    test_long_forcing_line_capped()
    test_pv_mismatch_falls_back()
    test_illegal_best_move()
    print("All assertions passed.")
