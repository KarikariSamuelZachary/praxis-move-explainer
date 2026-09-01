"""
Live test harness for the shared feature extractor
(src/services/persona_features.py) used by the Engine Sparring reranker.

Each fixture targets a specific known-bug class (labeled in each function).
We assert SIGN (and, where meaningful, rough magnitude) of the relevant field
-- not merely "it did not crash".

Fixtures (all required by the spec):
  1. obvious sacrifice            -> sacrifice_signal == 1.0
  2. normal even trade            -> sacrifice_signal == 0.0  (REGRESSION:
                                     the sacrifice-misclassification bug)
  3. obvious attacking move       -> attack_gain > 0, open_lines >= 1
  4. "fake attack"                -> attack_gain == 0 (geometric nearness
                                     alone must not count as pressure)
  5. defensive/consolidating move -> defense_gain > 0, line_blocking >= 1
  6. quiet developing move        -> defense_gain == 0 (development must not
                                     be misread as defense)
  7. king move into the open      -> defense_gain < 0 (safety NOT improved)
  8. castling                     -> defense_gain > 0 (safety improved)
  9. endgame with active king     -> king_mobility > 0

Run with: cd src && ../venv/bin/python services/persona_features_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from services.persona_features import _has_clear_ray, compute_style_scores


def _scores(fen, uci):
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    assert board.is_legal(move), f"illegal move {uci} in {fen}"
    return compute_style_scores(board, move)


def _dump(scores, debug):
    print(f"    move {debug['move_san']} ({debug['move_uci']})")
    print(f"      attack_gain={scores.attack_gain:+.3f} defense_gain={scores.defense_gain:+.3f} "
          f"sacrifice={scores.sacrifice_signal:.1f} volatility={scores.volatility:.3f}")
    a = scores.attack_sub
    d = scores.defense_sub
    print(f"      attack:  zone_pressure={a.king_zone_pressure:+.2f} adjacent={a.king_adjacent_attacks:+.1f} "
          f"checks={a.checks:.1f} open_lines={a.open_lines:.1f} escape={a.escape_square_pressure:+.1f}")
    print(f"      defense: enemy_press_red={d.enemy_pressure_reduction:+.2f} zone_def={d.king_zone_defense:+.2f} "
          f"line_block={d.line_blocking:.1f} shield={d.pawn_shield:+.2f} mobility={d.king_mobility:+.1f}")
    print(f"      sacrifice debug: hung={debug['sacrifice']['hung_value']} "
          f"captured={debug['sacrifice']['captured_value']} concession={debug['sacrifice']['concession']}")


def test_obvious_sacrifice():
    # Queen sacrifices itself on e5 (captures a bishop, is then en prise to
    # the e8 rook with no defender). Net concession 9 - 3 = 6 >= threshold.
    scores, debug = _scores("4r1k1/5ppp/8/4b3/8/8/8/2K1Q3 w - - 0 1", "e1e5")
    _dump(scores, debug)
    assert scores.sacrifice_signal == 1.0, scores.sacrifice_signal
    assert debug["sacrifice"]["concession"] >= 3
    assert scores.volatility > 0.0  # it is a capture -> nonzero volatility
    print("  [PASS] obvious sacrifice detected (signal=1.0, concession>=threshold)")


def test_even_trade_not_sacrifice():
    # REGRESSION for the sacrifice-misclassification bug: knight takes knight.
    # The capturing knight is left hanging (undefended, attacked by e6 pawn),
    # but it captured equal material, so net concession is 0 and this MUST NOT
    # be flagged as a sacrifice.
    scores, debug = _scores("6k1/5ppp/4p3/5n2/3N4/8/8/6K1 w - - 0 1", "d4f5")
    _dump(scores, debug)
    assert scores.sacrifice_signal == 0.0, scores.sacrifice_signal
    assert debug["sacrifice"]["hung_value"] == 3  # knight left en prise...
    assert debug["sacrifice"]["concession"] == 0  # ...but it's an even trade
    print("  [PASS] even trade NOT flagged (regression: concession=0 despite hung piece)")


def test_obvious_attacking_move():
    # Wayward queen: Qd1-h5 aims at f7 (next to the enemy king) along an open
    # diagonal. Creates real pressure + opens a line to the enemy king zone.
    scores, debug = _scores("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1", "d1h5")
    _dump(scores, debug)
    assert scores.attack_gain > 0.0, scores.attack_gain
    assert scores.attack_sub.open_lines >= 1, scores.attack_sub.open_lines
    assert scores.attack_sub.king_zone_pressure > 0.0
    print("  [PASS] attacking move: positive attack_gain, new open line to king")


def test_fake_attack_scores_zero():
    # A rook slides toward the enemy king's file (h1-g1, pointing at g8) but
    # its ray is blocked by its own g2 pawn: it touches no enemy-zone square
    # and opens nothing. Geometric "aiming" must NOT count as pressure.
    scores, debug = _scores("6k1/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w - - 0 1", "h1g1")
    _dump(scores, debug)
    assert scores.attack_gain == 0.0, scores.attack_gain
    assert scores.volatility == 0.0  # quiet, no capture/check/swing
    print("  [PASS] fake attack scores zero attack_gain (blocked ray -> no pressure)")


def test_defensive_consolidating_move():
    # White is in check from a rook on the open e-file; Bf1-e2 interposes and
    # blocks the line. The bishop is defended by the king, so it is a genuine
    # defensive block (not a hanging piece -> not a sacrifice). Big enemy-
    # pressure reduction + line blocking.
    scores, debug = _scores("4r1k1/5ppp/8/8/8/8/5PPP/4KB2 w - - 0 1", "f1e2")
    _dump(scores, debug)
    assert scores.defense_gain > 0.0, scores.defense_gain
    assert scores.defense_sub.line_blocking >= 1, scores.defense_sub.line_blocking
    assert scores.defense_sub.enemy_pressure_reduction > 0.0
    assert scores.sacrifice_signal == 0.0  # a defended block is not a sacrifice
    print("  [PASS] defensive move: positive defense_gain, blocks enemy line, not a sacrifice")


def test_quiet_developing_move_not_defensive():
    # Quiet fianchetto (Bc1-b2) while castled kingside. It develops a piece
    # (and, being a fianchetto, actually points *toward* the enemy king), but
    # it defends no own-king-zone square, reduces no enemy pressure, blocks
    # nothing, and does not move the king or pawns. defense_gain must be ~0:
    # geometric "same flank as the king" must NOT inflate defense.
    scores, debug = _scores("6k1/pppppppp/8/8/8/8/P1PPPPPP/2B2RK1 w - - 0 1", "c1b2")
    _dump(scores, debug)
    assert scores.defense_gain == 0.0, scores.defense_gain
    print("  [PASS] quiet developing move not scored as defensive (defense_gain=0)")


def test_king_move_into_open_not_safer():
    # King steps from behind f2/g2/h2 onto f1, next to the open e-file (enemy
    # rook) -- more exposed, not safer. Safety subcomponents must be non-
    # improving and overall defense_gain negative.
    scores, debug = _scores("4rrk1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1", "g1f1")
    _dump(scores, debug)
    assert scores.defense_gain < 0.0, scores.defense_gain
    assert scores.defense_sub.pawn_shield <= 0.0, scores.defense_sub.pawn_shield
    assert scores.defense_sub.enemy_pressure_reduction < 0.0, scores.defense_sub.enemy_pressure_reduction
    print("  [PASS] king walk into open: defense_gain<0 (safety NOT improved)")


def test_castling_improves_safety():
    # White's king sits in the center under two enemy rook lines (open d- and
    # e-files, blocked only by its own pawns). Castling kingside escapes both
    # files and connects the rooks. defense_gain must be positive.
    scores, debug = _scores("3rr1k1/ppp2ppp/8/8/8/8/PPPPPPPP/4K2R w K - 0 1", "e1g1")
    _dump(scores, debug)
    assert scores.defense_gain > 0.0, scores.defense_gain
    assert scores.defense_sub.enemy_pressure_reduction > 0.0
    print("  [PASS] castling: positive defense_gain, reduced enemy pressure")


def test_endgame_active_king():
    # King-and-pawn endgame; the king centralizes (Ke1-d2), gaining mobility
    # (activates toward the center). Must compute cleanly and show king
    # mobility increasing.
    scores, debug = _scores("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1", "e1d2")
    _dump(scores, debug)
    assert scores.defense_sub.king_mobility > 0.0, scores.defense_sub.king_mobility
    print("  [PASS] endgame active king: king_mobility>0 (king centralizes)")


def test_clear_ray_adjacency():
    # REGRESSION for the _has_clear_ray adjacency bug: two ALIGNED but ADJACENT
    # squares (nothing strictly between them) must count as a clear ray.
    # chess.between() returns an empty set for BOTH "not aligned" and "aligned
    # but adjacent", so the old guard `if not between: return False` wrongly
    # rejected adjacent aligned pairs (e.g. bishop h7 -> king g8 after Bxh7+).
    # Alignment must be checked first; adjacent aligned squares have zero
    # between-squares, which is trivially "all empty".
    #
    # 1. Adjacent on a diagonal (bishop h7 -> king g8, the Greek-gift case).
    board = chess.Board("6k1/7B/8/8/8/8/8/K7 w - - 0 1")
    assert _has_clear_ray(board, chess.H7, chess.G8) is True

    # 2. Adjacent on a rank (rook h7 -> square g7).
    board = chess.Board("8/7R/8/8/8/8/8/K7 w - - 0 1")
    assert _has_clear_ray(board, chess.H7, chess.G7) is True

    # 3. Non-adjacent, clear diagonal (bishop a1 -> h8) still works.
    board = chess.Board("7k/8/8/8/8/8/8/B6K w - - 0 1")
    assert _has_clear_ray(board, chess.A1, chess.H8) is True

    # 4. Non-adjacent, BLOCKED diagonal (pawn d4) -> no clear ray.
    board = chess.Board("7k/8/8/8/3P4/8/8/B6K w - - 0 1")
    assert _has_clear_ray(board, chess.A1, chess.H8) is False

    # 5. Not aligned at all (bishop a1 -> c2, knight geometry) -> no ray.
    board = chess.Board("8/8/8/8/8/8/2k5/B6K w - - 0 1")
    assert _has_clear_ray(board, chess.A1, chess.C2) is False
    print("  [PASS] clear-ray adjacency: adjacent aligned squares count; blocked/non-aligned do not")


def main() -> int:
    print("=== Running persona feature-extractor tests ===")
    tests = [
        test_obvious_sacrifice,
        test_even_trade_not_sacrifice,
        test_obvious_attacking_move,
        test_fake_attack_scores_zero,
        test_defensive_consolidating_move,
        test_quiet_developing_move_not_defensive,
        test_king_move_into_open_not_safer,
        test_castling_improves_safety,
        test_endgame_active_king,
        test_clear_ray_adjacency,
    ]
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            print(f"\n  [FAIL] {test.__name__}: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
            return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
