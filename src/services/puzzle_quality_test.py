"""
Test harness for services/puzzle_quality.py.

Pure board/PV analysis (no engine calls), so every case is a fixed FEN +
UCI line with the expected theme/acceptance asserted directly.

Covers the extractor rule: a position only counts as a puzzle when the
solver's last move in the solution line ends in checkmate, a promotion,
or a capture.
  1. build_solution_line: mate / material stop, forcing-only continuation,
     6-ply cap, quiet collapse (shared with routers/user_puzzles.py).
  2. solution_ending: mate / promotion / capture / None.
  3. Mate endings accepted; announced mates the line cannot deliver are
     mate_too_deep.
  4. Promotion endings accepted (even when the promoted piece is lost
     back: the rule is about the ending, not the net swing).
  5. Capture endings accepted as trap (clean trap set by the best move)
     else material.
  6. Quiet and forcing-only lines without a concrete ending are rejected
     as no_concrete_ending, whatever the margin.

Run:
    cd src && ../venv/bin/python services/puzzle_quality_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.puzzle_quality import (
    build_solution_line,
    classify_quality,
    decide,
    find_piece_trap,
    forcing_flags,
    material_balance,
    material_swing_cp,
    solution_ending,
)

import chess


def test_material_swing_capture():
    # Rook takes hanging bishop, king steps aside: +330 (bishop) for White.
    fen = "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1"
    swing = material_swing_cp(fen, ["a1a5", "e8d7"], 3)
    assert swing == 330, f"expected +330, got {swing}"
    # Opponent reply matters: only our first move scores the capture.
    one_ply = material_swing_cp(fen, ["a1a5"], 3)
    assert one_ply == 330, f"expected +330 after 1 ply, got {one_ply}"
    print("[PASS] material swing counts a capture after the reply")


def test_material_swing_promotion():
    # Pawn queens with check: +800 (queen minus pawn).
    fen = "8/2P5/8/8/8/1k6/8/4K3 w - - 0 1"
    swing = material_swing_cp(fen, ["c7c8q"], 3)
    assert swing == 800, f"expected +800, got {swing}"
    print("[PASS] material swing counts a promotion")


def test_material_swing_invalid():
    assert material_swing_cp(chess.STARTING_FEN, [], 3) is None
    assert material_swing_cp(chess.STARTING_FEN, ["e7e5"], 3) is None
    assert material_swing_cp("not-a-fen", ["e2e4"], 3) is None
    print("[PASS] material swing returns None on empty/illegal PV")


def test_forcing_flags():
    # Scholar's mate tactics: Qh5 is quiet, Qxf7# is check/capture.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3"
    flags = forcing_flags(fen, ["d1h5", "g8f6", "h5f7"])
    assert flags[0] is False, f"Qh5 is quiet, got {flags}"
    assert flags[2] is True, f"Qxf7# is check/capture, got {flags}"
    print("[PASS] forcing flags mark checks/captures per ply")


def test_solution_ending_mate():
    fen = "7k/8/5K2/8/8/8/6Q1/8 w - - 0 1"
    assert solution_ending(fen, "g2g7", ["g2g7"]) == "mate"
    scholar = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3"
    assert solution_ending(scholar, "d1h5", ["d1h5", "g8f6", "h5f7"]) == "mate"
    print("[PASS] mating lines end in mate")


def test_solution_ending_promotion():
    fen = "8/2P5/8/8/8/1k6/8/4K3 w - - 0 1"
    assert solution_ending(fen, "c7c8q", ["c7c8q"]) == "promotion"
    print("[PASS] promotion lines end in promotion")


def test_solution_ending_capture():
    fen = "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1"
    assert solution_ending(fen, "a1a5", ["a1a5", "e8d7"]) == "capture"
    print("[PASS] capture lines end in capture")


def test_solution_ending_none():
    assert solution_ending(
        chess.STARTING_FEN, "g1f3", ["g1f3", "d7d5"]
    ) is None
    # Forcing checks with no payoff: capped, but no concrete ending.
    ladder = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    assert solution_ending(
        ladder, "a1a8", ["a1a8", "e8d7", "a8a7", "d7e6"]
    ) is None
    print("[PASS] quiet and payoff-free lines have no ending")


def test_build_solution_line_cap():
    pv = ["a1a8", "e8d7", "a8a7", "d7e6", "a7a6", "e6f5", "a6a5", "f5g4"]
    line = build_solution_line(
        "4k3/8/8/8/8/8/8/R3K3 w - - 0 1", "a1a8", pv
    )
    assert line == pv[:6], line
    assert len(line[::2]) <= 3, "at most 3 player moves"
    print("[PASS] long forcing line capped at 6 plies")


def test_trap_detected_but_needs_ending():
    # Kc3-b3 traps the Na3: attacked once, undefended, 1 safe escape.
    fen = "8/1P6/B1kP2P1/5P2/8/n1K1p3/8/8 w - - 0 1"
    trap = find_piece_trap(fen, "c3b3")
    assert trap is not None, "expected a trap on a3"
    assert (trap.square, trap.piece, trap.value) == ("a3", "N", 320)
    assert trap.escapes <= 1
    assert trap.created_by_move is True
    quality = classify_quality(fen, "c3b3", ["c3b3"], None)
    assert "trap" in quality.themes, quality.themes
    # ...but the line ends quietly, so it is not a puzzle on its own.
    ending = solution_ending(fen, "c3b3", ["c3b3"])
    assert ending is None
    accept, _, reason = decide(quality, ending)
    assert not accept and reason == "no_concrete_ending", reason
    print("[PASS] trap without a concrete ending is rejected")


def test_capture_trap_accepted_as_trap():
    # Qxb2 wins the bishop AND traps the Nb3: capture ending + trap.
    fen = "2B5/8/1P1K4/8/8/1nPkP2p/1b6/Q7 w - - 0 1"
    pv = ["a1b2", "d3c4"]
    quality = classify_quality(fen, "a1b2", pv, None)
    assert "trap" in quality.themes, quality.themes
    ending = solution_ending(fen, "a1b2", pv)
    assert ending == "capture", ending
    accept, theme, _ = decide(quality, ending)
    assert accept and theme == "trap", theme
    print("[PASS] capture that also traps is accepted as trap")


def test_trap_already_hanging():
    # Nd5 is already attacked by c4 and undefended: h2-h3 creates nothing.
    trap = find_piece_trap(
        "4k3/8/8/3n4/2P5/8/PP3PPP/4K3 w - - 0 1", "h2h3"
    )
    assert trap is None, f"already-hanging piece is not a trap: {trap}"
    print("[PASS] already-hanging piece is not a trap")


def test_trap_too_many_escapes():
    # Na5 is attacked by b4 but keeps 4 safe squares: no trap.
    trap = find_piece_trap(
        "4k3/8/8/n7/8/8/1P4K1/8 w - - 0 1", "b2b4"
    )
    assert trap is None, f"piece with escapes is not a trap: {trap}"
    quality = classify_quality(
        "4k3/8/8/n7/8/8/1P4K1/8 w - - 0 1", "b2b4", ["b2b4"], None
    )
    assert "trap" not in quality.themes
    print("[PASS] piece with >1 safe square is not a trap")


def test_mate_accepted():
    fen = "7k/8/5K2/8/8/8/6Q1/8 w - - 0 1"
    quality = classify_quality(fen, "g2g7", ["g2g7"], 1)
    assert quality.mate_in == 1 and "mate" in quality.themes
    ending = solution_ending(fen, "g2g7", ["g2g7"])
    accept, theme, _ = decide(quality, ending)
    assert accept and theme == "mate", "mate needs no margin"
    print("[PASS] mate ending accepted as mate")


def test_mate_too_deep_rejected():
    quality = classify_quality(chess.STARTING_FEN, "e2e4", ["e2e4"], 7)
    ending = solution_ending(chess.STARTING_FEN, "e2e4", ["e2e4"])
    accept, _, reason = decide(quality, ending)
    assert not accept and reason == "mate_too_deep", reason
    print("[PASS] announced mate the line cannot deliver is rejected")


def test_capture_accepted_as_material():
    fen = "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1"
    pv = ["a1a5", "e8d7"]
    quality = classify_quality(fen, "a1a5", pv, None)
    ending = solution_ending(fen, "a1a5", pv)
    assert ending == "capture", ending
    accept, theme, _ = decide(quality, ending)
    assert accept and theme == "material"
    print("[PASS] capture ending accepted as material")


def test_promotion_accepted():
    fen = "8/2P5/8/8/8/1k6/8/4K3 w - - 0 1"
    quality = classify_quality(fen, "c7c8q", ["c7c8q"], None)
    assert quality.is_promotion and "promotion" in quality.themes
    ending = solution_ending(fen, "c7c8q", ["c7c8q"])
    accept, theme, _ = decide(quality, ending)
    assert accept and theme == "promotion"
    print("[PASS] promotion ending accepted as promotion")


def test_promotion_ending_accepts_despite_recapture():
    # Queen promotes but the rook takes it straight back: themes stay
    # quiet, yet the line ends in a promotion, so it counts.
    fen = "1r5k/P6p/8/8/8/8/8/K7 w - - 0 1"
    pv = ["a7a8q", "b8a8"]
    quality = classify_quality(fen, "a7a8q", pv, None)
    assert quality.is_promotion is True
    assert quality.themes == ["quiet"], quality.themes
    ending = solution_ending(fen, "a7a8q", pv)
    assert ending == "promotion", ending
    accept, theme, _ = decide(quality, ending)
    assert accept and theme == "promotion"
    print("[PASS] promotion ending counts even when recaptured")


def test_forcing_without_ending_rejected():
    # Checks all the way, comfortable margin, but no mate, promotion,
    # or capture at the end: not a puzzle.
    fen = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    pv = ["a1a8", "e8d7", "a8a7", "d7e6"]
    quality = classify_quality(fen, "a1a8", pv, None)
    assert quality.themes == ["forcing"], quality.themes
    ending = solution_ending(fen, "a1a8", pv)
    assert ending is None
    accept, _, reason = decide(quality, ending)
    assert not accept and reason == "no_concrete_ending", reason
    print("[PASS] forcing line without an ending is rejected")


def test_quiet_rejected():
    quality = classify_quality(
        chess.STARTING_FEN, "g1f3", ["g1f3", "d7d5"], None
    )
    assert quality.themes == ["quiet"]
    ending = solution_ending(chess.STARTING_FEN, "g1f3", ["g1f3", "d7d5"])
    accept, _, reason = decide(quality, ending)
    assert not accept and reason == "no_concrete_ending", reason
    print("[PASS] quiet positional line rejected")


def test_material_balance_sign():
    board = chess.Board("4k3/8/8/b7/8/8/8/R3K3 w - - 0 1")
    assert material_balance(board, chess.WHITE) == 500 - 330
    assert material_balance(board, chess.BLACK) == 330 - 500
    print("[PASS] material balance is signed from the given side")


if __name__ == "__main__":
    test_material_swing_capture()
    test_material_swing_promotion()
    test_material_swing_invalid()
    test_forcing_flags()
    test_solution_ending_mate()
    test_solution_ending_promotion()
    test_solution_ending_capture()
    test_solution_ending_none()
    test_build_solution_line_cap()
    test_trap_detected_but_needs_ending()
    test_capture_trap_accepted_as_trap()
    test_trap_already_hanging()
    test_trap_too_many_escapes()
    test_mate_accepted()
    test_mate_too_deep_rejected()
    test_capture_accepted_as_material()
    test_promotion_accepted()
    test_promotion_ending_accepts_despite_recapture()
    test_forcing_without_ending_rejected()
    test_quiet_rejected()
    test_material_balance_sign()
    print("All assertions passed.")
