"""
Test harness for serving and grading in routers/user_puzzles.py.

_solution_line serves the quiz (opens loud, ends decisive, no trailing
opponent reply). _accepted_lines adds near-equivalent alternates, and
_validate_user_line grades a full attempt against any accepted line.

Run:
    cd src && ../venv/bin/python routers/user_puzzles_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

from routers.user_puzzles import (
    _accepted_lines,
    _solution_line,
    _validate_user_line,
)


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


CAPTURE_FEN = "4k3/8/8/b7/8/8/4K3/R6b w - - 0 1"
MATE_FEN = "4k3/8/8/3Q4/2B5/8/8/4K3 w - - 0 1"


def _grade_entry(fen, lines):
    return {"fen_before": fen, "lines": lines}


def test_accepted_lines_best_only():
    entry = _entry(CAPTURE_FEN, "a1a5", ["a1a5", "e8d7"])
    assert _accepted_lines(entry) == [["a1a5"]]
    print("[PASS] no alts serves just the best quiz")


def test_accepted_lines_adds_alt():
    entry = _entry(CAPTURE_FEN, "a1a5", ["a1a5", "e8d7"])
    entry["alt_pvs"] = ["a1h1 e8d7"]
    assert _accepted_lines(entry) == [["a1a5"], ["a1h1"]]
    print("[PASS] qualifying alt PV is rebuilt into a second quiz")


def test_accepted_lines_skips_junk_alt():
    entry = _entry(CAPTURE_FEN, "a1a5", ["a1a5", "e8d7"])
    entry["alt_pvs"] = ["a1a5 e8d7", "e2d3 e8d7", "zz", ""]
    assert _accepted_lines(entry) == [["a1a5"]]
    print("[PASS] dupe, quiet, and malformed alts are skipped")


def test_grade_best_solves():
    entry = {"fen_before": CAPTURE_FEN}
    lines = [["a1a5", "e8d7"], ["a1h1", "e8d7"]]
    solved, expected = _validate_user_line(entry, lines, ["a1a5"])
    assert solved and expected is None
    print("[PASS] best move solves")


def test_grade_alt_solves():
    entry = {"fen_before": CAPTURE_FEN}
    lines = [["a1a5", "e8d7"], ["a1h1", "e8d7"]]
    solved, expected = _validate_user_line(entry, lines, ["a1h1"])
    assert solved and expected is None
    print("[PASS] near-equivalent alternate solves")


def test_grade_wrong_fails_with_best_expected():
    entry = {"fen_before": CAPTURE_FEN}
    lines = [["a1a5", "e8d7"], ["a1h1", "e8d7"]]
    solved, expected = _validate_user_line(entry, lines, ["e2d3"])
    assert not solved and expected == "Rxa5", expected
    print("[PASS] wrong move fails naming the best move")


def test_grade_shared_prefix_names_best():
    entry = {"fen_before": MATE_FEN}
    lines = [["d5d7", "e8f8", "d7f7"], ["d5d7", "e8f8", "d7e6"]]
    solved, expected = _validate_user_line(entry, lines, ["d5d7", "d7g7"])
    assert not solved and expected == "Qf7#", expected
    print("[PASS] divergence on a shared prefix names the best reply")


def test_grade_shared_prefix_switch_solves():
    entry = {"fen_before": MATE_FEN}
    lines = [["d5d7", "e8f8", "d7f7"], ["d5d7", "e8f8", "d7e6"]]
    solved, expected = _validate_user_line(entry, lines, ["d5d7", "d7e6"])
    assert solved and expected is None
    print("[PASS] switching to an alt mid-line solves")


def test_grade_wrong_second_move_fails():
    entry = {"fen_before": MATE_FEN}
    lines = [["d5d7", "e8f8", "d7f7"], ["d5d6"]]
    solved, expected = _validate_user_line(entry, lines, ["d5d7", "d7g7"])
    assert not solved and expected == "Qf7#", expected
    print("[PASS] wrong second move fails naming the best reply")


def test_grade_incomplete_is_not_wrong():
    entry = {"fen_before": MATE_FEN}
    lines = [["d5d7", "e8f8", "d7f7"]]
    solved, expected = _validate_user_line(entry, lines, ["d5d7"])
    assert not solved and expected is None
    print("[PASS] proper prefix is incomplete, not wrong")


def test_grade_too_many_raises():
    entry = {"fen_before": CAPTURE_FEN}
    lines = [["a1a5", "e8d7"]]
    try:
        _validate_user_line(entry, lines, ["a1a5", "e2d3"])
    except HTTPException as exc:
        assert exc.status_code == 400 and "Too many" in str(exc.detail)
    else:
        raise AssertionError("expected 400")
    print("[PASS] moves past a completed line raise 400")


def test_grade_malformed_raises():
    entry = {"fen_before": CAPTURE_FEN}
    try:
        _validate_user_line(entry, [["a1a5", "e8d7"]], ["zz"])
    except HTTPException as exc:
        assert exc.status_code == 400 and "Malformed" in str(exc.detail)
    else:
        raise AssertionError("expected 400")
    print("[PASS] malformed UCI raises 400")


def test_grade_illegal_raises():
    entry = {"fen_before": CAPTURE_FEN}
    try:
        _validate_user_line(entry, [["a1a5", "e8d7"]], ["b1c3"])
    except HTTPException as exc:
        assert exc.status_code == 400 and "Illegal" in str(exc.detail)
    else:
        raise AssertionError("expected 400")
    print("[PASS] illegal move raises 400")


def test_grade_unlisted_mate_solves():
    # Second mate in one, not stored in any line: mate is mate.
    entry = {"fen_before": "8/6Q1/8/5K1k/6rp/8/8/8 w - - 0 1"}
    solved, expected = _validate_user_line(entry, [["g7h8"]], ["g7h7"])
    assert solved and expected is None
    print("[PASS] unlisted mating move solves")


def test_grade_stalemate_does_not_solve():
    # Qd6 stalemates: not a mate, so it fails naming the best move.
    entry = {"fen_before": MATE_FEN}
    solved, expected = _validate_user_line(
        entry, [["d5d7", "e8f8", "d7f7"]], ["d5d6"]
    )
    assert not solved and expected == "Qd7+", expected
    print("[PASS] stalemate delivery does not solve")


def test_grade_trailing_after_wrong_raises():
    entry = {"fen_before": CAPTURE_FEN}
    try:
        _validate_user_line(entry, [["a1a5", "e8d7"]], ["e2d3", "a1a5"])
    except HTTPException as exc:
        assert exc.status_code == 400 and "first incorrect" in str(exc.detail)
    else:
        raise AssertionError("expected 400")
    print("[PASS] moves after the first incorrect move raise 400")


if __name__ == "__main__":
    test_mate_in_one()
    test_winning_capture_serves_no_reply()
    test_promotion_serves_no_reply()
    test_loud_first_mating_line_kept()
    test_long_line_never_ends_on_reply()
    test_pv_mismatch_falls_back()
    test_illegal_best_move()
    test_missing_best_move()
    test_accepted_lines_best_only()
    test_accepted_lines_adds_alt()
    test_accepted_lines_skips_junk_alt()
    test_grade_best_solves()
    test_grade_alt_solves()
    test_grade_wrong_fails_with_best_expected()
    test_grade_shared_prefix_names_best()
    test_grade_shared_prefix_switch_solves()
    test_grade_wrong_second_move_fails()
    test_grade_incomplete_is_not_wrong()
    test_grade_too_many_raises()
    test_grade_malformed_raises()
    test_grade_illegal_raises()
    test_grade_trailing_after_wrong_raises()
    test_grade_unlisted_mate_solves()
    test_grade_stalemate_does_not_solve()
    print("All assertions passed.")
