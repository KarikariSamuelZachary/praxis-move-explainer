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
 10. defended-pawn gambit offers  -> sacrifice_signal == 1.0 via the NEW
                                    gambit-offer path (Danish 3.c3,
                                    Smith-Morra 3.c3, BDG 4.f3 -- the three
                                    real positions from the gambit
                                    investigation; the en-prise path stays at
                                    concession=0 on all three, so these pin
                                    the new path specifically)
 11. gambit-offer controls        -> equal-material opening tension (French
                                    2...d5), a LEVEL-material undefended
                                    offer (King's Gambit 2.f4 -- the known
                                    out-of-scope false negative), and a
                                    down-material ENDGAME tension (opening
                                    gate) all stay 0.0
 12. pinned false-positive class  -> a position statically IDENTICAL to the
                                    Danish shape but reached without any
                                    gambit fires -- the detector's
                                    intent-blindness gap, pinned as accepted

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


def _position_before_and_move(san_moves):
    """Build a position by playing SAN from the start, returning the position
    BEFORE the final move plus that final move (legality guaranteed by
    python-chess; the same construction the gambit investigation used)."""
    board = chess.Board()
    for san in san_moves[:-1]:
        board.push_san(san)
    return board, board.parse_san(san_moves[-1])


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


def test_gambit_offer_defended_pawn_fires():
    # The three REAL gambit positions from the investigation report. Each
    # offers a DEFENDED pawn into pawn-vs-pawn tension, so the en-prise path
    # scores concession=0 on all three (measured: hung=0, captured=0) -- the
    # signal must come from the NEW gambit-offer path, and these assertions
    # pin exactly that (old path silent AND new path fired AND signal 1.0).
    # Material at each offer moment: mover 38 vs opponent 39 (already down a
    # pawn, locked in by the earlier never-recaptured capture); the offered
    # pawn is defended (bxc3 / bxc3 / gxf3 recaptures all exist).
    cases = [
        (["e4", "e5", "d4", "exd4", "c3"],
         "Danish Gambit 3.c3 (offers the d4 pawn; after ...dxc3 bxc3 the "
         "mover is 37 vs 38 -- down a pawn with compensation)"),
        (["e4", "c5", "d4", "cxd4", "c3"],
         "Smith-Morra 3.c3 (same shape as the Danish)"),
        (["d4", "d5", "e4", "dxe4", "Nc3", "Nf6", "f3"],
         "BDG 4.f3 (offers the e4 pawn via f3; after ...exf3 the mover is "
         "down a pawn -- and this is Stockfish's own #1 move here)"),
    ]
    for san_moves, label in cases:
        board, move = _position_before_and_move(san_moves)
        assert board.is_valid(), (label, board.status())
        scores, debug = compute_style_scores(board, move)
        g = debug["gambit_offer"]
        assert debug["sacrifice"]["concession"] == 0, (label, debug["sacrifice"])
        assert g["fired"] is True, (label, g)
        assert g["quiet_pawn_push"] and g["pawn_tension"] and g["defended_offer"]
        assert g["mover_behind"] and g["opening_gate"]
        assert g["mover_material"] < g["enemy_material"], (label, g)
        assert scores.sacrifice_signal == 1.0, (label, scores.sacrifice_signal)
        print(f"    {label}")
        print(f"      old-path concession=0 (pawn defended); gambit path fired: "
              f"mover={g['mover_material']} enemy={g['enemy_material']}")
    print("  [PASS] defended-pawn gambit offers fire via the new path "
          "(3 real gambits, old path verified silent)")


def test_gambit_offer_no_fire_equal_material():
    # French 1.e4 e6 2.d4 d5: ...d5 creates the same pawn-vs-pawn tension
    # (e4 pawn attacks d5, d5 is defended by Qd8), but the mover is NOT
    # behind in material (39 vs 39) -- condition (d) must keep it silent.
    # This is the key false-positive class for the new path.
    board, move = _position_before_and_move(["e4", "e6", "d4", "d5"])
    scores, debug = compute_style_scores(board, move)
    g = debug["gambit_offer"]
    assert g["quiet_pawn_push"] is True, g
    assert g["pawn_tension"] is True, g
    assert g["defended_offer"] is True, g
    assert g["mover_behind"] is False, g
    assert scores.sacrifice_signal == 0.0, scores.sacrifice_signal
    print("  [PASS] equal-material opening tension (French ...d5) stays silent")


