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
  * both persona scores bounded to [-1, 1]
  * Attacker and Sacrificer correlate on Bxh7+ (both positive, sacrificer
    heavier) but DIVERGE on a non-sacrificial direct attack (Qxg7#: mate-gated
    sac=0.0 -> attacker stays high, sacrificer low)
  * phase damping touches ONLY the king-zone-pressure attack subcomponents;
    sacrifice/volatility/concrete tactical events pass through untouched
  * end-to-end: the same StyleScores score lower under Attacker in a bare
    king-and-pawn endgame (phase 1.0) than in the opening (phase 0.0)

PART 4 integration (engine, print-only):
  full pipeline on 4 fixtures (the 3 required + active endgame king as a
  phase-gating sanity check): suggest() -> canonicalize_by_score() ->
  compute_style_scores() -> attacker_score()/sacrificer_score() ->
  persona_adjusted_score(real engine_norm_cp, real game_phase), i.e.
  final = norm + PERSONA_BIAS_CP (=100) * bounded_bias, demotion-floored
  at -75, with the engine/Attacker/Sacrificer orderings printed side by
  side. The ENGINE order is canonicalize_by_score()'s strict
  cp-descending order, so "(unchanged)" can only mean the persona
  contributed nothing -- never that the list needed re-sorting anyway.

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
    _phase_damped_scores,
    _squash_signed,
    attacker_score,
    canonicalize_by_score,
    normalize_style_scores,
    persona_adjusted_score,
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
INTEGRATION_FIXTURES = [
    "obvious sacrifice",
    "sharp tactical no quiet alternative",
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
        final_a = persona_adjusted_score(norm_cp, a, phase)
        final_s = persona_adjusted_score(norm_cp, x, phase)
        rows.append({
            "san": s["san"], "cp": s["score_cp"], "norm": norm_cp,
            "atk": scores.attack_gain, "def": scores.defense_gain,
            "sac": scores.sacrifice_signal, "vol": scores.volatility,
            "a": a, "x": x, "d_a": final_a - norm_cp, "d_s": final_s - norm_cp,
            "final_a": final_a, "final_s": final_s,
        })

    print("=" * 118)
    print(f"FIXTURE: {fixture['name']}   phase={phase:.2f}")
    print(f"  {fixture['description']}")
    print(f"  FEN: {fixture['fen']}")
    if resorted:
        print("  (note: raw MultiPV order was not cp-sorted; ENGINE order below is canonicalized)")
    header = (f"   {'#':>2} {'move':<7} {'cp':>6} {'norm':>6} {'atk':>7} {'def':>7} "
              f"{'sac':>4} {'vol':>5} {'A':>8} {'S':>8} {'dA_cp':>7} {'dS_cp':>7} "
              f"{'finalA':>9} {'finalS':>9}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    print("  (A/S = raw persona scores in [-1,1]; dA_cp/dS_cp = applied bias in"
          " CENTIPAWNS = PERSONA_BIAS_CP*bias; final = norm + d, demotion-floored at -75)")
    for i, r in enumerate(rows):
        print(f"   {i + 1:>2} {r['san']:<7} {r['cp']:>6} {r['norm']:>6} "
              f"{r['atk']:>+7.2f} {r['def']:>+7.2f} {r['sac']:>4.1f} {r['vol']:>5.2f} "
              f"{r['a']:>+8.4f} {r['x']:>+8.4f} {r['d_a']:>+7.2f} {r['d_s']:>+7.2f} "
              f"{r['final_a']:>+9.2f} {r['final_s']:>+9.2f}")

    att_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_a"], i))
    sac_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_s"], i))
    eng = " > ".join(rows[i]["san"] for i in range(len(rows)))
    att = " > ".join(rows[i]["san"] for i in att_idx)
    sac = " > ".join(rows[i]["san"] for i in sac_idx)
    print(f"  ENGINE order:     {eng}")
    mark_a = "  (unchanged)" if att == eng else ""
    mark_s = "  (unchanged)" if sac == eng else ""
    print(f"  ATTACKER order:   {att}{mark_a}")
    print(f"  SACRIFICER order: {sac}{mark_s}")
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
