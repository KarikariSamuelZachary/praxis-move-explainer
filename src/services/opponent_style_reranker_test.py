"""
Live test harness for opponent_style_reranker.py.

Verifies the re-ranker's required behaviours:

  1. HIGH-SACRIFICE OPPONENT: a style profile with sufficient=True and
     a high sacrifice_frequency. The candidate list is engineered so
     one mid-rank candidate (rank 2) is sacrifice-looking. Verify that
     across many trials of the weighted sampler, the sac-looking
     candidate is picked substantially more often than its base weight
     would predict (~50% under rank-decay base) and the choice
     distribution reflects the bias.

  2. LOW-SACRIFICE OPPONENT: same candidate list + board, with a low
     sacrifice_frequency. Verify that sac-looking candidate is picked
     close to its base rate -- bias must NOT over-tilt for low-sac
     opponents, or we'd damage Maia's natural calibration.

  3. INSUFFICIENT-DATA OPPONENT (regression): style with sufficient=False.
     Verify the re-ranker returns candidates[0] deterministically with
     applied_bias=False and source="insufficient_data" -- identical to
     what today's default Maia behavior would produce.

The candidate list and board are realistic:
  - Board FEN: '7k/8/8/3p4/8/2N5/8/4K3 w - - 0 1'
    White knight on c3, black pawn on d5 (which attacks c4 and e4).
    Two of the knight candidate moves:
      * c3e4 -> sac-looking (knight lands on e4, attacked by black's d5
        pawn, pawn value 1 < knight value 3 -> opponent can profitably
        recapture, per the v1 live-sac proxy)
      * c3a4 -> quiet (no black piece attacks a4)
  - Candidate list (ranked): rank 1 = c3a4 (quiet), rank 2 = c3e4 (sac)
    -- this places the sac-looking candidate at rank 2, so under
    unbiased base weights (rank 1=1.0, rank 2=0.5) the quiet candidate
    is favored 2:1 by default; the bias must measurably tilt this ratio,
    not necessarily flip it to majority (the design preserves rank-1
    as the base rate so Maia's calibration isn't discarded).

Tests:
  1. Head-to-head high-sac vs low-sac on the SAME candidate list +
     SAME board, n=5000 trials each. Assert: high-sac e4_pct - low-sac
     e4_pct >= 5pp (clear tilting); high-sac e4_pct > base+5pp (bias does
     work); low-sac e4_pct within 5pp of base (no over-tilt for low-sac).
  2. Insufficient-data regression: style.sufficient=False -> deterministic
     return of candidates[0] with applied_bias=False.
  3. No-candidates edge case: empty input returns source='no_candidates'.
  4. EVEN-TRADE RECAPTURE REGRESSION (bugfix for _is_live_sac_move):
     a candidate that captures a piece of roughly equal value to itself
     and then faces a cheaper recapture threat must NOT be flagged as
     sac-looking -- the net material swing is ~0, below the threshold.
     Also re-verifies the original fixtures (c3e4, c3a4, c3d5, c3b5) on
     their original board to confirm the bugfix does not change any of
     their classifications -- the fix targets the netting-out, not the
     recapture detection.
"""
import os
import sys
import random
import json
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import services.opponent_style_reranker as reranker_mod
from services.opponent_style_reranker import rerank_candidates


# ---------------------------------------------------------------------------
# Realistic shared fixture: board + candidate list engineered so bias can
# actually flip the choice against the rank-1 base weight.
# ---------------------------------------------------------------------------
BOARD_FEN = "7k/8/8/3p4/8/2N5/8/4K3 w - - 0 1"