def test_gambit_offer_no_fire_level_material_offer():
    # King's Gambit 2.f4: a genuine UNDEFENDED pawn offer (e5 pawn attacks
    # f4), but the mover is NOT behind in material at the offer moment
    # (39 vs 39) AND the offer is undefended (no recapture exists). This is
    # the known threshold-class false negative, explicitly OUT OF SCOPE for
    # the gambit-offer path -- conditions (c) and (d) must both keep it
    # silent here.
    board, move = _position_before_and_move(["e4", "e5", "f4"])
    scores, debug = compute_style_scores(board, move)
    g = debug["gambit_offer"]
    assert g["pawn_tension"] is True, g
    assert g["defended_offer"] is False, g
    assert g["mover_behind"] is False, g
    assert scores.sacrifice_signal == 0.0, scores.sacrifice_signal
    print("  [PASS] level-material undefended offer (KG 2.f4) stays silent "
          "(documented out-of-scope false negative)")


def test_gambit_offer_no_fire_endgame():
    # Down-material ENDGAME pawn tension: same shape as the gambits (quiet
    # pawn push, enemy-pawn tension, defended offer, mover behind) but the
    # opening gate (non-pawn material floor) must block it -- a down-material
    # endgame pawn break is routine technique, not gambling. White (2 pawns:
    # b2+c2 vs Black's d4+a7+h7 = 3) is behind; c3 is defended by b2, so the
    # OPENING GATE is the specific blocker this test isolates.
    board = chess.Board("4k3/p6p/8/8/3p4/8/1PP5/4K3 w - - 0 1")
    move = chess.Move.from_uci("c2c3")
    scores, debug = compute_style_scores(board, move)
    g = debug["gambit_offer"]
    assert g["quiet_pawn_push"] is True, g
    assert g["pawn_tension"] is True, g
    assert g["defended_offer"] is True, g
    assert g["mover_behind"] is True, g
    assert g["opening_gate"] is False, g
    assert scores.sacrifice_signal == 0.0, scores.sacrifice_signal
    print("  [PASS] down-material endgame tension blocked by the opening gate")


def test_gambit_offer_pinned_false_positive_class():
    # PINNED KNOWN FALSE-POSITIVE CLASS (intent-blindness): this position is
    # statically IDENTICAL in shape to the Danish -- quiet pawn push c2-c3
    # into an enemy-pawn (d4) tension, c3 defended, mover behind in material,
    # opening phase -- but the material deficit came from whatever happened
    # earlier, NOT from a gambit offer. P -> P' cannot see the deficit's
    # origin, so the signal fires here, and that is ACCEPTED and pinned:
    # any future edit that silences this case is a conscious design change,
    # not an accident.
    board = chess.Board("rnbqkbnr/ppp2ppp/8/8/3p4/8/PPP2PPP/R1Q1KBNR w - - 0 1")
    move = chess.Move.from_uci("c2c3")
    scores, debug = compute_style_scores(board, move)
    g = debug["gambit_offer"]
    assert g["quiet_pawn_push"] is True, g
    assert g["pawn_tension"] is True, g
    assert g["defended_offer"] is True, g
    assert g["mover_behind"] is True, g
    assert g["opening_gate"] is True, g
    assert scores.sacrifice_signal == 1.0, scores.sacrifice_signal
    print("  [PASS] pinned false-positive class: gambit-shaped-but-not-gambit "
          "fires (intent-blindness, accepted)")


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


