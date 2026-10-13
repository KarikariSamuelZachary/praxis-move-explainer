"""
Test harness for the quiz line served in routers/user_puzzles.py.

The served quiz opens loud and ends decisive with no trailing opponent
reply: mate, promotion, and winning captures serve just the payoff, while
loud-first mating sequences keep their middle moves.

Run:
    cd src && ../venv/bin/python routers/user_puzzles_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers.user_puzzles import _solution_line


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
    print("[PASS] mate in 1 serves the mating move")


def test_winning_capture_serves_no_reply():
    # The reply verifies the win; it is never part of the quiz.
    entry = _entry(
        "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1",
        "a1a5",
        ["a1a5", "e8d7", "a5a7"],
    )
    assert _solution_line(entry) == ["a1a5"]
    print("[PASS] winning capture serves only the capture")


def test_promotion_serves_no_reply():
    entry = _entry(
        "8/2P5/8/8/8/1k6/8/4K3 w - - 0 1",
        "c7c8q",
        ["c7c8q", "b3c4"],
    )
    assert _solution_line(entry) == ["c7c8q"]
    print("[PASS] promotion serves only the promotion")


def test_loud_first_mating_line_kept():
    # Qd7+ Kf8 Qf7#: the middle moves are the puzzle, mate ends it.
    entry = _entry(
        "4k3/8/8/3Q4/2B5/8/8/4K3 w - - 0 1",
        "d5d7",
        ["d5d7", "e8f8", "d7f7"],
    )
    assert _solution_line(entry) == ["d5d7", "e8f8", "d7f7"]
    print("[PASS] loud mating sequence keeps its middle moves")


def test_long_line_never_ends_on_reply():
    pv = ["a1a8", "e8d7", "a8a7", "d7e6", "a7a6", "e6f5", "a6a5", "f5g4"]
    entry = _entry("4k3/8/8/8/8/8/8/R3K3 w - - 0 1", "a1a8", pv)
    line = _solution_line(entry)
    assert len(line) % 2 == 1, "quiz always ends on our move"
    assert len(line) <= 6
    print("[PASS] served line ends on our move within the cap")


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


def test_missing_best_move():
    entry = {
        "fen_before": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        "best_move_uci": "",
        "best_pv_uci": "",
    }
    assert _solution_line(entry) == []
    print("[PASS] missing best move serves nothing")


if __name__ == "__main__":
    test_mate_in_one()
    test_winning_capture_serves_no_reply()
    test_promotion_serves_no_reply()
    test_loud_first_mating_line_kept()
    test_long_line_never_ends_on_reply()
    test_pv_mismatch_falls_back()
    test_illegal_best_move()
    test_missing_best_move()
    print("All assertions passed.")