CANDIDATES = [
    # Rank 1 -- quiet developing knight move. NOT sac-looking.
    # (Realistic shape -- bonus fields score/wdl from Maia's analyse)
    {
        "move": "c3a4",
        "score": 12,
        "wdl": {"win": 0.500, "draw": 0.040, "loss": 0.460},
    },
    # Rank 2 -- sac-looking move (knight lands on d5-attacked e4).
    {
        "move": "c3e4",
        "score": -8,
        "wdl": {"win": 0.430, "draw": 0.040, "loss": 0.530},
    },
    # Rank 3 filler -- quiet knight move.
    {
        "move": "c3b5",
        "score": 8,
        "wdl": {"win": 0.490, "draw": 0.045, "loss": 0.465},
    },
    # Rank 4 filler -- another sac-looking move to verify ALL sac-looking
    # candidates get the boost (not just the first).
    # c3d5 (capturing the d5 pawn) -> after capture, is the knight on d5
    # attacked by anything? Black king h8 doesn't reach d5; no other black
    # piece. So c3d5 is actually NOT sac-looking (knight captures a free
    # pawn on d5 and isn't attacked afterwards). Including it as a
    # NEGATIVE control -- verifies the proxy doesn't over-fire on capture
    # moves.
    {
        "move": "c3d5",
        "score": 30,
        "wdl": {"win": 0.520, "draw": 0.040, "loss": 0.440},
    },
]


def _make_style(*, sufficient, sacrifice_frequency, game_count=12):
    """Construct a minimal style dict with the fields rerank consumes."""
    return {
        "sufficient": sufficient,
        "game_count": game_count,
        "sacrifice_frequency": sacrifice_frequency,
        "opening_family_lean": {"Sicilian Defense": 1.0} if sufficient else None,
    }


# ---------------------------------------------------------------------------
# Test 1: HIGH-SAC OPPONENT -- bias must measurably tilt toward c3e4
# (NOT necessarily above c3a4 -- the design decision preserves rank-1 as
# the base rate; we measure the RELATIVE tilt vs low-sac, not absolute
# majority). See the module-level commentary on the design trade.
# ---------------------------------------------------------------------------
def _run_distribution(style, n_trials, seed):
    """Sample the re-ranker n_trials times, return Counter(chosen_index)."""
    board = chess.Board(BOARD_FEN)
    rng = random.Random(seed)
    picks = Counter()
    for _ in range(n_trials):
        result = rerank_candidates(
            candidates=CANDIDATES, style=style, board=board, rng=rng,
        )
        picks[result["chosen_index"]] += 1
    return picks