def test_sacrifice_gate_matrix():
    # Every sacrifice-gate behavior established in the review rounds, as one
    # permanent matrix on real fixture positions (see _sacrifice_concession's
    # docstring for the rule and its documented limitations).
    cases = [
        ("r1bq1rk1/pppnbppp/8/4P1N1/3P4/3B4/PPP2PPP/RNBQ1RK1 w - - 0 1", "d3h7", 1.0,
         "Bxh7+: check-gated king-stab (check + piece-for-pawn + king-adjacent)"),
        ("r1bqkb1r/ppp2ppp/2n5/3np1N1/2B5/8/PPPP1PPP/RNBQK2R w KQkq - 0 6", "g5f7", 1.0,
         "Nxf7 (Fried Liver): genuinely en prise, undefended -> threshold=2 fires"),
        ("6k1/5ppp/4p3/5n2/3N4/8/8/6K1 w - - 0 1", "d4f5", 0.0,
         "Nxf5 even trade: concession=0 despite the hung knight (regression)"),
        ("r1bq1rk1/pppnbppp/8/4P1N1/3P4/3B4/PPP2PPP/RNBQ1RK1 w - - 0 1", "g5h7", 0.0,
         "Nxh7: no check -> king-stab gate closed -> ACCEPTED false negative"),
        ("5rk1/5ppp/7Q/5N2/8/8/5PPP/6K1 w - - 0 1", "h6g7", 0.0,
         "Qxg7# mate: checkmate is excluded from the king-stab (mate gate)"),
    ]
    for fen, uci, expected, why in cases:
        scores, debug = _scores(fen, uci)
        assert scores.sacrifice_signal == expected, (debug["move_san"], expected)
        print(f"    {debug['move_san']:<7} sac={scores.sacrifice_signal:.1f}  ({why})")
    print("  [PASS] sacrifice-gate matrix: 2 fire, 2 correctly silent, mate gated")


def test_pinned_known_gaps():
    # PINNED KNOWN GAPS -- these assert the CURRENT, accepted behavior so any
    # future edit that changes it is a conscious decision, not an accident.
    #
    # GAP 1 (safe grab): a DEFENDED piece-for-pawn capture on a
    # king-controlled square WITHOUT check must stay silent. The check gate
    # on king-stab exists precisely to keep this from false-positiving.
    scores, debug = _scores("6k1/6pp/8/4B3/8/8/6Q1/6K1 w - - 0 1", "e5g7")
    assert scores.sacrifice_signal == 0.0, debug["sacrifice"]
    assert debug["sacrifice"]["hung_value"] == 0
    print("    Bxg7 (defended by Qg2, king controls g7, no check): sac=0.0")
    #
    # GAP 2 (COMPENSATION-BLINDNESS, named in persona_features.py): an
    # UNDEFENDED piece-for-pawn capture with no compensation is flagged as a
    # sacrifice. Threshold=2 + the no-lookahead contract cannot tell a real
    # gamble from material thrown away for nothing; closing that requires
    # looking past the single move (same category as initiative_proxy).
    scores, debug = _scores("4k3/5p2/8/6N1/8/8/8/4K3 w - - 0 1", "g5f7")
    assert scores.sacrifice_signal == 1.0, debug["sacrifice"]
    assert debug["sacrifice"]["concession"] == 2
    print("    Nxf7 (undefended, no check, no compensation): sac=1.0  [COMPENSATION-BLINDNESS pin]")
    print("  [PASS] both documented gaps behave exactly as documented")


def test_compute_style_scores_deterministic():
    # The extractor is a pure function of (board_before, move): running it
    # twice must produce identical StyleScores AND debug dicts. No engine,
    # no time, no hidden state -- any divergence would be a real bug.
    cases = [
        ("r1bq1rk1/pppnbppp/8/4P1N1/3P4/3B4/PPP2PPP/RNBQ1RK1 w - - 0 1", "d3h7"),
        ("5rk1/5ppp/7Q/5N2/8/8/5PPP/6K1 w - - 0 1", "h6g7"),
        ("r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4", "e1g1"),
    ]
    for fen, uci in cases:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        s1, d1 = compute_style_scores(board, move)
        s2, d2 = compute_style_scores(board, move)
        assert s1 == s2 and d1 == d2, uci
    print("  [PASS] compute_style_scores is deterministic (pure function)")


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
        test_gambit_offer_defended_pawn_fires,
        test_gambit_offer_no_fire_equal_material,
        test_gambit_offer_no_fire_level_material_offer,
        test_gambit_offer_no_fire_endgame,
        test_gambit_offer_pinned_false_positive_class,
        test_clear_ray_adjacency,
        test_sacrifice_gate_matrix,
        test_pinned_known_gaps,
        test_compute_style_scores_deterministic,
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
