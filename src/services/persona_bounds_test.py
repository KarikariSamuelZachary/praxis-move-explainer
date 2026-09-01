"""
Live test harness for the persona bounding / decay / phase-gate layer
(src/services/persona_bounds.py) used by the Engine Sparring reranker.

Covers:

  A. engine_trust(): endpoint values (1.0 at 0, exactly 0.0 at/beyond the
     boundary), strict monotonic decrease, and the actual decay-curve shape
     printed at several sample points so a reviewer can see the real numbers.

  B. game_phase(): 0.0 at the starting position, partial for a queenless
     middlegame, ~1.0 for a king+pawn endgame, using real FENs.

  C. bounded_persona_bias(): defensive clamp of the raw persona score to
     [-1, 1] and scaling by engine trust (phase intentionally unused).

  D. Integration: several fake candidates with different engine_norm_cp and
     raw_persona_score, proving the trust decay changes the FINAL ranking --
     a strongly-persona-preferred candidate at -70cp stays below a mildly
     preferred one at -5cp, and a -1cp strongly-preferred candidate no longer
     ties the engine's best move once decay is applied.

Run with: cd src && ../venv/bin/python services/persona_bounds_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from services.persona_bounds import bounded_persona_bias, engine_trust, game_phase

START_FEN = chess.STARTING_FEN
QUEENLESS_MIDDLEGAME_FEN = "rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNB1KBNR w KQkq - 0 1"
KING_PAWN_ENDGAME_FEN = "4k3/4p3/8/8/8/8/4P3/4K3 w - - 0 1"


def test_engine_trust_curve():
    # Endpoints and boundary behavior.
    assert engine_trust(0.0) == 1.0, engine_trust(0.0)
    assert engine_trust(-75.0) == 0.0, engine_trust(-75.0)
    assert engine_trust(-100.0) == 0.0, engine_trust(-100.0)
    assert engine_trust(-75.0 - 1e-9) == 0.0, engine_trust(-75.0 - 1e-9)

    # Positive (defensive) input clamps to 1.0.
    assert engine_trust(5.0) == 1.0, engine_trust(5.0)

    # Monotonic strictly decreasing as engine_norm_cp decreases.
    sample_points = [0.0, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0, -60.0, -70.0]
    values = [engine_trust(x) for x in sample_points]
    for lo, hi in zip(values, values[1:]):
        assert lo > hi, f"expected strict decrease, got {lo} then {hi}"

    # Always within [0, 1].
    for x in sample_points:
        assert 0.0 <= engine_trust(x) <= 1.0

    print("  decay curve:")
    for x in [0, -20, -40, -60, -75]:
        print(f"    trust({x:>4}) = {engine_trust(float(x)):.6f}")
    print("  [PASS] endpoints, boundary clamp, and strict monotonicity hold")


def test_game_phase():
    phase_start = game_phase(chess.Board(START_FEN))
    assert abs(phase_start - 0.0) < 1e-9, phase_start

    phase_queenless = game_phase(chess.Board(QUEENLESS_MIDDLEGAME_FEN))
    assert 0.0 < phase_queenless < 1.0, phase_queenless
    assert abs(phase_queenless - 0.29032258) < 1e-4, phase_queenless

    phase_endgame = game_phase(chess.Board(KING_PAWN_ENDGAME_FEN))
    assert abs(phase_endgame - 1.0) < 1e-9, phase_endgame

    print(f"  [PASS] start={phase_start:.4f} queenless={phase_queenless:.4f} "
          f"king+pawn={phase_endgame:.4f}")


def test_bounded_persona_bias_clamps_and_scales():
    # Clamp to [-1, 1].
    assert bounded_persona_bias(3.0, 0.0, phase=0.0) == 1.0
    assert bounded_persona_bias(-3.0, 0.0, phase=0.0) == -1.0

    # At engine_norm_cp == 0, trust == 1.0, so bias == clamped score.
    assert abs(bounded_persona_bias(0.4, 0.0, phase=0.0) - 0.4) < 1e-12

    # At the boundary, bias collapses to 0.0 regardless of raw score.
    assert bounded_persona_bias(0.9, -75.0, phase=0.0) == 0.0
    assert bounded_persona_bias(-0.9, -100.0, phase=0.0) == 0.0

    # phase is intentionally unused: changing it must not change the result.
    assert bounded_persona_bias(0.5, -20.0, phase=0.0) == \
        bounded_persona_bias(0.5, -20.0, phase=1.0)

    print("  [PASS] clamp to [-1,1], trust scaling, and phase-unused all hold")


def test_trust_decay_affects_ranking():
    # Fake candidates: (engine_norm_cp, raw_persona_score, label).
    candidates = [
        (0.0, 0.0, "engine-best-neutral"),
        (-1.0, 1.0, "strongly-preferred-1cp-worse"),
        (-5.0, 0.2, "mildly-preferred-5cp-worse"),
        (-70.0, 0.9, "strongly-preferred-70cp-worse"),
    ]

    scored = []
    for cp, raw, label in candidates:
        bias = bounded_persona_bias(raw, cp, phase=0.0)
        final = cp + bias
        scored.append((final, bias, label))

    scored.sort(key=lambda t: t[0], reverse=True)
    order = [label for _, _, label in scored]
    by_label = {label: (final, bias) for final, bias, label in scored}

    # 1. The engine's best move (persona-neutral) must rank first.
    assert order[0] == "engine-best-neutral", order

    # 2. Required example: a strongly-persona-preferred candidate at -70cp
    #    ranks BELOW a mildly preferred one at -5cp. (The -70 candidate's
    #    raw 0.9 preference is crushed by trust decay.)
    assert order.index("mildly-preferred-5cp-worse") < \
        order.index("strongly-preferred-70cp-worse"), order

    # 3. Trust decay changes the final ordering at the margin: at -1cp with a
    #    raw 1.0 persona score, the candidate would TIE the engine's best move
    #    if the raw score were added un-decayed (-1 + 1.0 == 0.0). With decay
    #    its final score is strictly below 0.0, so it no longer ties.
    final_1cp, _ = by_label["strongly-preferred-1cp-worse"]
    assert final_1cp < 0.0, final_1cp

    # 4. The -70 candidate's bias was genuinely crushed (raw 0.9 -> ~0.023),
    #    not just marginally reduced.
    _, bias_70 = by_label["strongly-preferred-70cp-worse"]
    assert bias_70 < 0.05, bias_70
    assert bias_70 < by_label["mildly-preferred-5cp-worse"][1]

    print("  final ranking (final = engine_norm_cp + bounded_bias):")
    for final, bias, label in scored:
        print(f"    {label:32s}  final={final:+.4f}  (bias={bias:+.4f})")
    print("  [PASS] trust decay affects final ordering, not just the trust number")


def main() -> int:
    print("=== Running persona bounding/decay/phase-gate tests ===")
    tests = [
        test_engine_trust_curve,
        test_game_phase,
        test_bounded_persona_bias_clamps_and_scales,
        test_trust_decay_affects_ranking,
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