def test_high_vs_low_sacrifice_opponents():
    print("\n=== Test 1: HIGH-SAC vs LOW-SAC OPPONENT (head-to-head) ===")
    board = chess.Board(BOARD_FEN)
    n_trials = 5000

    # HIGH-sac: an extreme but plausible sacrifice rate. Matches the
    # recent-shift fixture's recency-weighted rate (~0.15) plus headroom.
    high_style = _make_style(sufficient=True, sacrifice_frequency=0.15)
    # LOW-sac: a typical conservative-positional-player rate.
    low_style = _make_style(sufficient=True, sacrifice_frequency=0.01)

    print(f"  high-sac style: sac_freq = {high_style['sacrifice_frequency']}")
    print(f"  low-sac  style: sac_freq = {low_style['sacrifice_frequency']}")
    print(f"  candidates (with pre-flight sac-look check):")
    for i, c in enumerate(CANDIDATES):
        is_sac = reranker_mod._is_live_sac_move(board, c["move"])
        print(f"    [{i}] {c['move']:8}  sac-looking = {is_sac}")

    # Closed-form expected weights, for the test to be self-validating
    # against the implementation, not just against an arbitrary threshold.
    # base(rank) = 0.5**(rank-1): rank1=1.0, rank2=0.5, rank3=0.25, rank4=0.125
    # bias_mult_i = 1 + 4.0 * sac_freq * sac_indicator_i
    # weight_i = base_i * bias_mult_i
    def expected_weights(sac_freq):
        weights = []
        for i, c in enumerate(CANDIDATES):
            base = 0.5 ** i
            is_sac = reranker_mod._is_live_sac_move(board, c["move"])
            mult = 1.0 + 4.0 * sac_freq * (1.0 if is_sac else 0.0)
            weights.append(base * mult)
        total = sum(weights)
        return [w / total for w in weights], weights

    high_expected, high_raw = expected_weights(high_style["sacrifice_frequency"])
    low_expected, low_raw = expected_weights(low_style["sacrifice_frequency"])
    print(f"\n  Closed-form expected weights (sanity check vs measured):")
    print(f"    high-sac raw weights: {[round(w, 4) for w in high_raw]}, "
          f"share: {[round(p, 4) for p in high_expected]}")
    print(f"    low-sac  raw weights: {[round(w, 4) for w in low_raw]}, "
          f"share: {[round(p, 4) for p in low_expected]}")

    # Sample. Use different seeds for high vs low so the empirical
    # distributions aren't the same draws (this is a real-world
    # reproduction, not an adversarial test).
    high_picks = _run_distribution(high_style, n_trials, seed=17726)
    low_picks = _run_distribution(low_style, n_trials, seed=99)

    print(f"\n  Empirical distribution over {n_trials} trials each:")
    print(f"    {'idx':>3}  {'move':>6}  {'high_n':>7} {'high_pct':>10}  "
          f"{'low_n':>7} {'low_pct':>10}  {'delta_pp':>10}")
    for idx in range(len(CANDIDATES)):
        c = CANDIDATES[idx]
        h_n = high_picks.get(idx, 0)
        l_n = low_picks.get(idx, 0)
        h_pct = h_n / n_trials * 100
        l_pct = l_n / n_trials * 100
        delta = h_pct - l_pct
        marker = " <-- sac-looking" if reranker_mod._is_live_sac_move(board, c["move"]) else ""
        print(f"    {idx:>3}  {c['move']:>6}  {h_n:>7} {h_pct:>9.2f}%  "
              f"{l_n:>7} {l_pct:>9.2f}%  {delta:>+9.2f}pp{marker}")

    # --- Assertions ----------------------------------------------------------
    # The sac-looking candidate (c3e4 index 1) must:
    #   (a) be picked SUBSTANTIALLY more often under high-sac than low-sac
    #       (the user's "picks differently for each" requirement, made
    #       precise as "the bias measurably tilts the distribution").
    #   (b) be picked well ABOVE its unbiased base-rate share under high-sac
    #       (proves the bias is doing work, not just sampling noise).
    #   (c) be picked CLOSE TO its base-rate share under low-sac (proves
    #       the bias doesn't over-tilt for low-sac opponents -- preserving
    #       Maia's calibration is the design contract).
    e4_high_pct = high_picks.get(1, 0) / n_trials * 100
    e4_low_pct = low_picks.get(1, 0) / n_trials * 100

    delta_pp = e4_high_pct - e4_low_pct
    # Closed-form: e4_high_share ~ 0.8/2.175 ~= 36.8%, e4_low_share ~ 0.52/1.895
    # ~= 27.4%. delta = ~9.4pp. We assert >= 5pp to allow sampling noise but
    # require a clearly visible effect.
    assert delta_pp >= 5.0, (
        f"head-to-head test: e4 should be picked >= 5pp more often under high-sac "
        f"than low-sac; got high={e4_high_pct:.2f}% low={e4_low_pct:.2f}% "
        f"(delta={delta_pp:+.2f}pp)"
    )
    print(f"\n  [PASS] high-sac e4_pct ({e4_high_pct:.2f}%) - low-sac e4_pct "
          f"({e4_low_pct:.2f}%) = {delta_pp:+.2f}pp  (>= 5pp required)")

    # (b) high-sac must push e4 above its base-rate share (~27.6%)
    base_rate_e4_share = 0.5 / (1.0 + 0.5 + 0.25 + 0.125)
    assert e4_high_pct > base_rate_e4_share * 100 + 5.0, (
        f"high-sac: e4 should be picked >5pp above base rate ({base_rate_e4_share*100:.2f}%); "
        f"got {e4_high_pct:.2f}%"
    )
    print(f"  [PASS] high-sac e4_pct ({e4_high_pct:.2f}%) > base+5pp ({base_rate_e4_share*100 + 5.0:.2f}%)")

    # (c) low-sac must keep e4 near its base rate
    assert abs(e4_low_pct - base_rate_e4_share * 100) < 5.0, (
        f"low-sac: e4 should be picked within 5pp of base rate "
        f"({base_rate_e4_share*100:.2f}%); got {e4_low_pct:.2f}%"
    )
    print(f"  [PASS] low-sac e4_pct ({e4_low_pct:.2f}%) within 5pp of base "
          f"({base_rate_e4_share*100:.2f}%) -- bias doesn't over-tilt")

    # The sample return shape must be correct on a high-sac call
    sample = rerank_candidates(
        candidates=CANDIDATES, style=high_style, board=board, rng=random.Random(0)
    )
    assert sample["applied_bias"] is True
    assert sample["source"] == "style_biased_sacrifice"
    assert sample["bias_breakdown"] is not None
    assert sample["bias_breakdown"]["family_lean"] == "disabled_in_v1_no_candidate_family_classifier"
    assert sample["sacrifice_frequency"] == 0.15
    assert sample["opening_family_lean"] == high_style["opening_family_lean"]
    assert sample["game_count"] == 12
    print(f"  [PASS] sample return shape: applied_bias=True, "
          f"source='style_biased_sacrifice', bias_breakdown populated, "
          f"family_lean sentinel set, sacrifice_frequency surfaced.")
    print(f"\n  Sample return:")
    print(f"  {json.dumps(sample, indent=2)}")


