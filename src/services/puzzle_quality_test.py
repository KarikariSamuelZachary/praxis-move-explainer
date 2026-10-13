"""
Test harness for services/puzzle_quality.py.

Pure board/PV analysis (no engine calls), so every case is a fixed FEN +
UCI line with the expected theme/acceptance asserted directly.

Covers the extractor rule: the quiz must open loud (best move gives
check, captures, mates, or promotes) and end decisive (mate, promotion,
or advantage-confirmed capture). Replies are verification only and are
never served, so every quiz ends on the solver's move.
  1. immediate_ending: mate / promotion / capture / None, including the
     guards (losing capture, missing or illegal reply, illegal best).
  2. build_quiz_line: truncation at the first decisive solver move (no
     trailing reply), loud-first flag, forcing continuation, cap.
  3. decide: loud + decisive accepted with mate/promotion/trap/material
     themes; quiet openers fall out as quiet_first_move even when the PV
     finds a tactic later; payoff-free lines as no_concrete_ending.

Run:
    cd src && ../venv/bin/python services/puzzle_quality_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.puzzle_quality import (
    build_quiz_line,
    classify_quality,
    decide,
    find_piece_trap,
    forcing_flags,
    immediate_ending,
    material_balance,
    material_swing_cp,
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


def test_immediate_mate():
    fen = "7k/8/5K2/8/8/8/6Q1/8 w - - 0 1"
    assert immediate_ending(fen, "g2g7", ["g2g7"]) == "mate"
    print("[PASS] best move mating at once ends in mate")


def test_immediate_promotion():
    fen = "8/2P5/8/8/8/1k6/8/4K3 w - - 0 1"
    assert immediate_ending(fen, "c7c8q", ["c7c8q"]) == "promotion"
    print("[PASS] best move promoting at once ends in promotion")


def test_immediate_winning_capture():
    fen = "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1"
    assert immediate_ending(fen, "a1a5", ["a1a5", "e8d7"]) == "capture"
    print("[PASS] best move capturing to advantage ends in capture")


def test_immediate_losing_capture_rejected():
    # Nxe5 wins a pawn but Nxe5... Nxe5 loses the knight: net -220.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 2"
    assert immediate_ending(fen, "f3e5", ["f3e5", "c6e5"]) is None
    print("[PASS] capture recaptured for a loss has no ending")


def test_immediate_capture_needs_reply():
    # No stored reply, so the advantage cannot be confirmed.
    fen = "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1"
    assert immediate_ending(fen, "a1a5", ["a1a5"]) is None
    # An illegal reply cannot confirm it either.
    assert immediate_ending(fen, "a1a5", ["a1a5", "e8e5"]) is None
    print("[PASS] capture without a legal reply has no ending")


def test_immediate_quiet_has_no_ending():
    assert immediate_ending(
        chess.STARTING_FEN, "g1f3", ["g1f3", "d7d5"]
    ) is None
    print("[PASS] quiet best moves have no immediate ending")


def test_immediate_illegal_best():
    fen = "7k/8/5K2/8/8/8/6Q1/8 w - - 0 1"
    assert immediate_ending(fen, "e2e4", ["e2e4"]) is None
    assert immediate_ending(fen, "", []) is None
    assert immediate_ending("not-a-fen", "g2g7", ["g2g7"]) is None
    print("[PASS] illegal best moves have no ending")


def test_quiz_truncates_at_decisive_capture():
    # The verifying reply is never part of the quiz.
    fen = "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1"
    line, ending, loud = build_quiz_line(fen, "a1a5", ["a1a5", "e8d7", "a5a7"])
    assert line == ["a1a5"], line
    assert ending == "capture" and loud is True
    print("[PASS] quiz ends on the decisive capture, reply dropped")


def test_quiz_loud_first_multi_into_mate():
    # Qd7+ Kf8 Qf7#: checks all the way, middle moves are the puzzle.
    fen = "4k3/8/8/3Q4/2B5/8/8/4K3 w - - 0 1"
    pv = ["d5d7", "e8f8", "d7f7"]
    line, ending, loud = build_quiz_line(fen, "d5d7", pv)
    assert line == pv, line
    assert ending == "mate" and loud is True
    quality = classify_quality(fen, "d5d7", pv, 2)
    accept, theme, _ = decide(quality, ending, loud)
    assert accept and theme == "mate", theme
    print("[PASS] loud opener into mate keeps the whole line")


def test_quiz_quiet_first_mate_rejected():
    # Scholar's: the mate arrives, but the opener is a quiet move, so the
    # tactic belongs to a later position, not this quiz.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3"
    pv = ["d1h5", "g8f6", "h5f7"]
    line, ending, loud = build_quiz_line(fen, "d1h5", pv)
    assert line == pv and ending == "mate" and loud is False
    quality = classify_quality(fen, "d1h5", pv, 2)
    accept, _, reason = decide(quality, ending, loud)
    assert not accept and reason == "quiet_first_move", reason
    print("[PASS] quiet opener rejected even with mate to follow")


def test_quiz_no_trailing_reply():
    # Forcing checks with no payoff: trimmed to end on our move, then out.
    fen = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    pv = ["a1a8", "e8d7", "a8a7", "d7e6"]
    line, ending, loud = build_quiz_line(fen, "a1a8", pv)
    assert len(line) % 2 == 1, "quiz always ends on our move"
    assert ending is None and loud is True
    quality = classify_quality(fen, "a1a8", pv, None)
    accept, _, reason = decide(quality, ending, loud)
    assert not accept and reason == "no_concrete_ending", reason
    print("[PASS] payoff-free line trimmed and rejected")


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
    # ...but the best move itself is quiet, so it is not a puzzle.
    _, ending, loud = build_quiz_line(fen, "c3b3", ["c3b3"])
    assert ending is None and loud is False
    accept, _, reason = decide(quality, ending, loud)
    assert not accept and reason == "quiet_first_move", reason
    print("[PASS] quiet trap without a concrete ending is rejected")


def test_capture_trap_accepted_as_trap():
    # Qxb2 wins the bishop AND traps the Nb3: capture ending + trap.
    fen = "2B5/8/1P1K4/8/8/1nPkP2p/1b6/Q7 w - - 0 1"
    pv = ["a1b2", "d3c4"]
    quality = classify_quality(fen, "a1b2", pv, None)
    assert "trap" in quality.themes, quality.themes
    line, ending, loud = build_quiz_line(fen, "a1b2", pv)
    assert line == ["a1b2"] and ending == "capture" and loud is True
    accept, theme, _ = decide(quality, ending, loud)
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
    _, ending, loud = build_quiz_line(fen, "g2g7", ["g2g7"])
    accept, theme, _ = decide(quality, ending, loud)
    assert accept and theme == "mate", "mate needs no margin"
    print("[PASS] mate ending accepted as mate")


def test_mate_too_deep_rejected():
    quality = classify_quality(chess.STARTING_FEN, "e2e4", ["e2e4"], 7)
    _, ending, loud = build_quiz_line(chess.STARTING_FEN, "e2e4", ["e2e4"])
    accept, _, reason = decide(quality, ending, loud)
    assert not accept and reason == "mate_too_deep", reason
    print("[PASS] announced mate the quiz cannot deliver is rejected")


def test_capture_accepted_as_material():
    fen = "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1"
    pv = ["a1a5", "e8d7"]
    quality = classify_quality(fen, "a1a5", pv, None)
    _, ending, loud = build_quiz_line(fen, "a1a5", pv)
    assert ending == "capture" and loud is True
    accept, theme, _ = decide(quality, ending, loud)
    assert accept and theme == "material"
    print("[PASS] capture ending accepted as material")


def test_promotion_accepted():
    fen = "8/2P5/8/8/8/1k6/8/4K3 w - - 0 1"
    quality = classify_quality(fen, "c7c8q", ["c7c8q"], None)
    assert quality.is_promotion and "promotion" in quality.themes
    _, ending, loud = build_quiz_line(fen, "c7c8q", ["c7c8q"])
    accept, theme, _ = decide(quality, ending, loud)
    assert accept and theme == "promotion"
    print("[PASS] promotion ending accepted as promotion")


def test_promotion_ending_accepts_despite_recapture():
    # Queen promotes but the rook takes it straight back: themes stay
    # quiet, yet the best move promotes, so it counts.
    fen = "1r5k/P6p/8/8/8/8/8/K7 w - - 0 1"
    pv = ["a7a8q", "b8a8"]
    quality = classify_quality(fen, "a7a8q", pv, None)
    assert quality.is_promotion is True
    assert quality.themes == ["quiet"], quality.themes
    _, ending, loud = build_quiz_line(fen, "a7a8q", pv)
    assert ending == "promotion" and loud is True
    accept, theme, _ = decide(quality, ending, loud)
    assert accept and theme == "promotion"
    print("[PASS] promotion ending counts even when recaptured")


def test_quiet_rejected():
    quality = classify_quality(
        chess.STARTING_FEN, "g1f3", ["g1f3", "d7d5"], None
    )
    assert quality.themes == ["quiet"]
    _, ending, loud = build_quiz_line(
        chess.STARTING_FEN, "g1f3", ["g1f3", "d7d5"]
    )
    accept, _, reason = decide(quality, ending, loud)
    assert not accept and reason == "quiet_first_move", reason
    print("[PASS] quiet best move rejected up front")


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
    test_immediate_mate()
    test_immediate_promotion()
    test_immediate_winning_capture()
    test_immediate_losing_capture_rejected()
    test_immediate_capture_needs_reply()
    test_immediate_quiet_has_no_ending()
    test_immediate_illegal_best()
    test_quiz_truncates_at_decisive_capture()
    test_quiz_loud_first_multi_into_mate()
    test_quiz_quiet_first_mate_rejected()
    test_quiz_no_trailing_reply()
    test_trap_detected_but_needs_ending()
    test_capture_trap_accepted_as_trap()
    test_trap_already_hanging()
    test_trap_too_many_escapes()
    test_mate_accepted()
    test_mate_too_deep_rejected()
    test_capture_accepted_as_material()
    test_promotion_accepted()
    test_promotion_ending_accepts_despite_recapture()
    test_quiet_rejected()
    test_material_balance_sign()
    print("All assertions passed.")
