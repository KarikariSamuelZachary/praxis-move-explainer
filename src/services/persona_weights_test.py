"""
Live test harness for the persona weight layer
(src/services/persona_weights.py) used by the Engine Sparring reranker.

PART 1 unit tests (normalization):
  * zero maps to exactly 0
  * strictly monotonic over the whole (and beyond-documented) range
  * very large inputs approach but never reach +/-1
  * real fixture values (Qxg7#, castling O-O, Greek gift, Be2, Kf1) printed
    raw -> normalized so a human can sanity-check the tanh mapping against
    actual positions, not just synthetic numbers

PART 2/3 unit tests (weights + phase gating):
  * all persona scores bounded to [-1, 1]
  * Attacker and Sacrificer correlate on Bxh7+ (both positive, sacrificer
    heavier) but DIVERGE on a non-sacrificial direct attack (Qxg7#: mate-gated
    sac=0.0 -> attacker stays high, sacrificer low)
  * phase damping touches ONLY the king-zone-pressure attack subcomponents;
    sacrifice/volatility/concrete tactical events pass through untouched
  * Defender: defense_gain dominates with NEGATIVE style penalties (attack/
    sacrifice mildly, volatility more), so its final clamp is load-bearing;
    sign agreement with defense_gain's established signs on the four
    defense-sign fixtures; defense-side phase damping zeroes the STATIC
    features (king_zone_defense, pawn_shield) while concrete deltas and king
    mobility survive; on the near-equal fixture the Defender promotes the
    quiet rook activation (Re1) that both other personas leave demoted
  * Positional: PURE PENALTY persona (no positive anchor -- the argued
    design decision is pinned in persona_weights.py's weight block): a
    perfectly quiet move scores exactly 0,     sharpness is penalized
    (volatility dominant, attack_gain strong, sacrifice incremental,
    defense 0), the de-escalation credit is provably self-limiting, and the
    clamp is VESTIGIAL (practical range [-0.78, 0], unlike Defender's);
    sharp tops score lower under Positional than under all three other
    personas; on the near-equal fixture Positional's ranking equals cp
    order (zero bias on all-quiet candidates) with an asserted pairwise
    flip against each of the other three personas
  * Gambiter: DERIVED persona (literal 0.7 Attacker + 0.3 Sacrificer blend,
    recomputed from the parents' LIVE constants so a parent retune can
    never drift it silently); the blend identity g == 0.7*A + 0.3*S holds
    EXACTLY wherever the clamps are vestigial (asserted to 1e-12); on the
    sharp top candidates of the two sacrifice fixtures it lands strictly
    between the parent scores (real numbers printed); on the near-equal
    fixture its ranking is genuinely unique among all five personas
    (Qd2 > Qd3 > Ne5 > a4 > Re1 -- the only persona putting Qd3 second);
    on the quiet positional fixture it coincides with cp order with the
    coincidence QUANTIFIED (h6/a5/a6 score exactly 0; Bb6/Kh8's biases are
    1.5/0.7cp after trust, far short of the 2-4cp gaps)
  * end-to-end: the same StyleScores score lower under Attacker in a bare
    king-and-pawn endgame (phase 1.0) than in the opening (phase 0.0)

PART 4 integration (engine, print-only):
  full pipeline on the sacrifice fixtures (obvious sacrifice + sharp
  tactical: do Defender/Positional correctly NOT promote the sacrifice-
  heavy top candidate?), the quiet positional middlegame fixture (built
  FOR Positional; all-quiet candidates -> expected cp-order fallback),
  the near-equal mixed-style fixture (persona reorder comparison) and
  active endgame king (phase-gating sanity check):
  suggest() -> canonicalize_by_score() -> compute_style_scores() ->
  attacker_score()/sacrificer_score()/defender_score()/positional_score()
   -> persona_adjusted_score(real engine_norm_cp, real game_phase), i.e.
   final = norm + PERSONA_BIAS_CP (=100) * bounded_bias, demotion-floored
   at -75, with the engine/Attacker/Sacrificer/Defender/Positional/Gambiter
   orderings printed side by side. The ENGINE order is
   canonicalize_by_score()'s strict cp-descending order, so "(unchanged)"
   can only mean the persona contributed nothing -- never that the list
   needed re-sorting anyway.

Run with: cd src && ../venv/bin/python services/persona_weights_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from engines.stockfish_engine import StockfishEngine
from services.persona_bounds import engine_trust, game_phase
from services.persona_features import (
    AttackGainSub,
    DefenseGainSub,
    StyleScores,
    compute_style_scores,
)
from services.persona_fixtures import FIXTURES
from services.persona_weights import (
    PERSONA_BIAS_CP,
    _GAMBITER_W_ATTACK,
    _GAMBITER_W_DEFENSE,
    _GAMBITER_W_INITIATIVE,
    _GAMBITER_W_SACRIFICE,
    _GAMBITER_W_VOLATILITY,
    _ATTACKER_W_ATTACK,
    _ATTACKER_W_DEFENSE,
    _ATTACKER_W_INITIATIVE,
    _ATTACKER_W_SACRIFICE,
    _ATTACKER_W_VOLATILITY,
    _SACRIFICER_W_ATTACK,
    _SACRIFICER_W_DEFENSE,
    _SACRIFICER_W_INITIATIVE,
    _SACRIFICER_W_SACRIFICE,
    _SACRIFICER_W_VOLATILITY,
    _defender_phase_damped_scores,
    _phase_damped_scores,
    _squash_signed,
    attacker_score,
    canonicalize_by_score,
    defender_score,
    gambiter_score,
    normalize_style_scores,
    persona_adjusted_score,
    positional_score,
    sacrificer_score,
)

GREEK_FEN = "r1bq1rk1/pppnbppp/8/4P1N1/3P4/3B4/PPP2PPP/RNBQ1RK1 w - - 0 1"
MATE_FEN = "5rk1/5ppp/7Q/5N2/8/8/5PPP/6K1 w - - 0 1"
ITALIAN_FEN = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"


def _scores(fen, uci):
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    assert board.is_legal(move), f"illegal move {uci} in {fen}"
    return compute_style_scores(board, move)


def _synthetic(kzp=0.0, kaa=0.0, checks=0.0, open_lines=0.0, esc=0.0,
               defense=0.0, sac=0.0, vol=0.0):
    """Hand-built StyleScores for normalization/gating unit tests."""
    attack_gain = kzp + kaa + checks + open_lines + esc
    return StyleScores(
        attack_gain=attack_gain,
        defense_gain=defense,
        sacrifice_signal=sac,
        volatility=vol,
        initiative_proxy=0.0,
        attack_sub=AttackGainSub(kzp, kaa, checks, open_lines, esc),
        defense_sub=DefenseGainSub(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _synthetic_def(epr=0.0, kzd=0.0, blk=0.0, shd=0.0, mob=0.0,
                   kzp=0.0, sac=0.0, vol=0.0):
    """Hand-built StyleScores with CONSISTENT defense subcomponents.

    Unlike _synthetic (whose defense subs stay zero, fine for attack-side
    tests), the Defender DAMPS king_zone_defense/pawn_shield and REBUILDS
    defense_gain as sum(defense_sub) -- so a defense-side unit test must
    set the SUBS, not just the aggregate, or the rebuild would zero it.
    """
    defense_gain = epr + kzd + blk + shd + mob
    return StyleScores(
        attack_gain=kzp,
        defense_gain=defense_gain,
        sacrifice_signal=sac,
        volatility=vol,
        initiative_proxy=0.0,
        attack_sub=AttackGainSub(kzp, 0.0, 0.0, 0.0, 0.0),
        defense_sub=DefenseGainSub(epr, kzd, blk, shd, mob),
    )


def test_zero_maps_to_zero():
    n = normalize_style_scores(_synthetic())
    assert n["attack_gain"] == 0.0, n["attack_gain"]
    assert n["defense_gain"] == 0.0, n["defense_gain"]
    assert n["sacrifice_signal"] == 0.0
    assert n["volatility"] == 0.0
    assert n["initiative_proxy"] == 0.0
    # pass-through: already-bounded fields are unchanged, not re-squashed
    n2 = normalize_style_scores(_synthetic(sac=1.0, vol=0.7))
    assert n2["sacrifice_signal"] == 1.0
    assert n2["volatility"] == 0.7
    print("  [PASS] 0 maps to 0; [0,1] fields pass through unchanged")


def test_monotonic():
    xs = [-30, -20, -10, -5, -2, -0.5, 0.0, 0.5, 2, 5, 10, 20, 30]
    prev = -2.0
    for x in xs:
        n = normalize_style_scores(_synthetic(kzp=float(x)))["attack_gain"]
        assert n > prev, f"attack_gain {x}: {n} not > previous {prev}"
        prev = n
    prev = -2.0
    for x in xs:
        n = normalize_style_scores(_synthetic(defense=float(x)))["defense_gain"]
        assert n > prev, f"defense_gain {x}: {n} not > previous {prev}"
        prev = n
    print("  [PASS] strictly monotonic on both signed fields (incl. beyond [-8,+20])")


def test_large_inputs_approach_but_never_reach_one():
    # IEEE note: float64 tanh returns EXACTLY 1.0 once |x/REF| >= ~19, i.e.
    # raw magnitudes around +/-266 with REF=14 -- unreachable in practice
    # (the mate-class ceiling is +15; even a queen camping in the king's ring
    # stays under ~+50). The magnitudes below are already absurd for real
    # positions and still do NOT reach 1.0.
    for v in (50.0, 100.0, 200.0):
        pos = _squash_signed(v)
        neg = _squash_signed(-v)
        assert 0.998 < pos < 1.0, (v, pos)
        assert -1.0 < neg < -0.998, (v, neg)
        print(f"    {v:+7.1f} -> {pos:.12f} / {neg:.12f}")
    print("  [PASS] huge inputs approach but never reach +/-1")


def test_real_fixture_values():
    """Print raw -> normalized for REAL StyleScores pulled from the
    persona_dump.py fixtures (FENs are the fixture FENs, moves are the
    engine's actual top candidates)."""
    cases = [
        ("obvious attack: Qxg7# mate", MATE_FEN, "h6g7"),
        ("castling: O-O quiet Italian", ITALIAN_FEN, "e1g1"),
        ("obvious sacrifice: Bxh7+", GREEK_FEN, "d3h7"),
        ("obvious sacrifice: Nxh7", GREEK_FEN, "g5h7"),
        ("defensive consolidating: Be2", "4r1k1/5ppp/8/8/8/8/5PPP/4KB2 w - - 0 1", "f1e2"),
        ("king move not safer: Kf1", "4rrk1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1", "g1f1"),
    ]
    print("  real fixture values, raw -> normalized:")
    for name, fen, uci in cases:
        scores, _ = _scores(fen, uci)
        n = normalize_style_scores(scores)
        print(f"    {name:<30} atk {scores.attack_gain:+7.2f} -> {n['attack_gain']:+.4f}   "
              f"def {scores.defense_gain:+7.2f} -> {n['defense_gain']:+.4f}   "
              f"sac {n['sacrifice_signal']:.1f}  vol {n['volatility']:.2f}")
        if name.startswith("obvious attack"):
            assert 0.75 < n["attack_gain"] < 0.85, n["attack_gain"]
        elif name.startswith("castling"):
            assert 0.0 <= n["defense_gain"] < 0.01, n["defense_gain"]
        elif name.endswith("Bxh7+"):
            assert 0.10 < n["attack_gain"] < 0.20, n["attack_gain"]
            assert n["defense_gain"] == 0.0
        elif name.endswith("Nxh7"):
            assert -0.02 < n["attack_gain"] < 0.0, n["attack_gain"]
        elif name.startswith("defensive"):
            assert 0.25 < n["defense_gain"] < 0.32, n["defense_gain"]
        elif name.startswith("king move"):
            assert -0.30 < n["defense_gain"] < -0.25, n["defense_gain"]
    print("  [PASS] real fixture values map into sensible normalized ranges")


def test_phase_damping_is_selective():
    # Synthetic scores: attack_gain 13 = kzp 8 + kaa 2 + checks 1 + open 1 + esc 1.
    s = _synthetic(kzp=8.0, kaa=2.0, checks=1.0, open_lines=1.0, esc=1.0,
                   defense=2.0, sac=1.0, vol=0.5)
    d0 = _phase_damped_scores(s, 0.0)
    assert d0.attack_sub == s.attack_sub
    assert d0.attack_gain == s.attack_gain
    assert d0.sacrifice_signal == 1.0 and d0.volatility == 0.5
    assert d0.defense_gain == 2.0

    dm = _phase_damped_scores(s, 0.5)
    assert dm.attack_sub.king_zone_pressure == 4.0      # damped
    assert dm.attack_sub.king_adjacent_attacks == 1.0   # damped
    assert dm.attack_sub.checks == 1.0                  # NOT damped
    assert dm.attack_sub.open_lines == 1.0              # NOT damped
    assert dm.attack_sub.escape_square_pressure == 1.0  # NOT damped
    assert dm.attack_gain == 8.0                        # rebuilt from damped subs
    assert dm.sacrifice_signal == 1.0 and dm.volatility == 0.5  # never damped
    assert dm.defense_gain == 2.0                                # untouched here

    d1 = _phase_damped_scores(s, 1.0)
    assert d1.attack_sub.king_zone_pressure == 0.0
    assert d1.attack_sub.king_adjacent_attacks == 0.0
    assert d1.attack_sub.checks == 1.0
    assert d1.attack_sub.open_lines == 1.0
    assert d1.attack_sub.escape_square_pressure == 1.0
    assert d1.attack_gain == 3.0
    # original scores object must be untouched (pure function)
    assert s.attack_sub.king_zone_pressure == 8.0 and s.attack_gain == 13.0
    print("  [PASS] phase damping hits only kzp/kaa; tactical events + sac/vol untouched")


def test_persona_scores_bounded_and_differentiated():
    board = chess.Board(GREEK_FEN)
    move = chess.Move.from_uci("d3h7")
    scores, _ = compute_style_scores(board, move)
    a_bxh = attacker_score(scores, board)
    s_bxh = sacrificer_score(scores, board)
    assert -1.0 <= a_bxh <= 1.0 and -1.0 <= s_bxh <= 1.0
    assert a_bxh > 0.0 and s_bxh > 0.0        # both personas like the Greek gift
    assert s_bxh > a_bxh                      # sacrificer weights the sac far more

    board2 = chess.Board(MATE_FEN)
    move2 = chess.Move.from_uci("h6g7")
    scores2, _ = compute_style_scores(board2, move2)
    a_mate = attacker_score(scores2, board2)
    s_mate = sacrificer_score(scores2, board2)
    # mate-in-one: sacrifice_signal is 0.0 (mate-gated) but attack is maximal.
    # NOTE the mate fixture sits at phase~0.73 (Q+N vs R), so its king-zone
    # components (kzp +9, kaa +3) are damped to x0.27 inside the persona
    # function; the surviving checks+open+escape still give a clearly positive
    # attacker score that is several times the sacrificer's.
    assert a_mate > 0.35, a_mate
    assert s_mate < 0.3, s_mate
    assert a_mate > 2.0 * s_mate, (a_mate, s_mate)
    print(f"    Bxh7+: attacker={a_bxh:+.4f} sacrificer={s_bxh:+.4f} (correlated, sac-heavier)")
    print(f"    Qxg7#: attacker={a_mate:+.4f} sacrificer={s_mate:+.4f} (diverged, sac-gated)")
    print("  [PASS] both personas bounded; correlated on Bxh7+, diverged on the mate")


def test_phase_gating_end_to_end_in_persona_score():
    # The SAME StyleScores under Attacker: full weight in the opening
    # (phase 0.0), damped in a bare king-and-pawn ending (phase 1.0).
    s = _synthetic(kzp=8.0, kaa=2.0, checks=1.0, open_lines=1.0, esc=1.0, vol=0.3)
    opening = chess.Board()
    bare = chess.Board("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1")
    assert game_phase(opening) == 0.0
    assert game_phase(bare) == 1.0
    a_open = attacker_score(s, opening)
    a_bare = attacker_score(s, bare)
    s_open = sacrificer_score(s, opening)
    s_bare = sacrificer_score(s, bare)
    assert a_bare < a_open
    assert s_bare < s_open  # its 0.20 attack term damps; the 0.70 sac term cannot
    print(f"    attacker: phase0={a_open:+.4f} -> phase1={a_bare:+.4f} (damped)")
    print(f"    sacrificer: phase0={s_open:+.4f} -> phase1={s_bare:+.4f} (only its 0.20 attack term damps)")
    print("  [PASS] phase gating flows through the persona functions")


def test_persona_adjusted_score_scale_and_floor():
    # A fully-trusted maximal preference is worth PERSONA_BIAS_CP centipawns.
    assert persona_adjusted_score(0.0, 1.0, 0.0) == PERSONA_BIAS_CP
    # A maximally style-hated engine-best is demoted, but never past the
    # trust boundary (demotion floor).
    assert persona_adjusted_score(0.0, -1.0, 0.0) == -75.0
    # Mid-window gains are exactly PERSONA_BIAS_CP * trust(norm).
    norm = -20.0
    expected = norm + PERSONA_BIAS_CP * engine_trust(norm)
    got = persona_adjusted_score(norm, 1.0, 0.0)
    assert abs(got - expected) < 1e-9, (got, expected)
    print("  [PASS] bias scaled to cp (B=100); demotion floored at the trust boundary")


def test_persona_adjusted_score_trust_contract():
    # Rescue side: a candidate >75cp below best has trust EXACTLY 0, so its
    # own persona bias contributes exactly nothing AND its final stays its
    # norm (the floor must never LIFT a hard-rejected candidate).
    assert persona_adjusted_score(-80.0, 1.0, 0.0) == -80.0
    assert persona_adjusted_score(-75.0, 1.0, 0.0) == -75.0
    # Therefore even a maximally style-hated ENGINE BEST (floored at -75)
    # can never be overtaken by a >=75cp-worse candidate.
    hated_best = persona_adjusted_score(0.0, -1.0, 0.0)
    assert hated_best == -75.0
    assert persona_adjusted_score(-80.0, 1.0, 0.0) < hated_best
    assert persona_adjusted_score(-76.0, 1.0, 0.0) < hated_best
    # Mid-window rescue is bounded far short of the boundary even at bias 1.0.
    assert persona_adjusted_score(-40.0, 1.0, 0.0) < -27.0
    print("  [PASS] contract: nothing <= -75cp can ever win, regardless of persona score")


def test_persona_adjusted_score_real_flip_on_near_equal():
    # Deterministic ordering test on the near-equal fixture: REAL persona
    # scores for its moves with FIXED engine-relative gaps mirroring the
    # values that fixture actually produced across runs (Qd2 best; Ne5 -12;
    # a4 -5; Re1 -6; Qd3 -8).
    #
    # Assertions are anchored on DECISIVE margins only. The knife-edge case
    # (Qd3's 8.07cp bias against an exactly-8.00cp gap, final +0.07) is
    # printed informationally and deliberately NOT asserted: it sits within
    # MultiPV jitter and would flake if compute_style_scores moved by <1%.
    fen = "rnbq1rk1/ppp1ppbp/5np1/3p4/3P1B2/2N2NP1/PPP1PPBP/R2Q1RK1 w - - 0 1"
    board = chess.Board(fen)

    def persona_scores(uci):
        scores, _ = compute_style_scores(board, chess.Move.from_uci(uci))
        return attacker_score(scores, board), sacrificer_score(scores, board)

    a_qd2, _ = persona_scores("d1d2")
    a_ne5, s_ne5 = persona_scores("f3e5")
    a_a4, _ = persona_scores("a2a4")
    a_re1, _ = persona_scores("f1e1")
    a_qd3, s_qd3 = persona_scores("d1d3")
    assert a_qd2 == 0.0 and a_a4 == 0.0 and a_re1 == 0.0

    # ATTACKER (decisive, ~4.5-6.5cp margins): the persona-preferred Ne5
    # promotes PAST two zero-bias candidates despite sitting 12cp back.
    f_ne5 = persona_adjusted_score(-12.0, a_ne5, 0.0)
    f_a4 = persona_adjusted_score(-5.0, a_a4, 0.0)
    f_re1 = persona_adjusted_score(-6.0, a_re1, 0.0)
    assert f_ne5 > f_a4, (f_ne5, f_a4)
    assert f_ne5 > f_re1, (f_ne5, f_re1)
    # Robust no-flip: a 10cp gap exceeds Qd3's ~8.07cp bias (2.7cp slack).
    f_qd3_gap10 = persona_adjusted_score(-10.0, a_qd3, 0.0)
    assert f_qd3_gap10 < 0.0
    f_qd3_gap8 = persona_adjusted_score(-8.0, a_qd3, 0.0)  # knife-edge, print only
    # SACRIFICER (small biases): never promotes past the engine best.
    assert persona_adjusted_score(-8.0, s_qd3, 0.0) < 0.0
    assert persona_adjusted_score(-12.0, s_ne5, 0.0) < 0.0
    print(f"    A: Ne5 {f_ne5:+.2f} PROMOTED past a4 {f_a4:+.2f} and Re1 {f_re1:+.2f}")
    print(f"    A: Qd3 @gap-8 {f_qd3_gap8:+.2f} (knife-edge, not asserted) / @gap-10 {f_qd3_gap10:+.2f} (stays put)")
    print(f"    S: Qd3/Ne5 never promote past the leader")
    print("  [PASS] persona reorders near-equal candidates; bigger gaps stay put")


def test_phase_gating_on_real_mate_position():
    # REAL fixture position (not synthetic scores): the mate-in-one sits at
    # phase ~0.73, so its king-zone components (kzp +9, kaa +3) are damped
    # to x0.27 inside the persona functions while the concrete tactical
    # events (check, open line, escape squares) survive untouched. This
    # pins the damping behavior first observed in the live dump.
    fen = "5rk1/5ppp/7Q/5N2/8/8/5PPP/6K1 w - - 0 1"
    board = chess.Board(fen)
    scores, _ = compute_style_scores(board, chess.Move.from_uci("h6g7"))
    phase = game_phase(board)
    assert abs(phase - 0.7258) < 0.001, phase
    a_damped = attacker_score(scores, board)
    a_undamped = attacker_score(scores, chess.Board())  # same scores, phase 0
    assert a_undamped > a_damped
    assert a_damped > 0.35
    damped = _phase_damped_scores(scores, phase)
    assert abs(damped.attack_sub.king_zone_pressure - 9.0 * (1.0 - phase)) < 1e-9
    assert abs(damped.attack_sub.king_adjacent_attacks - 3.0 * (1.0 - phase)) < 1e-9
    assert damped.attack_sub.checks == 1.0
    assert damped.attack_sub.open_lines == 1.0
    assert damped.attack_sub.escape_square_pressure == 1.0
    print(f"    Qxg7# at phase {phase:.4f}: attacker {a_undamped:+.4f} (phase0) -> {a_damped:+.4f} (damped)")
    print("  [PASS] real mate position: zone terms damped, tactical terms survive")


def test_persona_ordering_differentiation_deterministic():
    # Deterministic differentiation on the sharp fixture (no engine needed):
    # the Sacrificer must prefer the REAL sacrifice (Nxf7) over the queen
    # sortie (Qh5); engine trust does the rest at rerank time.
    fen = "r1bqkb1r/ppp2ppp/2n5/3np1N1/2B5/8/PPPP1PPP/RNBQK2R w KQkq - 0 6"
    board = chess.Board(fen)

    def both(uci):
        scores, _ = compute_style_scores(board, chess.Move.from_uci(uci))
        return attacker_score(scores, board), sacrificer_score(scores, board)

    a_nxf7, s_nxf7 = both("g5f7")
    a_qh5, s_qh5 = both("d1h5")
    assert s_nxf7 > s_qh5
    # Documented quirk (by-design delta scoring, finding 4): the ATTACKER's
    # raw score prefers the queen sortie over the Fried Liver. Printed, not
    # asserted -- accepted behavior; engine trust is what keeps Qh5 from
    # ever mattering at rerank time.
    print(f"    Nxf7: A={a_nxf7:+.4f} S={s_nxf7:+.4f} | Qh5: A={a_qh5:+.4f} S={s_qh5:+.4f}")
    print("  [PASS] sacrificer prefers the real sacrifice over the queen sortie")


def test_defender_scores_bounded():
    # Bounded to [-1, 1] on ordinary inputs...
    board = chess.Board()
    s = _synthetic_def(epr=2.0, blk=1.0, mob=1.0, kzp=2.0, vol=0.5)
    d = defender_score(s, board)
    assert -1.0 <= d <= 1.0, d
    # ...and at the EXTREMES the final clamp is LOAD-BEARING for the Defender
    # (negative style weights -> affine, not convex, combination): a maximal
    # defense score must clamp to exactly 1, and a maximally defense-hated
    # sharp move to exactly -1. (These would exceed the range unclamped:
    # ~+1.2 and ~-1.24.)
    s_max = _synthetic_def(mob=100.0)
    assert defender_score(s_max, board) == 1.0
    s_min = _synthetic_def(epr=-100.0, kzp=20.0)
    assert defender_score(s_min, board) == -1.0
    print(f"    ordinary: {d:+.4f} | extremes clamp to exactly +/-1 (clamp is load-bearing)")
    print("  [PASS] defender bounded to [-1, 1]; clamp enforced at the extremes")


def test_defender_fixture_signs():
    # SIGN AGREEMENT with defense_gain's established signs on the same four
    # fixtures the earlier suites pin (persona_features's design list +
    # test_real_fixture_values): castling weakly positive, Be2 block strongly
    # positive, Kf1 negative, Bb2 development exactly 0.
    #
    # NOTE ON "CLEARLY POSITIVE" ON CASTLING: the extractor itself emits
    # defense_gain = +0.01 there (pawn_shield +1.00 vs king_mobility -1.0
    # nearly cancel -- the extractor's documented design). No weighting can
    # amplify what the extractor does not emit, so the honest expectation is
    # sign agreement (> 0), not a large magnitude; Be2 is the fixture that
    # demonstrates "clearly positive".
    #
    # FLIP-RISK COMMENT (required): the Defender's NEGATIVE attack/vol/sac
    # terms are the only thing that could fight defense_gain's sign. On
    # these four fixtures the combined penalties are -0.000 (castling: atk 0,
    # vol 0), -0.015 (Be2: atk +1.0, vol 0.11), -0.013 (Kf1: vol 0.13) and
    # -0.032 (Bb2: atk +4.8, vol 0.16) against defense terms of +0.001 /
    # +0.336 / -0.325 / 0.000 -- no flips. In general the attack term alone
    # would need tanh(atk/14) > 24x tanh(def/14) to flip a candidate
    # (defense < ~0.6 raw AND attack > ~+17 raw; see the weight-block
    # comment) -- the "attacking move dressed as defense" case, where the
    # mild dislike is INTENDED. Bb2 is the benign version: a quiet
    # developing move with a small attack swing lands slightly negative
    # (near 0), which is exactly the Defender's stated preference for calm
    # over development-with-sharpness.
    cases = [
        # (label, fen, uci, def_sign_expectation, defender assertion)
        ("castling O-O", "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4",
         "e1g1", "positive(weak)", "gt0"),
        ("defensive Be2 block", "4r1k1/5ppp/8/8/8/8/5PPP/4KB2 w - - 0 1",
         "f1e2", "positive(strong)", "gt025"),
        ("Kf1 walks into open e-file", "4rrk1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1",
         "g1f1", "negative", "lt_neg025"),
        ("Bb2 develops, defends nothing", "6k1/pppppppp/8/8/8/8/P1PPPPPP/2B2RK1 w - - 0 1",
         "c1b2", "zero", "le0"),
    ]
    for label, fen, uci, def_sign, assertion in cases:
        board = chess.Board(fen)
        scores, _ = compute_style_scores(board, chess.Move.from_uci(uci))
        d = defender_score(scores, board)
        sign_ok = (
            (def_sign.startswith("positive") and scores.defense_gain > 0)
            or (def_sign == "negative" and scores.defense_gain < 0)
            or (def_sign == "zero" and scores.defense_gain == 0.0)
        )
        assert sign_ok, (label, scores.defense_gain)
        if assertion == "gt0":
            assert d > 0.0, (label, d)
        elif assertion == "gt025":
            assert d > 0.25, (label, d)
        elif assertion == "lt_neg025":
            assert d < -0.25, (label, d)
        elif assertion == "le0":
            assert d <= 0.0, (label, d)
        print(f"    {label:<28} def={scores.defense_gain:+6.2f} -> defender={d:+.4f}  [{def_sign}]")
    print("  [PASS] defender signs agree with defense_gain's established signs on all four")


def test_defender_phase_gating_real_position():
    # REAL position ("active endgame king"), NOT synthetic scores, with THREE
    # nonzero defense subcomponents (kzd +0.021, shd -1.00, mob +3.0). The
    # Defender's damping decisions imply: the STATIC subs (king_zone_defense,
    # pawn_shield) go to zero at phase 1 while the concrete deltas and king
    # mobility survive untouched. Here that means the shelter "penalty"
    # (shd -1.00, correct-for-endgame noise the fixture documents) VANISHES
    # while the +3.0 king activation keeps full weight -> the expected
    # direction is defender score RISING from phase 0 to phase 1.
    fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    board = chess.Board(fen)
    scores, _ = compute_style_scores(board, chess.Move.from_uci("e1d2"))
    phase = game_phase(board)
    assert abs(phase - 1.0) < 1e-9, phase
    d_end = defender_score(scores, board)
    d_open = defender_score(scores, chess.Board())  # same scores, phase 0
    assert d_end > d_open, (d_open, d_end)
    # Sub-level pinning of the damping decisions:
    damped = _defender_phase_damped_scores(scores, phase)
    assert damped.defense_sub.king_zone_defense == 0.0   # static -> damped
    assert damped.defense_sub.pawn_shield == 0.0         # static -> damped
    assert damped.defense_sub.enemy_pressure_reduction == 0.0  # was already 0
    assert damped.defense_sub.line_blocking == 0.0             # was already 0
    assert damped.defense_sub.king_mobility == scores.defense_sub.king_mobility  # NEVER damped
    assert damped.defense_gain == damped.defense_sub.king_mobility  # rebuilt invariant
    # At phase 0 nothing is damped and the originals survive (pure function).
    undamped = _defender_phase_damped_scores(scores, 0.0)
    assert undamped.defense_sub == scores.defense_sub
    assert undamped.defense_gain == scores.defense_gain
    assert undamped.attack_sub == scores.attack_sub
    print(f"    Kd2 endgame (phase {phase:.2f}): defender phase0={d_open:+.4f} -> phase1={d_end:+.4f}"
          " (shield penalty removed, mobility survives)")
    # CONTRAST print (no assert): the Be2 block's defense mass sits in
    # UNDAMPED subs (epr +2.00, blk 1.0), so phase damping barely moves it
    # (def +4.06 -> +4.01) -- concrete defense survives deep into the
    # endgame, exactly per the taxonomy.
    be2 = chess.Board("4r1k1/5ppp/8/8/8/8/5PPP/4KB2 w - - 0 1")
    be2_scores, _ = compute_style_scores(be2, chess.Move.from_uci("f1e2"))
    d_be2 = defender_score(be2_scores, be2)
    d_be2_open = defender_score(be2_scores, chess.Board())
    print(f"    Be2 block (phase {game_phase(be2):.2f}): defender phase0={d_be2_open:+.4f}"
          f" -> real={d_be2:+.4f} (barely moves: its defense is concrete, not static)")
    print("  [PASS] defense-side phase gating: static subs damp, deltas + mobility survive")


def test_defender_differentiation_on_near_equal():
    # Requirement: on the near-equal fixture, Defender must produce a
    # genuinely different ranking from BOTH Attacker and Sacrificer.
    # Deterministic setup mirroring test_persona_adjusted_score_real_flip_on_
    # near_equal: REAL scores (pure extractor) + FIXED engine-relative gaps
    # that fixture actually produced across runs (Qd2 best; Ne5 -12; a4 -5;
    # Re1 -6; Qd3 -8).
    fen = "rnbq1rk1/ppp1ppbp/5np1/3p4/3P1B2/2N2NP1/PPP1PPBP/R2Q1RK1 w - - 0 1"
    board = chess.Board(fen)

    def all_personas(uci):
        scores, _ = compute_style_scores(board, chess.Move.from_uci(uci))
        return (
            attacker_score(scores, board),
            sacrificer_score(scores, board),
            defender_score(scores, board),
        )

    a_qd2, s_qd2, d_qd2 = all_personas("d1d2")
    a_ne5, s_ne5, d_ne5 = all_personas("f3e5")
    a_a4, s_a4, d_a4 = all_personas("a2a4")
    a_re1, s_re1, d_re1 = all_personas("f1e1")
    a_qd3, s_qd3, d_qd3 = all_personas("d1d3")

    # The swing candidate is Re1: it vacates f1 (tiny coverage loss) but
    # FREES the king's f1 square (+1 mobility) -> small POSITIVE defender
    # score, while Attacker and Sacrificer both score it exactly 0.
    assert d_re1 > 0.0, d_re1
    assert a_re1 == 0.0 and s_re1 == 0.0
    # And Ne5 (the only sharp candidate): Attacker actively likes it,
    # Defender mildly dislikes it -- a genuine SIGN divergence.
    assert a_ne5 > 0.0 > d_ne5, (a_ne5, d_ne5)

    fD_qd2 = persona_adjusted_score(0.0, d_qd2, 0.0)
    fD_ne5 = persona_adjusted_score(-12.0, d_ne5, 0.0)
    fD_a4 = persona_adjusted_score(-5.0, d_a4, 0.0)
    fD_re1 = persona_adjusted_score(-6.0, d_re1, 0.0)
    fD_qd3 = persona_adjusted_score(-8.0, d_qd3, 0.0)
    # Defender's ranking: Re1 PROMOTED past a4 (engine rank #3 -> #2), Ne5
    # demoted to LAST. HONEST LIMIT, pinned deliberately: Re1's +7.54 raw
    # preference is attenuated by trust(-6) = 0.731 to +5.5cp -- 0.5cp SHORT
    # of the 6cp gap -- so it does NOT overtake the engine best Qd2. (The
    # trust curve is doing its job: a 6cp-worse move cannot be promoted on a
    # 0.075-strength persona signal alone.)
    assert fD_qd2 > fD_re1 > fD_a4 > fD_qd3 > fD_ne5, (
        fD_qd2, fD_re1, fD_a4, fD_qd3, fD_ne5,
    )
    # Same candidates, OPPOSITE orderings under the other two personas --
    # a different pairwise flip against EACH of them:
    #   vs Attacker:    Ne5-vs-Re1 is flipped (Attacker: sharp move above
    #                   the rook; Defender: rook activation above sharpness)
    #   vs Sacrificer:  a4-vs-Re1 is flipped (Sacrificer keeps engine order,
    #                   Defender promotes the rook activation)
    fA_ne5 = persona_adjusted_score(-12.0, a_ne5, 0.0)
    fA_re1 = persona_adjusted_score(-6.0, a_re1, 0.0)
    fS_ne5 = persona_adjusted_score(-12.0, s_ne5, 0.0)
    fS_re1 = persona_adjusted_score(-6.0, s_re1, 0.0)
    fS_a4 = persona_adjusted_score(-5.0, s_a4, 0.0)
    assert fA_ne5 > fA_re1, (fA_ne5, fA_re1)   # Attacker: sharp move above the rook
    assert fS_a4 > fS_re1, (fS_a4, fS_re1)     # Sacrificer: engine order kept
    assert fD_ne5 < fD_re1, (fD_ne5, fD_re1)   # Defender: rook activation above sharpness
    assert fD_re1 > fD_a4, (fD_re1, fD_a4)     # Defender: the pairwise flip vs Sacrificer
    print(f"    Re1: A={a_re1:+.4f} S={s_re1:+.4f} D={d_re1:+.4f}"
          " -> only the DEFENDER gives it a positive bias (+5.5cp after trust)")
    print(f"    Ne5: A={a_ne5:+.4f} vs D={d_ne5:+.4f} (sign divergence on the sharp candidate)")
    print(f"    Defender order:   Qd2 {fD_qd2:+.2f} > Re1 {fD_re1:+.2f} > a4 {fD_a4:+.2f}"
          f" > Qd3 {fD_qd3:+.2f} > Ne5 {fD_ne5:+.2f}  (Re1 #3 -> #2; 0.5cp short of Qd2)")
    print(f"    Attacker order:   Qd2 > Ne5 {fA_ne5:+.2f} > a4 > Re1 {fA_re1:+.2f} > Qd3 (knife-edge)")
    print(f"    Sacrificer order: Qd2 > a4 {fS_a4:+.2f} > Qd3 > Re1 {fS_re1:+.2f} > Ne5 {fS_ne5:+.2f}")
    print("  [PASS] Defender ranking differs from both Attacker and Sacrificer"
          " (Re1/a4 reorder + Ne5-vs-Re1 flipped)")


def test_positional_scores_bounded():
    # Bounded to [-1, 1] on ordinary and extreme inputs. With all-negative
    # weights the practical range is ~[-0.78, 0]: the theoretical floor of
    # -0.80 requires ALL THREE inputs maxed AND attack_gain's tanh to
    # SATURATE (|atk| >= ~266 -- absurd), and the theoretical +0.20 ceiling
    # (pure de-escalation) is unreachable because a big attack DROP raises
    # volatility at a rate that always outweighs the credit. So the clamp is
    # VESTIGIAL here -- unlike the Defender, where it is load-bearing.
    # (Checked: it is not needed.)
    board = chess.Board()
    s = _synthetic(kzp=8.0, kaa=2.0, checks=1.0, open_lines=1.0, esc=1.0,
                   vol=0.5, sac=1.0)
    p_sharp = positional_score(s, board)
    assert -1.0 <= p_sharp <= 1.0 and p_sharp < 0.0
    # Perfectly quiet move -> EXACTLY 0 (the neutral anchor this persona
    # lacks by design; a pin, not an accident).
    p_quiet = positional_score(_synthetic(), board)
    assert p_quiet == 0.0, p_quiet
    # Extreme sharpness (absurd inputs): approaches but does NOT cross -1.
    s_max = _synthetic(kzp=200.0, vol=1.0, sac=1.0)
    p_extreme = positional_score(s_max, board)
    assert -1.0 <= p_extreme < 0.0, p_extreme
    # De-escalation co-firing proof: attack_gain -8 forces volatility >= 1/3
    # (the pressure-swing term |delta|/6 feeds the average), and the vol
    # penalty rate (0.48/18 per raw point) always exceeds the atk credit rate
    # (0.20/14) -- so the "reward retreats" loophole is closed by construction.
    s_deesc = _synthetic(kzp=-8.0, vol=1.0 / 3.0)
    assert positional_score(s_deesc, board) < 0.0
    print(f"    sharp: {p_sharp:+.4f} | quiet: {p_quiet:+.4f} (exact 0 anchor) | "
          f"extreme: {p_extreme:+.4f} (no clamp hit)")
    print(f"    de-escalator (atk -8, vol 1/3): {positional_score(s_deesc, board):+.4f} (net penalty)")
    print("  [PASS] positional bounded; clamp is vestigial; quiet == exactly 0")


def test_positional_fixture_contrasts():
    # REAL fixtures, no engine: Positional must score the SHARP top
    # candidates (Bxh7+, Nxf7) lower than ALL THREE existing personas do,
    # score the perfect-quiet castling move at exactly 0, and penalize
    # development-with-threat (Bb2).
    board = chess.Board(GREEK_FEN)
    scores, _ = compute_style_scores(board, chess.Move.from_uci("d3h7"))
    a, x, d, p = (attacker_score(scores, board), sacrificer_score(scores, board),
                  defender_score(scores, board), positional_score(scores, board))
    assert p < d < 0.0 < a and p < 0.0 < x, (a, x, d, p)
    print(f"    Bxh7+: A={a:+.4f} S={x:+.4f} D={d:+.4f} P={p:+.4f} (P lowest by far)")

    board2 = chess.Board("r1bqkb1r/ppp2ppp/2n5/3np1N1/2B5/8/PPPP1PPP/RNBQK2R w KQkq - 0 6")
    scores2, _ = compute_style_scores(board2, chess.Move.from_uci("g5f7"))
    a2, x2, d2, p2 = (attacker_score(scores2, board2), sacrificer_score(scores2, board2),
                      defender_score(scores2, board2), positional_score(scores2, board2))
    assert p2 < d2 < 0.0 < a2 and p2 < 0.0 < x2, (a2, x2, d2, p2)
    print(f"    Nxf7:  A={a2:+.4f} S={x2:+.4f} D={d2:+.4f} P={p2:+.4f} (P lowest by far)")

    board3 = chess.Board(ITALIAN_FEN)
    scores3, _ = compute_style_scores(board3, chess.Move.from_uci("e1g1"))
    p3 = positional_score(scores3, board3)
    assert p3 == 0.0, p3  # castling: atk 0, vol 0, sac 0 -> the exact quiet anchor
    print(f"    O-O quiet anchor: P={p3:+.4f} (exact 0, like all fully-quiet moves)")

    board4 = chess.Board("6k1/pppppppp/8/8/8/8/P1PPPPPP/2B2RK1 w - - 0 1")
    scores4, _ = compute_style_scores(board4, chess.Move.from_uci("c1b2"))
    p4 = positional_score(scores4, board4)
    assert p4 < 0.0, p4  # development that also creates threats is not "quiet"
    print(f"    Bb2 develop-with-threat: P={p4:+.4f} (penalized, unlike D's ~0)")
    print("  [PASS] positional contrasts: sharp tops lowest of all four personas")


def test_positional_differentiation_on_near_equal():
    # Requirement: on the near-equal fixture, Positional must produce a
    # genuinely different ranking from all three existing personas -- OR the
    # honest reason it coincides. Deterministic setup (real scores + the
    # same fixed gaps as the other tests: Qd2 best; Ne5 -12; a4 -5; Re1 -6;
    # Qd3 -8). HONEST MECHANISM, stated up front: all five candidates are
    # quiet except Ne5/Qd3, and Re1/a4/Qd2 have ZERO positional signal (no
    # sharpness to penalize, defense weighted 0) -> they tie at exactly 0
    # and Positional's ranking FALLS BACK TO CP ORDER. It differs from each
    # persona via an asserted pairwise flip, not by adding a new preference.
    fen = "rnbq1rk1/ppp1ppbp/5np1/3p4/3P1B2/2N2NP1/PPP1PPBP/R2Q1RK1 w - - 0 1"
    board = chess.Board(fen)

    def all_personas(uci):
        scores, _ = compute_style_scores(board, chess.Move.from_uci(uci))
        return (
            attacker_score(scores, board),
            sacrificer_score(scores, board),
            defender_score(scores, board),
            positional_score(scores, board),
        )

    a_qd2, s_qd2, d_qd2, p_qd2 = all_personas("d1d2")
    a_ne5, s_ne5, d_ne5, p_ne5 = all_personas("f3e5")
    a_a4, s_a4, d_a4, p_a4 = all_personas("a2a4")
    a_re1, s_re1, d_re1, p_re1 = all_personas("f1e1")
    a_qd3, s_qd3, d_qd3, p_qd3 = all_personas("d1d3")

    # The "no opinion on quiet moves" property, pinned: zero sharpness in,
    # exactly zero out -- Positional expresses NOTHING on a4/Re1/Qd2.
    assert p_qd2 == 0.0 and p_a4 == 0.0 and p_re1 == 0.0
    # Only the sharp candidates get penalized.
    assert p_ne5 < p_qd3 < 0.0, (p_ne5, p_qd3)

    fP_qd2 = persona_adjusted_score(0.0, p_qd2, 0.0)
    fP_ne5 = persona_adjusted_score(-12.0, p_ne5, 0.0)
    fP_a4 = persona_adjusted_score(-5.0, p_a4, 0.0)
    fP_re1 = persona_adjusted_score(-6.0, p_re1, 0.0)
    fP_qd3 = persona_adjusted_score(-8.0, p_qd3, 0.0)
    # Positional's ranking == cp order (the honest fallback), with the two
    # sharp candidates pushed further down.
    assert fP_qd2 > fP_a4 > fP_re1 > fP_qd3 > fP_ne5, (
        fP_qd2, fP_a4, fP_re1, fP_qd3, fP_ne5,
    )
    # And it still differs from EACH persona via a pairwise flip:
    #   vs Attacker:   Ne5-vs-Re1 flipped (Attacker: sharp above rook)
    #   vs Sacrificer: Qd3-vs-Re1 is flipped (Sacrificer's small positive
    #                  bias lifts Qd3 above Re1; Positional's penalty sinks
    #                  Qd3 below it)
    #   vs Defender:   a4-vs-Re1 is flipped (Defender promotes Re1 past a4)
    fA_ne5 = persona_adjusted_score(-12.0, a_ne5, 0.0)
    fA_re1 = persona_adjusted_score(-6.0, a_re1, 0.0)
    fS_qd3 = persona_adjusted_score(-8.0, s_qd3, 0.0)
    fS_re1 = persona_adjusted_score(-6.0, s_re1, 0.0)
    fD_re1 = persona_adjusted_score(-6.0, d_re1, 0.0)
    fD_a4 = persona_adjusted_score(-5.0, d_a4, 0.0)
    assert fA_ne5 > fA_re1 and fP_re1 > fP_ne5, (fA_ne5, fA_re1, fP_re1, fP_ne5)
    assert fS_qd3 > fS_re1 and fP_re1 > fP_qd3, (fS_qd3, fS_re1, fP_re1, fP_qd3)
    assert fD_re1 > fD_a4 and fP_a4 > fP_re1, (fD_re1, fD_a4, fP_a4, fP_re1)
    print(f"    sharp-candidate penalties: Ne5 P={p_ne5:+.4f}, Qd3 P={p_qd3:+.4f};"
          " quiet candidates all P=+0.0000 (fallback to cp order)")
    print(f"    Positional order:   Qd2 {fP_qd2:+.2f} > a4 {fP_a4:+.2f} > Re1 {fP_re1:+.2f}"
          f" > Qd3 {fP_qd3:+.2f} > Ne5 {fP_ne5:+.2f}  (= engine order)")
    print(f"    pairwise flips: Ne5/Re1 vs Attacker; Qd3/Re1 vs Sacrificer; a4/Re1 vs Defender")
    print("  [PASS] Positional differs from all three personas"
          " (cp-order fallback + asserted pairwise flip vs each)")


def test_gambiter_weights_are_the_declared_blend():
    # The weight block claims Gambiter is literally 0.7*Attacker +
    # 0.3*Sacrificer. This is asserted against the parents' LIVE constants,
    # not trusted: if either parent's vector is retuned, this fails and
    # forces a conscious re-blend instead of silent drift.
    blend_attack = 0.7 * _ATTACKER_W_ATTACK + 0.3 * _SACRIFICER_W_ATTACK
    blend_vol = 0.7 * _ATTACKER_W_VOLATILITY + 0.3 * _SACRIFICER_W_VOLATILITY
    blend_sac = 0.7 * _ATTACKER_W_SACRIFICE + 0.3 * _SACRIFICER_W_SACRIFICE
    blend_def = 0.7 * _ATTACKER_W_DEFENSE + 0.3 * _SACRIFICER_W_DEFENSE
    blend_init = 0.7 * _ATTACKER_W_INITIATIVE + 0.3 * _SACRIFICER_W_INITIATIVE
    for label, got, want in (
        ("attack_gain", _GAMBITER_W_ATTACK, blend_attack),
        ("volatility", _GAMBITER_W_VOLATILITY, blend_vol),
        ("sacrifice_signal", _GAMBITER_W_SACRIFICE, blend_sac),
        ("defense_gain", _GAMBITER_W_DEFENSE, blend_def),
        ("initiative_proxy", _GAMBITER_W_INITIATIVE, blend_init),
    ):
        assert abs(got - want) < 1e-12, (label, got, want)
    print(f"    recomputed from LIVE parent constants: "
          f"attack {blend_attack:.4f} vol {blend_vol:.4f} sac {blend_sac:.4f} "
          f"def {blend_def:.4f} init {blend_init:.4f}")
    assert abs(_GAMBITER_W_ATTACK - 0.55) < 1e-12
    assert abs(_GAMBITER_W_VOLATILITY - 0.17) < 1e-12
    assert abs(_GAMBITER_W_SACRIFICE - 0.28) < 1e-12
    assert _GAMBITER_W_DEFENSE == 0.0 and _GAMBITER_W_INITIATIVE == 0.0
    total = (_GAMBITER_W_ATTACK + _GAMBITER_W_VOLATILITY
             + _GAMBITER_W_SACRIFICE + _GAMBITER_W_DEFENSE + _GAMBITER_W_INITIATIVE)
    assert abs(total - 1.0) < 1e-12, total
    assert all(w >= 0.0 for w in (_GAMBITER_W_ATTACK, _GAMBITER_W_VOLATILITY,
                                  _GAMBITER_W_SACRIFICE, _GAMBITER_W_DEFENSE,
                                  _GAMBITER_W_INITIATIVE))
    print(f"    declared literals hit (0.55/0.17/0.28/0/0); sum={total!r}; all non-negative")
    print("  [PASS] Gambiter weights are the exact 0.7/0.3 blend, sum 1.0, non-negative")


def test_gambiter_scores_bounded_and_clamp_vestigial():
    # Bounded on ordinary inputs...
    board = chess.Board()
    s = _synthetic(kzp=8.0, kaa=2.0, checks=1.0, open_lines=1.0, esc=1.0,
                   vol=0.5, sac=1.0)
    g = gambiter_score(s, board)
    assert -1.0 <= g <= 1.0, g
    # ...and the clamp is VESTIGIAL (not load-bearing) at the extremes: the
    # theoretical bound of |total| is exactly 0.55+0.17+0.28 = 1.0 with all
    # inputs at their endpoints, and attack_gain's tanh never reaches 1.0
    # below raw ~+266, so even an absurd input cannot push the total past
    # the range. Proven here by asserting the output equals the UNCLAMPED
    # analytic value and lies strictly inside (-1, 1) on both extremes.
    extreme_pos = _synthetic(kzp=200.0, vol=1.0, sac=1.0)
    extreme_neg = _synthetic(kzp=-200.0, vol=1.0, sac=1.0)
    for label, s_ext in (("max-sharp", extreme_pos), ("max-anti-sharp", extreme_neg)):
        got = gambiter_score(s_ext, board)
        phase = game_phase(board)
        n = normalize_style_scores(_phase_damped_scores(s_ext, phase))
        unclamped = (
            _GAMBITER_W_ATTACK * n["attack_gain"]
            + _GAMBITER_W_VOLATILITY * n["volatility"]
            + _GAMBITER_W_SACRIFICE * n["sacrifice_signal"]
            + _GAMBITER_W_DEFENSE * n["defense_gain"]
            + _GAMBITER_W_INITIATIVE * n["initiative_proxy"]
        )
        assert got == unclamped, (label, got, unclamped)  # clamp did NOT bind
        assert -1.0 < got < 1.0, (label, got)
        print(f"    {label}: got={got:+.15f} == unclamped total (no clamp hit)")
    # Blend identity holds wherever all three clamps are vestigial (both
    # parents' clamps are vestigial for the same reason): g == 0.7a + 0.3s.
    a = attacker_score(extreme_pos, board)
    x = sacrificer_score(extreme_pos, board)
    g2 = gambiter_score(extreme_pos, board)
    assert abs(g2 - (0.7 * a + 0.3 * x)) < 1e-12, (a, x, g2)
    print(f"    blend identity at the extreme: G={g2:+.12f} == 0.7*A({a:+.15f})"
          f" + 0.3*S({x:+.15f}) (|err| < 1e-12)")
    print("  [PASS] gambiter bounded on ordinary + extreme inputs; clamp verified vestigial")


def test_gambiter_blend_between_attacker_and_sacrificer():
    # REAL fixtures, sharp top candidates: the blend must land strictly
    # between the two parent scores -- and, since both parents and the
    # blend consume the SAME normalized inputs, the identity
    # g == 0.7*a + 0.3*s must hold EXACTLY (no clamp binds on any of the
    # three). Reported with real numbers per the task.
    cases = [
        ("obvious sacrifice", GREEK_FEN, "Bxh7+", "d3h7"),
        ("obvious sacrifice", GREEK_FEN, "Nxh7", "g5h7"),
        ("sharp tactical no quiet alternative",
         "r1bqkb1r/ppp2ppp/2n5/3np1N1/2B5/8/PPPP1PPP/RNBQK2R w KQkq - 0 6",
         "Nxf7", "g5f7"),
        ("sharp tactical no quiet alternative",
         "r1bqkb1r/ppp2ppp/2n5/3np1N1/2B5/8/PPPP1PPP/RNBQK2R w KQkq - 0 6",
         "Qh5", "d1h5"),
    ]
    for fname, fen, san, uci in cases:
        board = chess.Board(fen)
        scores, _ = compute_style_scores(board, chess.Move.from_uci(uci))
        a = attacker_score(scores, board)
        x = sacrificer_score(scores, board)
        g = gambiter_score(scores, board)
        exact = 0.7 * a + 0.3 * x
        assert abs(g - exact) < 1e-12, (fname, san, g, exact)
        lo, hi = min(a, x), max(a, x)
        assert lo <= g <= hi, (fname, san, a, x, g)
        if a != x:
            assert lo < g < hi, (fname, san, a, x, g)
        print(f"    {fname:<36} {san:<5} A={a:+.6f} S={x:+.6f} G={g:+.6f}"
              f" (identity exact to 1e-12; strictly between the parents)")
    print("  [PASS] blend lands between attacker_score and sacrificer_score"
          " on all four sharp candidates (exact linear identity)")


def test_gambiter_differentiation_on_near_equal():
    # Requirement: on the near-equal fixture, Gambiter must produce a
    # genuinely different ranking from ALL FOUR existing personas -- or the
    # honest reason it coincides. Deterministic setup (real extractor scores
    # + the same fixed engine-relative gaps the other differentiation tests
    # use: Qd2 best; Ne5 -12; a4 -5; Re1 -6; Qd3 -8). The real numbers: the
    # two sharp-ish candidates (Ne5, Qd3) get small positive blended scores
    # (Ne5 G=+0.1722, Qd3 G=+0.0971) while the quiet ones score exactly 0,
    # so Gambiter promotes BOTH past the quiet a4/Re1 but the blend's
    # attenuated biases never reach the engine best -> a UNIQUE ranking
    # (Qd2 > Qd3 > Ne5 > a4 > Re1; the only persona with Qd3 second).
    fen = "rnbq1rk1/ppp1ppbp/5np1/3p4/3P1B2/2N2NP1/PPP1PPBP/R2Q1RK1 w - - 0 1"
    board = chess.Board(fen)

    def all_five(uci):
        scores, _ = compute_style_scores(board, chess.Move.from_uci(uci))
        return (
            attacker_score(scores, board),
            sacrificer_score(scores, board),
            defender_score(scores, board),
            positional_score(scores, board),
            gambiter_score(scores, board),
        )

    gaps = {"d1d2": 0.0, "f3e5": -12.0, "a2a4": -5.0, "f1e1": -6.0, "d1d3": -8.0}
    sans = {"d1d2": "Qd2", "f3e5": "Ne5", "a2a4": "a4", "f1e1": "Re1", "d1d3": "Qd3"}
    finals = {}
    finals_by_key = {"A": {}, "S": {}, "D": {}, "P": {}, "G": {}}
    for uci, gap in gaps.items():
        a, x, d, p, g = all_five(uci)
        for key, score in (("A", a), ("S", x), ("D", d), ("P", p), ("G", g)):
            finals_by_key[key][sans[uci]] = persona_adjusted_score(gap, score, 0.0)
        finals[sans[uci]] = (a, x, d, p, g)

    g_order = sorted(
        sans.values(),
        key=lambda san: -finals_by_key["G"][san],
    )
    others = {key: sorted(sans.values(), key=lambda s: -finals_by_key[key][s])
              for key in ("A", "S", "D", "P")}
    # Genuinely different from ALL FOUR:
    assert g_order == ["Qd2", "Qd3", "Ne5", "a4", "Re1"], g_order
    for key in ("A", "S", "D", "P"):
        assert g_order != others[key], (key, g_order, others[key])
    # The blend linearity carries through the rerank: same gap -> same
    # trust -> final_G == 0.7*final_A + 0.3*final_S exactly per candidate.
    for san in sans.values():
        want = 0.7 * finals_by_key["A"][san] + 0.3 * finals_by_key["S"][san]
        assert abs(finals_by_key["G"][san] - want) < 1e-9, (san, finals_by_key["G"][san], want)
    # And a decisive pairwise flip against each persona, with real numbers:
    #   vs Attacker:   Qd2/Qd3 flipped (Attacker's Qd3 bias is the 8.07cp
    #                  knife-edge that outranks the engine best; Gambiter's
    #                  attenuated blend (G_Qd3 = +0.0971 -> 6.2cp pull) does
    #                  NOT reach it, so the engine best stays on top)
    #   vs Sacrificer: Qd3/a4 flipped (Sacrificer keeps a4 above Qd3;
    #                  Gambiter promotes Qd3 past it)
    #   vs Defender:   Ne5/Re1 flipped (Defender sinks the sharp candidate
    #                  to last; Gambiter promotes it past the rook)
    #   vs Positional: Ne5/a4 flipped for the same reason
    fG, fA, fS, fD, fP = (finals_by_key[k] for k in ("G", "A", "S", "D", "P"))
    assert fA["Qd3"] > fA["Qd2"] and fG["Qd2"] > fG["Qd3"], (fA["Qd3"], fA["Qd2"], fG)
    assert fS["a4"] > fS["Qd3"] and fG["Qd3"] > fG["a4"], (fS["a4"], fS["Qd3"], fG)
    assert fD["Re1"] > fD["Ne5"] and fG["Ne5"] > fG["Re1"], (fD["Re1"], fD["Ne5"], fG)
    assert fP["a4"] > fP["Ne5"] and fG["Ne5"] > fG["a4"], (fP["a4"], fP["Ne5"], fG)
    for key in ("A", "S", "D", "P", "G"):
        order = sorted(sans.values(), key=lambda s: -finals_by_key[key][s])
        print(f"    {key} order: " + " > ".join(f"{s}({finals_by_key[key][s]:+.2f})"
                                                for s in order))
    print("    Gambiter ranking is UNIQUE among the five personas"
          " (Qd2 > Qd3 > Ne5 > a4 > Re1); pairwise flip asserted vs each")
    print("  [PASS] Gambiter's near-equal ranking differs from all four existing personas")


def test_gambiter_quiet_positional_fallback():
    # Requirement (quiet positional middlegame fixture): Gambiter must
    # produce a genuinely different ranking from all four personas OR the
    # honest reason it coincides -- explained with real numbers. This is the
    # coincidence case, quantified: three of the five candidates (h6, a5,
    # a6) are PERFECTLY quiet (atk 0, vol 0, sac 0) -> exactly-zero persona
    # scores -> zero bias; the other two (Bb6, Kh8) carry only tiny blended
    # biases whose trust-attenuated pull (1.53cp / 0.74cp at the fixture's
    # real gaps) is far short of the 2-4cp engine gaps -> the ranking FALLS
    # BACK TO CP ORDER, coinciding with Attacker/Sacrificer/Positional and
    # differing from Defender (whose bigger defense score on Kh8 DOES
    # promote it -- the saved-harness divergence).
    fen = "r1bq1rk1/ppp2ppp/2np1n2/2b1p3/2B1P3/2PP1N2/PP3PPP/RNBQ1RK1 b - - 0 1"
    board = chess.Board(fen)
    saved_gaps = {"h6": 0.0, "a5": -12.0, "a6": -16.0, "Bb6": -18.0, "Kh8": -18.0}
    finals_g, finals_d = {}, {}
    for san, gap in saved_gaps.items():
        scores, _ = compute_style_scores(board, board.parse_san(san))
        g = gambiter_score(scores, board)
        d = defender_score(scores, board)
        finals_g[san] = persona_adjusted_score(gap, g, game_phase(board))
        finals_d[san] = persona_adjusted_score(gap, d, game_phase(board))
        if san in ("h6", "a5", "a6"):
            assert g == 0.0, (san, g)  # perfectly quiet -> exactly zero blend
        else:
            print(f"    {san}: G={g:+.6f} -> bias pull "
                  f"{(finals_g[san] - gap):+.2f}cp vs the {abs(gap - saved_gaps['a6']):.0f}cp gap to a6"
                  f" (cannot flip; final {finals_g[san]:+.2f})")
    order_g = sorted(saved_gaps, key=lambda s: -finals_g[s])
    assert order_g == ["h6", "a5", "a6", "Bb6", "Kh8"], order_g
    print(f"    Gambiter order: " + " > ".join(f"{s}({finals_g[s]:+.2f})" for s in order_g)
          + "  == cp order (coincides with A/S/P)")
    print(f"    Defender order: " + " > ".join(f"{s}({finals_d[s]:+.2f})" for s in sorted(saved_gaps, key=lambda s: -finals_d[s])))
    print("  [PASS] Gambiter coincides with cp order on the all-quiet fixture,"
          " with the coincidence quantified (0-score anchors + sub-2cp pulls)")


def test_canonicalize_by_score_fixes_multipv_order():
    # MOCKED suggest() output (deterministic, no live engine): modeled
    # directly on the real inversion observed in game 0 move 10 of the
    # user's Lichess games -- Bh3+ (101) listed BEFORE Nh3 (111) at
    # adjacent MultiPV ranks. The scores are fine; only list order is wrong.
    raw = [
        {"uci": "d4d5", "san": "d5", "score_cp": 155},
        {"uci": "h2h4", "san": "h4", "score_cp": 148},
        {"uci": "g1e2", "san": "Ne2", "score_cp": 141},
        {"uci": "c1h3", "san": "Bh3+", "score_cp": 101},  # LOWER cp listed first
        {"uci": "g1h3", "san": "Nh3", "score_cp": 111},
    ]
    canon = canonicalize_by_score(raw)
    assert [s["san"] for s in canon] == ["d5", "h4", "Ne2", "Nh3", "Bh3+"], canon

    # STABLE-sort tie: equal score_cp entries keep their original relative
    # order (cp alone cannot rank a tie; the caller's tie-break stays in charge).
    tied = [
        {"uci": "a2a3", "san": "a3", "score_cp": 40},
        {"uci": "b2b3", "san": "b3", "score_cp": 40},
        {"uci": "c2c3", "san": "c3", "score_cp": 10},
    ]
    assert [s["san"] for s in canonicalize_by_score(tied)] == ["a3", "b3", "c3"]

    # Purity: the INPUT list is never mutated by the canonicalization.
    assert [s["san"] for s in raw] == ["d5", "h4", "Ne2", "Bh3+", "Nh3"]

    # Already-canonical input passes through in the same order.
    assert [s["san"] for s in canonicalize_by_score(canon)] == [
        "d5", "h4", "Ne2", "Nh3", "Bh3+",
    ]
    print("    inverted MultiPV list -> strict cp-descending; tie keeps original order")
    print("  [PASS] canonicalize_by_score fixes out-of-order MultiPV (stable, pure)")


# --- PART 4: end-to-end integration (engine, print-only) ----------------------
NUM_MOVES = 5
TIME_LIMIT = 0.3
# Sacrifice fixtures first (Defender/Positional must NOT promote the
# sacrifice-heavy top candidate there), then the fixture built FOR
# Positional ("quiet positional middlegame" -- all-quiet candidates:
# expected Positional fallback to cp order), the near-equal reorder
# comparison fixture and the endgame phase-gating sanity check.
INTEGRATION_FIXTURES = [
    "obvious sacrifice",
    "sharp tactical no quiet alternative",
    "quiet positional middlegame",
    "near-equal candidates mixed style",
    "active endgame king",
]


def _run_integration_fixture(engine, fixture):
    board = chess.Board(fixture["fen"])
    phase = game_phase(board)
    raw_suggestions = engine.suggest(board, num_moves=NUM_MOVES, time_limit=TIME_LIMIT)
    suggestions = canonicalize_by_score(raw_suggestions)
    resorted = [s["uci"] for s in suggestions] != [s["uci"] for s in raw_suggestions]
    if not suggestions:
        print("  (no candidate moves returned)")
        return
    best = max(s["score_cp"] for s in suggestions)

    rows = []
    for s in suggestions:
        move = chess.Move.from_uci(s["uci"])
        scores, _ = compute_style_scores(board, move)
        norm_cp = s["score_cp"] - best
        a = attacker_score(scores, board)
        x = sacrificer_score(scores, board)
        d = defender_score(scores, board)
        p = positional_score(scores, board)
        g = gambiter_score(scores, board)
        final_a = persona_adjusted_score(norm_cp, a, phase)
        final_s = persona_adjusted_score(norm_cp, x, phase)
        final_d = persona_adjusted_score(norm_cp, d, phase)
        final_p = persona_adjusted_score(norm_cp, p, phase)
        final_g = persona_adjusted_score(norm_cp, g, phase)
        rows.append({
            "san": s["san"], "cp": s["score_cp"], "norm": norm_cp,
            "atk": scores.attack_gain, "def": scores.defense_gain,
            "sac": scores.sacrifice_signal, "vol": scores.volatility,
            "a": a, "x": x, "d": d, "p": p, "g": g,
            "d_a": final_a - norm_cp, "d_s": final_s - norm_cp,
            "d_d": final_d - norm_cp, "d_p": final_p - norm_cp,
            "d_g": final_g - norm_cp,
            "final_a": final_a, "final_s": final_s,
            "final_d": final_d, "final_p": final_p, "final_g": final_g,
        })

    print("=" * 200)
    print(f"FIXTURE: {fixture['name']}   phase={phase:.2f}")
    print(f"  {fixture['description']}")
    print(f"  FEN: {fixture['fen']}")
    if resorted:
        print("  (note: raw MultiPV order was not cp-sorted; ENGINE order below is canonicalized)")
    header = (f"   {'#':>2} {'move':<7} {'cp':>6} {'norm':>6} {'atk':>7} {'def':>7} "
              f"{'sac':>4} {'vol':>5} {'A':>8} {'S':>8} {'D':>8} {'P':>8} {'G':>8} "
              f"{'dA_cp':>7} {'dS_cp':>7} {'dD_cp':>7} {'dP_cp':>7} {'dG_cp':>7} "
              f"{'finalA':>9} {'finalS':>9} {'finalD':>9} {'finalP':>9} {'finalG':>9}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    print("  (A/S/D/P/G = raw persona scores in [-1,1]; d*_cp = applied bias in"
          " CENTIPAWNS = PERSONA_BIAS_CP*bias; final = norm + d, demotion-floored at -75)")
    for i, r in enumerate(rows):
        print(f"   {i + 1:>2} {r['san']:<7} {r['cp']:>6} {r['norm']:>6} "
              f"{r['atk']:>+7.2f} {r['def']:>+7.2f} {r['sac']:>4.1f} {r['vol']:>5.2f} "
              f"{r['a']:>+8.4f} {r['x']:>+8.4f} {r['d']:>+8.4f} {r['p']:>+8.4f} {r['g']:>+8.4f} "
              f"{r['d_a']:>+7.2f} {r['d_s']:>+7.2f} {r['d_d']:>+7.2f} {r['d_p']:>+7.2f} {r['d_g']:>+7.2f} "
              f"{r['final_a']:>+9.2f} {r['final_s']:>+9.2f} {r['final_d']:>+9.2f} "
              f"{r['final_p']:>+9.2f} {r['final_g']:>+9.2f}")

    att_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_a"], i))
    sac_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_s"], i))
    def_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_d"], i))
    pos_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_p"], i))
    gam_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_g"], i))
    eng = " > ".join(rows[i]["san"] for i in range(len(rows)))
    att = " > ".join(rows[i]["san"] for i in att_idx)
    sac = " > ".join(rows[i]["san"] for i in sac_idx)
    dfn = " > ".join(rows[i]["san"] for i in def_idx)
    pos = " > ".join(rows[i]["san"] for i in pos_idx)
    gam = " > ".join(rows[i]["san"] for i in gam_idx)
    print(f"  ENGINE order:     {eng}")
    mark_a = "  (unchanged)" if att == eng else ""
    mark_s = "  (unchanged)" if sac == eng else ""
    mark_d = "  (unchanged)" if dfn == eng else ""
    mark_p = "  (unchanged)" if pos == eng else ""
    mark_g = "  (unchanged)" if gam == eng else ""
    print(f"  ATTACKER order:   {att}{mark_a}")
    print(f"  SACRIFICER order: {sac}{mark_s}")
    print(f"  DEFENDER order:   {dfn}{mark_d}")
    print(f"  POSITIONAL order: {pos}{mark_p}")
    print(f"  GAMBITER order:   {gam}{mark_g}")
    print()


def run_integration(engine):
    targets = set(INTEGRATION_FIXTURES)
    for fixture in FIXTURES:
        if fixture["name"] in targets:
            _run_integration_fixture(engine, fixture)


def main() -> int:
    print("=== Running persona weight-layer tests (parts 1-3) ===")
    tests = [
        test_zero_maps_to_zero,
        test_monotonic,
        test_large_inputs_approach_but_never_reach_one,
        test_real_fixture_values,
        test_phase_damping_is_selective,
        test_persona_scores_bounded_and_differentiated,
        test_phase_gating_end_to_end_in_persona_score,
        test_persona_adjusted_score_scale_and_floor,
        test_persona_adjusted_score_trust_contract,
        test_persona_adjusted_score_real_flip_on_near_equal,
        test_phase_gating_on_real_mate_position,
        test_persona_ordering_differentiation_deterministic,
        test_defender_scores_bounded,
        test_defender_fixture_signs,
        test_defender_phase_gating_real_position,
        test_defender_differentiation_on_near_equal,
        test_positional_scores_bounded,
        test_positional_fixture_contrasts,
        test_positional_differentiation_on_near_equal,
        test_gambiter_weights_are_the_declared_blend,
        test_gambiter_scores_bounded_and_clamp_vestigial,
        test_gambiter_blend_between_attacker_and_sacrificer,
        test_gambiter_differentiation_on_near_equal,
        test_gambiter_quiet_positional_fallback,
        test_canonicalize_by_score_fixes_multipv_order,
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
    print("\nAll part 1-3 assertions passed.")

    print("\n=== PART 4: end-to-end integration (engine, print-only) ===")
    engine = StockfishEngine()
    engine.start()
    try:
        run_integration(engine)
    finally:
        engine.close()
    print("Integration run complete (print-only; see flags in the delivery report).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