# ---------------------------------------------------------------------------
# Test 3: INSUFFICIENT DATA (regression) -- must return candidates[0]
# deterministically with applied_bias=False.
# ---------------------------------------------------------------------------
def test_insufficient_data_regression():
    print("\n=== Test 3: INSUFFICIENT-DATA REGRESSION ===")
    board = chess.Board(BOARD_FEN)
    style = _make_style(sufficient=False, sacrifice_frequency=0.20)
    print(f"  style: sufficient=False (sac_freq irrelevant = {style['sacrifice_frequency']})")
    print(f"  candidates[0]: {CANDIDATES[0]}")

    # Run several times -- MUST return move=c3a4 every time.
    for trial_idx in range(5):
        result = rerank_candidates(
            candidates=CANDIDATES, style=style, board=board,
            rng=random.Random(trial_idx),
        )
        assert result["chosen_index"] == 0, (
            f"insufficient test trial {trial_idx}: expected chosen_index=0, "
            f"got {result['chosen_index']}"
        )
        assert result["chosen_move_uci"] == "c3a4"
        assert result["applied_bias"] is False
        assert result["source"] == "insufficient_data"
        assert result["bias_breakdown"] is None

    # Show the full return once for inspection
    sample = rerank_candidates(
        candidates=CANDIDATES, style=style, board=board
    )
    print(f"  return: {json.dumps(sample, indent=2)}")
    print(f"  [PASS] 5 trials all returned candidates[0] deterministically")


# ---------------------------------------------------------------------------
# Test 4 (bonus structural): no-candidates list, sufficient=False.
# Confirms the degenerate empty-input path doesn't crash and surfaces
# the documented "no_candidates" source.
# ---------------------------------------------------------------------------
def test_no_candidates():
    print("\n=== Test 4: NO-CANDIDATES EDGE CASE ===")
    board = chess.Board(BOARD_FEN)
    style = _make_style(sufficient=True, sacrifice_frequency=0.15)
    result = rerank_candidates(
        candidates=[], style=style, board=board,
    )
    assert result["chosen_move_uci"] == ""
    assert result["chosen_index"] == -1
    assert result["applied_bias"] is False
    assert result["source"] == "no_candidates"
    print(f"  return: {json.dumps(result, indent=2)}")
    print(f"  [PASS] empty list returns source='no_candidates' without crashing")


