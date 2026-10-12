"""
Test harness for services/puzzle_quality.py.

Pure board/PV analysis (no engine calls), so every case is a fixed FEN +
UCI line with the expected theme/acceptance asserted directly.

Covers the extractor upgrade spec:
  1. material_swing_cp: net material for the solver over <= 3 PV plies
     (captures and promotions; None on empty/illegal PV).
  2. Mate-in-1..4 tags THEME_MATE and is accepted; deeper mates are
     rejected as mate_too_deep (the stored 6-ply line cannot show them).
  3. find_piece_trap: enemy piece with <= 1 safe escape after our move
     that was not already hanging; negatives (already hanging, > 1
     escape) return None.
  4. Promotion detection: a winning promotion tags THEME_PROMOTION; a
     promotion that drops material without mate stays quiet.
  5. decide(): accept mate / material / trap / promotion outright, else
     require margin >= 0.12 on a forcing line (tagged "forcing");
     quiet squeezes are rejected as quiet_positional.

Run:
    cd src && ../venv/bin/python services/puzzle_quality_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.puzzle_quality as mod
from services.puzzle_quality import (
    QualityReport,
    classify_quality,
    decide,
    find_piece_trap,
    forcing_flags,
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
    # Scholar's mate tactics: capture then mate delivery are forcing.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3"
    flags = forcing_flags(fen, ["d1h5", "g8f6", "h5f7"])
    assert flags[0] is False, f"Qh5 is quiet, got {flags}"
    assert flags[2] is True, f"Qxf7# is check/capture, got {flags}"
    print("[PASS] forcing flags mark checks/captures per ply")


def test_trap_positive():
    # Kc3-b3 traps the Na3: attacked once, undefended, 1 safe escape.
    fen = "8/1P6/B1kP2P1/5P2/8/n1K1p3/8/8 w - - 0 1"
    trap = find_piece_trap(fen, "c3b3")
    assert trap is not None, "expected a trap on a3"
    assert (trap.square, trap.piece, trap.value) == ("a3", "N", 320)
    assert trap.escapes <= 1
    assert trap.created_by_move is True
    quality = classify_quality(fen, "c3b3", ["c3b3"], None)
    assert "trap" in quality.themes, quality.themes
    accept, theme, _ = decide(quality, 0.0)
    assert accept and theme == "trap", "clean trap accepted w/o margin"
    print("[PASS] piece trap detected, tagged, and accepted")


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
    quality = classify_quality(
        "7k/8/5K2/8/8/8/6Q1/8 w - - 0 1", "g2g7", ["g2g7"], 1
    )
    assert quality.mate_in == 1 and "mate" in quality.themes
    accept, theme, _ = decide(quality, None)
    assert accept and theme == "mate", "mate in 1 needs no margin"
    print("[PASS] mate in 1 tagged and accepted")


def test_mate_too_deep_rejected():
    quality = classify_quality(chess.STARTING_FEN, "e2e4", ["e2e4"], 7)
    accept, _, reason = decide(quality, 0.20)
    assert not accept and reason == "mate_too_deep", reason
    print("[PASS] mate beyond 4 rejected even with high margin")


def test_material_accepted():
    quality = classify_quality(
        "4k3/8/8/b7/8/8/8/R3K3 w - - 0 1",
        "a1a5",
        ["a1a5", "e8d7"],
        None,
    )
    assert quality.material_gain == 330
    assert "material" in quality.themes
    accept, theme, _ = decide(quality, 0.0)
    assert accept and theme == "material"
    print("[PASS] net material >= +3 within 3 plies accepted")


def test_promotion_winning_accepted():
    quality = classify_quality(
        "8/2P5/8/8/8/1k6/8/4K3 w - - 0 1", "c7c8q", ["c7c8q"], None
    )
    assert quality.is_promotion and "promotion" in quality.themes
    accept, theme, _ = decide(quality, 0.0)
    assert accept and theme == "promotion"
    print("[PASS] winning promotion accepted")


def test_promotion_losing_stays_quiet():
    # Queen promotes but the rook takes it straight back: -100, no mate.
    quality = classify_quality(
        "1r5k/P6p/8/8/8/8/8/K7 w - - 0 1",
        "a7a8q",
        ["a7a8q", "b8a8"],
        None,
    )
    assert quality.is_promotion is True
    assert quality.material_gain == -100
    assert quality.themes == ["quiet"], quality.themes
    accept, _, reason = decide(quality, 0.0)
    assert not accept and reason == "quiet_positional"
    print("[PASS] non-winning promotion stays quiet and is rejected")


def test_high_margin_forcing_accepted():
    quality = QualityReport(
        themes=["forcing"],
        primary="forcing",
        mate_in=None,
        material_gain=0,
        trap=None,
        forcing=2,
        is_promotion=False,
    )
    accept, theme, _ = decide(quality, mod.MARGIN_HIGH)
    assert accept and theme == "forcing", "forcing + margin tagged forcing"
    print("[PASS] high margin + forcing line accepted as forcing")


def test_forcing_theme_from_classify():
    # Rook checks chase the lone king: forcing, but no mate, material,
    # trap, or promotion -- so the line is tagged forcing, not quiet.
    quality = classify_quality(
        "4k3/8/8/8/8/8/8/R3K3 w - - 0 1",
        "a1a8",
        ["a1a8", "e8d7", "a8a7", "d7e6"],
        None,
    )
    assert quality.themes == ["forcing"], quality.themes
    assert quality.primary == "forcing"
    accept, theme, _ = decide(quality, mod.MARGIN_HIGH)
    assert accept and theme == "forcing"
    print("[PASS] forcing line classified and stored as forcing")


def test_margin_boundary():
    quality = QualityReport(
        themes=["forcing"],
        primary="forcing",
        mate_in=None,
        material_gain=0,
        trap=None,
        forcing=2,
        is_promotion=False,
    )
    accept, _, reason = decide(quality, mod.MARGIN_HIGH - 0.001)
    assert not accept and reason == "quiet_positional", reason
    print("[PASS] margin just below 0.12 is rejected")


def test_quiet_non_forcing_rejected():
    quality = classify_quality(
        chess.STARTING_FEN, "g1f3", ["g1f3", "d7d5"], None
    )
    assert quality.themes == ["quiet"]
    accept, _, reason = decide(quality, 0.15)
    assert not accept, "high margin alone must not save a quiet line"
    print("[PASS] quiet positional squeeze rejected despite high margin")


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
    test_trap_positive()
    test_trap_already_hanging()
    test_trap_too_many_escapes()
    test_mate_accepted()
    test_mate_too_deep_rejected()
    test_material_accepted()
    test_promotion_winning_accepted()
    test_promotion_losing_stays_quiet()
    test_high_margin_forcing_accepted()
    test_forcing_theme_from_classify()
    test_margin_boundary()
    test_quiet_non_forcing_rejected()
    test_material_balance_sign()
    print("All assertions passed.")