# ---------------------------------------------------------------------------
# Test 5: EVEN-TRADE RECAPTURE REGRESSION (bugfix for _is_live_sac_move).
#
# bug: _is_live_sac_move compared the MOVED piece's RAW value to the
# cheapest attacker's value -- it never netted out what the candidate
# itself captured. So NxB (knight=3, bishop=3) followed by ...pxN
# (pawn recaptures knight) was flagged as sac-looking ("knight hangs to
# a cheaper pawn") even though the net swing is 0. The fix adds a
# net-material-at-stake gate BEFORE the recapture-profitability check:
# net_loss = (moved piece value) - (captured piece value) must meet
# SAC_MATERIAL_THRESHOLD (=3) before the recapture check is consulted.
#
# This test uses a board engineered to put BOTH cases side-by-side so we
# can prove the fix targets the netting-out and not the recapture
# detection itself:
#
#   Board FEN: '7k/8/2p5/1b1p4/8/2N5/8/4K3 w - - 0 1'
#     White: king e1, knight c3
#     Black: king h8, bishop b5, pawn c6 (attacks b5), pawn d5 (attacks
#            e4)
#
#   * c3b5 (Nxb5) -- captures the bishop (value 3). The knight on b5 is
#     then attacked by the c6 pawn (value 1 < 3). With the BUGGY
#     raw-value-only logic, this would be flagged sac-looking. With the
#     fix, net_loss = 3 - 3 = 0 < SAC_MATERIAL_THRESHOLD=3 -> NOT
#     sac-looking. This is the regression we care about.
#
#   * c3e4 (Ne4) -- moves to empty e4 (no capture). The knight on e4 is
#     attacked by the d5 pawn (value 1 < 3). net_loss = 3 - 0 = 3 >= 3
#     -> STILL sac-looking. Proves the fix preserves the basic hanging-
#     knight-to-pawn case.
#
#   * c3a4, c3b1 -- quiet knight moves (no capture, no attackers). Both
#     must remain NOT sac-looking (unchanged from the basic case).
# ---------------------------------------------------------------------------
def test_even_trade_recapture_is_not_sac_looking():
    print("\n=== Test 5: EVEN-TRADE RECAPTURE NOT SAC-LOOKING (bugfix) ===")
    even_trade_fen = "7k/8/2p5/1b1p4/8/2N5/8/4K3 w - - 0 1"
    board = chess.Board(even_trade_fen)
    print(f"  board: {even_trade_fen}")

    # The even-trade capture: knight takes bishop, gets recaptured by
    # a cheaper pawn. Without the netting-out fix, this is sac-looking.
    # With the fix, the 0 net loss gates it out.
    even_trade_move = "c3b5"
    is_sac_even_trade = reranker_mod._is_live_sac_move(board, even_trade_move)
    print(f"  {even_trade_move} (Nxb5, net_loss=3-3=0): "
          f"sac-looking = {is_sac_even_trade}")
    assert is_sac_even_trade is False, (
        f"BUG: even-trade capture {even_trade_move} (knight captures bishop, "
        f"pawn recapture available) was flagged sac-looking -- the net "
        f"netting-out gate is missing or wrong"
    )
    print(f"  [PASS] {even_trade_move} (NxB -> ...pxN, even trade) is NOT "
          f"sac-looking under the fixed proxy")

    # Same board, the basic hanging-knight-to-pawn case MUST still be
    # sac-looking -- the fix must not damage the core detection.
    sac_no_capture_move = "c3e4"
    is_sac_no_capture = reranker_mod._is_live_sac_move(board, sac_no_capture_move)
    print(f"  {sac_no_capture_move} (Ne4, net_loss=3-0=3, "
          f"attacked by d5 pawn): sac-looking = {is_sac_no_capture}")
    assert is_sac_no_capture is True, (
        f"REGRESSION: {sac_no_capture_move} (knight to empty square, attacked "
        f"by cheaper pawn) should still be sac-looking after the even-trade fix"
    )
    print(f"  [PASS] {sac_no_capture_move} (Ne4 -> ...pctN, hangs to pawn) "
          f"IS still sac-looking -- core detection preserved")

    # Quiet knight moves -- unchanged.
    for quiet_move in ("c3a4", "c3b1"):
        is_sac_quiet = reranker_mod._is_live_sac_move(board, quiet_move)
        print(f"  {quiet_move} (quiet, no capture, no attackers): "
              f"sac-looking = {is_sac_quiet}")
        assert is_sac_quiet is False, (
            f"quiet knight move {quiet_move} should not be sac-looking"
        )
    print(f"  [PASS] quiet knight moves c3a4 / c3b1 remain NOT sac-looking")

    # --- Original-board fixture regression sweep -----------------------------
    # The fix must NOT change classification of the four existing
    # fixtures (c3e4, c3a4, c3b5, c3d5) on the original Test 1 board --
    # they must all classify the same way as before the fix. This is the
    # user-requested regression guardrail: an unchanged classification
    # across these four proves the fix targets the netting-out behavior
    # rather than the recapture detection behavior.
    print(f"\n  Regression sweep: original BOARD_FEN fixtures (must NOT change)")
    print(f"  board: {BOARD_FEN}")
    orig_board = chess.Board(BOARD_FEN)
    expected = {
        # net_loss = 3 - 0 = 3 >= 3, attacked by d5 pawn (1 < 3) -> sac
        "c3e4": True,
        # no attackers -> not sac
        "c3a4": False,
        # no attackers -> not sac (rank 3 filler)
        "c3b5": False,
        # c3d5 (Nxd5): net_loss = 3 - 1 = 2 < 3 -> not sac under fix.
        # Pre-fix: not sac because no attackers on d5 after the capture.
        # Both paths return False -- classification is unchanged even
        # though the reasoning path differs.
        "c3d5": False,
    }
    for uci, expected_sac in expected.items():
        actual_sac = reranker_mod._is_live_sac_move(orig_board, uci)
        marker = " <-- sac-looking" if actual_sac else ""
        print(f"    {uci:8}  expected={expected_sac!s:5}  "
              f"actual={actual_sac!s:5}{marker}")
        assert actual_sac == expected_sac, (
            f"REGRESSION: original fixture {uci} on BOARD_FEN changed "
            f"classification after fix: expected {expected_sac}, got "
            f"{actual_sac}. The fix must NOT change any of these."
        )
    print(f"  [PASS] all four original fixtures classify the same way "
          f"as before the fix -- bugfix is scoped to the netting-out gate")

    # --- Full rerank flow on the even-trade board ----------------------------
    # Even with a high-sac opponent, an even-trade candidate set should
    # produce NO sac-looking candidates, so the re-ranker must take the
    # "default_top_candidate" deterministic path (applied_bias=False).
    # This verifies the fix propagates all the way through rerankCandidates.
    print(f"\n  End-to-end: even-trade candidates + high-sac style -> no "
          f"bias applied")
    even_trade_candidates = [
        # Rank 1: Nxb5 -- the even-trade capture (would be sac-looking
        # under the buggy raw-value-only logic, hence a tilted choice;
        # under the fix it's just a top-ranked candidate).
        {"move": "c3b5", "score": 20, "wdl": {"win": 0.51, "draw": 0.04, "loss": 0.45}},
        # Rank 2: quiet knight move (no attackers).
        {"move": "c3a4", "score": 12, "wdl": {"win": 0.50, "draw": 0.04, "loss": 0.46}},
    ]
    high_sac_style = _make_style(sufficient=True, sacrifice_frequency=0.15)
    # Deterministic: no candidate should flag, so rerank returns top.
    for trial in range(5):
        result = rerank_candidates(
            candidates=even_trade_candidates, style=high_sac_style,
            board=board, rng=random.Random(trial),
        )
        assert result["applied_bias"] is False, (
            f"trial {trial}: even-trade candidate set should not trigger "
            f"bias under fix (applied_bias=True found)"
        )
        assert result["source"] == "default_top_candidate", (
            f"trial {trial}: even-trade + high-sac should hit "
            f"default_top_candidate path, got source={result['source']!r}"
        )
        assert result["chosen_index"] == 0, (
            f"trial {trial}: expected deterministic top pick, got "
            f"chosen_index={result['chosen_index']}"
        )
    sample = rerank_candidates(
        candidates=even_trade_candidates, style=high_sac_style,
        board=board, rng=random.Random(0),
    )
    assert sample["chosen_move_uci"] == "c3b5"
    print(f"  return: {json.dumps(sample, indent=2)}")
    print(f"  [PASS] high-sac style + Nxb5/Nc3a4 candidates -> deterministic "
          f"top pick (Nxb5), no bias applied -- fix propagates to rerank flow")


def main():
    print("opponent_style_reranker live test harness")
    print(f"  BOARD_FEN = {BOARD_FEN}")
    print(f"  CANDIDATES = {[c['move'] for c in CANDIDATES]}")
    # Pre-flight: verify which candidates are sac-looking before any test
    # runs, so the test's expectations are visibly grounded.
    board = chess.Board(BOARD_FEN)
    print("\n  Pre-flight sac-look check:")
    for i, c in enumerate(CANDIDATES):
        is_sac = reranker_mod._is_live_sac_move(board, c["move"])
        print(f"    [{i}] {c['move']:8}  sac-looking = {is_sac}")

    test_high_vs_low_sacrifice_opponents()
    test_insufficient_data_regression()
    test_no_candidates()
    test_even_trade_recapture_is_not_sac_looking()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()