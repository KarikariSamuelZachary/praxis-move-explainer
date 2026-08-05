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

  5. QUEEN-TRADE BIAS (head-to-head): an opponent with a high
     queen-trade preference (low queens_stay_on_rate) should pick the
     candidate that captures the queen substantially more often than
     an opponent with a low queen-trade preference (high
     queens_stay_on_rate). The bias is multiplicative with sac and
     gated by a timing window -- these tests use queen_trade_move_number
     = None (window fully open) and sacrifice_frequency = 0 (sac
     dormant) so the queen-trade signal is isolated.

  6. QUEEN-TRADE TIMING WINDOW: with queen_trade_move_number set, the
     bias should fire (or be very strong) when the candidate ply is
     within QUEEN_TRADE_WINDOW_HALF_WIDTH of the trade point, and
     should NOT fire (window weight 0) when the candidate ply is
     further away. The chosen-move distribution at in-window vs
     out-of-window positions should differ measurably.

  7. POLICY-BASED BASE WEIGHT: with `policy` fields on each candidate
     (the patched UCI wrapper emits them), the reranker uses the actual
     softmax probability as the base weight instead of the geometric
     rank-decay proxy. A candidate set engineered so the rank-1 move
     has a LOW policy and a mid-rank move has a HIGH policy should
     pick the high-policy move substantially more often than the
     rank-decay baseline.

  8. BASE-SOURCE REPORTING: the result's `base_source` field correctly
     reports "policy" when all candidates have usable policy values,
     "rank_decay" when none do, and "mixed" when the list has both.
     Verifies the patch-health operator-audit signal.
"""
import os
import sys
import random
import json
from collections import Counter
from typing import Dict, List

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


def _make_style(
    *,
    sufficient,
    sacrifice_frequency,
    game_count=12,
    queens_stay_on_rate=0.5,
    queen_trade_move_number=None,
):
    """Construct a minimal style dict with the fields rerank consumes.

    Defaults: queens_stay_on_rate=0.5 (neutral, no queen-trade tilt) and
    queen_trade_move_number=None (no timing gate). With these defaults
    the queen-trade bias is dormant and the existing sac tests exercise
    the sac path in isolation -- no regression from the new signal.
    """
    return {
        "sufficient": sufficient,
        "game_count": game_count,
        "sacrifice_frequency": sacrifice_frequency,
        "opening_family_lean": {"Sicilian Defense": 1.0} if sufficient else None,
        "queens_stay_on_rate": queens_stay_on_rate,
        "queen_trade_move_number": queen_trade_move_number,
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
    # Source label generalized from "style_biased_sacrifice" to "style_biased"
    # now that multiple signals can drive the bias; signals_applied tells
    # you which ones actually contributed.
    assert sample["source"] == "style_biased"
    assert sample["bias_breakdown"] is not None
    assert sample["bias_breakdown"]["family_lean"] == "disabled_in_v1_no_candidate_family_classifier"
    # On this fixture (neutral queen-trade style), only sac contributed.
    assert "sacrifice" in sample["bias_breakdown"]["signals_applied"]
    # The queen-trade signal is dormant because no candidate captures a
    # queen and the timing window is closed (no queen_trade_move_number).
    assert "queen_trade" not in sample["bias_breakdown"]["signals_applied"]
    assert sample["sacrifice_frequency"] == 0.15
    assert sample["opening_family_lean"] == high_style["opening_family_lean"]
    assert sample["game_count"] == 12
    # Per-candidate breakdown rows now carry both sac and qt sub-fields.
    for row in sample["bias_breakdown"]["weights"]:
        assert "sac_indicator" in row
        assert "sac_multiplier" in row
        assert "qt_indicator" in row
        assert "qt_window_weight" in row
        assert "qt_multiplier" in row
        assert "bias_multiplier" in row
    print(f"  [PASS] sample return shape: applied_bias=True, "
          f"source='style_biased', bias_breakdown populated, "
          f"family_lean sentinel set, signals_applied=['sacrifice'], "
          f"per-row qt fields present.")
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


# ---------------------------------------------------------------------------
# Test 6: QUEEN-TRADE BIAS (head-to-head).
#
# Isolates the queen-trade signal by:
#   * Setting sacrifice_frequency=0 (sac multiplier = 1.0 for every
#     candidate, so sac contributes nothing).
#   * Setting queen_trade_move_number=None (timing window is fully open,
#     window_weight=1.0 regardless of position).
#   * Picking a board with a queen on it and a candidate that captures it
#     (so is_qt can fire) and other candidates that don't (so the
#     contrast is visible in the distribution).
#
# Board FEN: '4k3/8/8/3q4/8/2N5/8/4K3 w - - 0 1'
#   White: king e1, knight c3
#   Black: king e8, queen d5
#   Fullmove 1, white to move.
#   * c3b5 -- quiet knight move (no capture, no attackers on b5) -> NOT qt
#   * c3d5 (Nxd5) -- captures the queen on d5. After Nxd5, the knight
#     sits on d5 with no attackers (king e8 doesn't reach d5: file diff
#     1, rank diff 3). So this is NOT a sacrifice (no recapture), but
#     it IS a queen trade. This is the bias target.
#   * c3a4 -- quiet knight move -> NOT qt
#   * c3e4 -- knight to empty e4, no attackers -> NOT qt
#
# High-trade style (queens_stay_on_rate=0.1) -> centered=0.8. For c3d5:
#   qt_mult = 1 + 1.5*0.8*1*1 = 2.2 -> weight = 0.5*2.2 = 1.1
#   Total weights: 1.1 (c3d5) + 1.0 (c3b5) + 0.25 (c3a4) + 0.125 (c3e4) = 2.475
#   c3d5 share = 1.1/2.475 = 44.4%
#
# Low-trade style (queens_stay_on_rate=0.9) -> centered=-0.8. For c3d5:
#   qt_mult = max(0.05, 1 + 1.5*-0.8*1*1) = max(0.05, -0.2) = 0.05 (floor)
#   -> weight = 0.5*0.05 = 0.025
#   Total weights: 0.025 (c3d5) + 1.0 (c3b5) + 0.25 (c3a4) + 0.125 (c3e4) = 1.4
#   c3d5 share = 0.025/1.4 = 1.8%
#
# Delta ~42.6pp between high-trade and low-trade -- way above the 5pp
# threshold the sac test uses. Asserting >= 30pp to allow sampling
# noise.
# ---------------------------------------------------------------------------
QT_BOARD_FEN = "4k3/8/8/3q4/8/2N5/8/4K3 w - - 0 1"
QT_CANDIDATES = [
    {"move": "c3b5", "score": 10, "wdl": {"win": 0.49, "draw": 0.04, "loss": 0.47}},
    {"move": "c3d5", "score": 25, "wdl": {"win": 0.55, "draw": 0.03, "loss": 0.42}},
    {"move": "c3a4", "score": 5,  "wdl": {"win": 0.48, "draw": 0.05, "loss": 0.47}},
    {"move": "c3e4", "score": 0,  "wdl": {"win": 0.46, "draw": 0.05, "loss": 0.49}},
]


def test_high_vs_low_queen_trade_preference():
    print("\n=== Test 6: HIGH vs LOW QUEEN-TRADE PREFERENCE (head-to-head) ===")
    board = chess.Board(QT_BOARD_FEN)
    n_trials = 5000

    # Isolated queen-trade signal: sac_freq=0, queen_trade_move_number=None.
    high_qt = _make_style(
        sufficient=True, sacrifice_frequency=0.0,
        queens_stay_on_rate=0.1, queen_trade_move_number=None,
    )
    low_qt = _make_style(
        sufficient=True, sacrifice_frequency=0.0,
        queens_stay_on_rate=0.9, queen_trade_move_number=None,
    )
    print(f"  high-qt style: queens_stay_on_rate = {high_qt['queens_stay_on_rate']} "
          f"(centered = {2.0 * (1 - high_qt['queens_stay_on_rate']) - 1:+.2f})")
    print(f"  low-qt  style: queens_stay_on_rate = {low_qt['queens_stay_on_rate']} "
          f"(centered = {2.0 * (1 - low_qt['queens_stay_on_rate']) - 1:+.2f})")
    print(f"  candidates (with pre-flight qt check):")
    for i, c in enumerate(QT_CANDIDATES):
        is_qt = reranker_mod._is_queen_trade_move(board, c["move"])
        print(f"    [{i}] {c['move']:8}  qt = {is_qt}")

    high_picks = Counter()
    low_picks = Counter()
    rng_high = random.Random(314)
    rng_low = random.Random(271)
    for _ in range(n_trials):
        high_picks[rerank_candidates(
            candidates=QT_CANDIDATES, style=high_qt, board=board, rng=rng_high,
        )["chosen_index"]] += 1
        low_picks[rerank_candidates(
            candidates=QT_CANDIDATES, style=low_qt, board=board, rng=rng_low,
        )["chosen_index"]] += 1

    print(f"\n  Empirical distribution over {n_trials} trials each:")
    print(f"    {'idx':>3}  {'move':>6}  {'high_n':>7} {'high_pct':>10}  "
          f"{'low_n':>7} {'low_pct':>10}  {'delta_pp':>10}")
    for idx in range(len(QT_CANDIDATES)):
        c = QT_CANDIDATES[idx]
        h_n = high_picks.get(idx, 0)
        l_n = low_picks.get(idx, 0)
        h_pct = h_n / n_trials * 100
        l_pct = l_n / n_trials * 100
        delta = h_pct - l_pct
        marker = " <-- queen trade" if reranker_mod._is_queen_trade_move(
            board, c["move"]
        ) else ""
        print(f"    {idx:>3}  {c['move']:>6}  {h_n:>7} {h_pct:>9.2f}%  "
              f"{l_n:>7} {l_pct:>9.2f}%  {delta:>+9.2f}pp{marker}")

    # c3d5 is the queen-trade candidate. Closed-form shares are 44.4%
    # (high) and 1.8% (low); delta ~42.6pp. Assert >= 30pp to leave
    # headroom for sampling noise.
    d5_high_pct = high_picks.get(1, 0) / n_trials * 100
    d5_low_pct = low_picks.get(1, 0) / n_trials * 100
    delta_pp = d5_high_pct - d5_low_pct
    assert delta_pp >= 30.0, (
        f"queen-trade head-to-head: c3d5 should be picked >= 30pp more "
        f"often under high-trade than low-trade; got high={d5_high_pct:.2f}% "
        f"low={d5_low_pct:.2f}% (delta={delta_pp:+.2f}pp)"
    )
    print(f"\n  [PASS] high-trade d5_pct ({d5_high_pct:.2f}%) - low-trade d5_pct "
          f"({d5_low_pct:.2f}%) = {delta_pp:+.2f}pp  (>= 30pp required)")

    # High-trade must push d5 above its unbiased base rate (~26.7%)
    base_rate_d5_share = 0.5 / 1.875
    assert d5_high_pct > base_rate_d5_share * 100 + 5.0, (
        f"high-trade: d5 should be picked >5pp above base rate "
        f"({base_rate_d5_share*100:.2f}%); got {d5_high_pct:.2f}%"
    )
    print(f"  [PASS] high-trade d5_pct ({d5_high_pct:.2f}%) > base+5pp "
          f"({base_rate_d5_share*100 + 5.0:.2f}%)")

    # Low-trade must suppress d5 below its unbiased base rate
    assert d5_low_pct < base_rate_d5_share * 100 - 5.0, (
        f"low-trade: d5 should be picked >5pp BELOW base rate "
        f"({base_rate_d5_share*100:.2f}%); got {d5_low_pct:.2f}%"
    )
    print(f"  [PASS] low-trade d5_pct ({d5_low_pct:.2f}%) < base-5pp "
          f"({base_rate_d5_share*100 - 5.0:.2f}%) -- suppression works")

    # Return-shape: source, signals_applied, and per-row qt fields.
    sample = rerank_candidates(
        candidates=QT_CANDIDATES, style=high_qt, board=board, rng=random.Random(0)
    )
    assert sample["applied_bias"] is True
    assert sample["source"] == "style_biased"
    assert "queen_trade" in sample["bias_breakdown"]["signals_applied"]
    # No sac on this fixture (sacrifice_frequency=0, no sac-looking moves).
    assert "sacrifice" not in sample["bias_breakdown"]["signals_applied"]
    # Per-row qt_indicator must be True ONLY for c3d5 (index 1).
    for row in sample["bias_breakdown"]["weights"]:
        if row["move"] == "c3d5":
            assert row["qt_indicator"] is True, (
                f"c3d5 row should have qt_indicator=True; got {row}"
            )
        else:
            assert row["qt_indicator"] is False, (
                f"{row['move']} row should have qt_indicator=False; got {row}"
            )
    print(f"  [PASS] sample return: applied_bias=True, source='style_biased', "
          f"signals_applied=['queen_trade'], per-row qt_indicator correct")
    print(f"\n  Sample return:")
    print(f"  {json.dumps(sample, indent=2)}")


# ---------------------------------------------------------------------------
# Test 7: QUEEN-TRADE TIMING WINDOW.
#
# With queen_trade_move_number set, the bias only fires (or fires at
# reduced strength) when the candidate ply is close to the trade point.
# We compare:
#   * IN-WINDOW: fullmove 15, white to move, candidate_ply=29. trade_ply=30.
#     delta=1. window_w = 1 - 1/24 = 0.958. Bias fires at near-full strength.
#   * OUT-OF-WINDOW: fullmove 50, white to move, candidate_ply=99. trade_ply=30.
#     delta=69 > 24. window_w = 0. Bias is fully gated -- c3d5 weight is
#     back to base (0.5) and the choice is essentially unbiased.
#
# We use the same QT_CANDIDATES fixture and the SAME high-trade style
# (queens_stay_on_rate=0.1, centered=0.8) so the only thing changing
# between the two trials is the position's fullmove number. The
# expected effect: IN-WINDOW d5_pct is much higher than OUT-OF-WINDOW
# d5_pct.
# ---------------------------------------------------------------------------
def test_queen_trade_timing_window():
    print("\n=== Test 7: QUEEN-TRADE TIMING WINDOW ===")
    high_qt = _make_style(
        sufficient=True, sacrifice_frequency=0.0,
        queens_stay_on_rate=0.1, queen_trade_move_number=30.0,
    )

    in_window_fen = "4k3/8/8/3q4/8/2N5/8/4K3 w - - 0 15"
    out_of_window_fen = "4k3/8/8/3q4/8/2N5/8/4K3 w - - 0 50"
    in_board = chess.Board(in_window_fen)
    out_board = chess.Board(out_of_window_fen)

    in_ply = reranker_mod._candidate_ply(in_board)
    out_ply = reranker_mod._candidate_ply(out_board)
    in_w = reranker_mod._queen_trade_window_weight(in_ply, 30.0)
    out_w = reranker_mod._queen_trade_window_weight(out_ply, 30.0)
    print(f"  in-window:  fullmove=15, candidate_ply={in_ply}, "
          f"window_weight={in_w:.4f} (expect ~0.96)")
    print(f"  out-window: fullmove=50, candidate_ply={out_ply}, "
          f"window_weight={out_w:.4f} (expect 0.0)")
    assert in_w > 0.9, f"in-window weight should be near 1.0; got {in_w}"
    assert out_w == 0.0, f"out-of-window weight should be 0.0; got {out_w}"

    n_trials = 5000
    in_picks = Counter()
    out_picks = Counter()
    rng_in = random.Random(101)
    rng_out = random.Random(202)
    for _ in range(n_trials):
        in_picks[rerank_candidates(
            candidates=QT_CANDIDATES, style=high_qt, board=in_board, rng=rng_in,
        )["chosen_index"]] += 1
        out_picks[rerank_candidates(
            candidates=QT_CANDIDATES, style=high_qt, board=out_board, rng=rng_out,
        )["chosen_index"]] += 1

    d5_in_pct = in_picks.get(1, 0) / n_trials * 100
    d5_out_pct = out_picks.get(1, 0) / n_trials * 100
    print(f"\n  Empirical d5_pct (queen-trade candidate) over {n_trials} trials:")
    print(f"    in-window  d5_pct = {d5_in_pct:6.2f}%  (expect >> base rate 26.7%)")
    print(f"    out-window d5_pct = {d5_out_pct:6.2f}%  (expect ~0% -- timing "
          f"window closed -> deterministic top pick)")

    # In-window must substantially boost d5 above base rate.
    base_rate_d5 = 0.5 / 1.875 * 100
    assert d5_in_pct > base_rate_d5 + 5.0, (
        f"in-window: d5 should be >5pp above base rate ({base_rate_d5:.2f}%); "
        f"got {d5_in_pct:.2f}%"
    )
    # Out-of-window: timing gate closes -> no candidate's qt_multiplier
    # deviates from 1.0 -> applied_bias contract trips to False ->
    # reranker takes the deterministic top path -> c3d5 (rank 2) is
    # NEVER picked. Assert < 1% (i.e. effectively 0) over 5000 trials --
    # the deterministic path is exact, no random sampling is involved.
    assert d5_out_pct < 1.0, (
        f"out-of-window: d5 should be ~0% (deterministic top path); "
        f"got {d5_out_pct:.2f}%"
    )
    print(f"  [PASS] in-window d5 boosted ({d5_in_pct:.2f}%), "
          f"out-of-window d5 ~0% ({d5_out_pct:.2f}%) -- timing gate works")

    # End-to-end structural check: out-of-window with a queen-trade
    # candidate but no sac-looking moves and a gated timing window
    # must take the default_top_candidate path. With the QT_CANDIDATES
    # fixture, c3b5 (rank 1) is the deterministic pick.
    out_result = rerank_candidates(
        candidates=QT_CANDIDATES, style=high_qt, board=out_board,
        rng=random.Random(0),
    )
    assert out_result["applied_bias"] is False, (
        f"out-of-window: window_weight=0 should mean NO qt bias is applied; "
        f"got applied_bias=True with breakdown {out_result['bias_breakdown']}"
    )
    assert out_result["source"] == "default_top_candidate", (
        f"out-of-window: no bias applied -> default_top_candidate; got "
        f"source={out_result['source']!r}"
    )
    assert out_result["chosen_index"] == 0
    print(f"  [PASS] out-of-window end-to-end: applied_bias=False, "
          f"source='default_top_candidate', top pick (c3b5) deterministic")


# ---------------------------------------------------------------------------
# Test 8: POLICY-BASED BASE WEIGHT.
#
# The patched UCI wrapper (scripts/maia3_patched_uci.py) emits a
# `policy` token per candidate; the python-chess parser captures it
# into candidate["policy"]; the reranker uses it as the base weight
# INSTEAD of the geometric rank-decay proxy.
#
# Setup: same board as Test 1 (knight c3, black pawn d5). A high-sac
# style (sac_freq=0.15) is used to ensure the sac bias fires and the
# reranker takes the SAMPLING path (not the deterministic-top path).
# The candidate list is engineered so:
#   * rank 1 = c3a4 (quiet)   policy=0.10  -- under rank-decay this
#     dominates, but under policy it should be picked rarely.
#   * rank 2 = c3e4 (sac)     policy=0.50  -- the bias target; under
#     policy it's strongly favored.
#   * rank 3 = c3b5 (quiet)   policy=0.25
#   * rank 4 = c3d5 (Nxd5 -- not sac-looking: net_loss=3-1=2 < 3
#     threshold)  policy=0.15
#
# Expected weights under each path (with sac_freq=0.15):
#   rank_decay: c3a4=1.0, c3e4=0.8 (sac), c3b5=0.25, c3d5=0.125
#   policy:     c3a4=0.10, c3e4=0.80 (sac), c3b5=0.25, c3d5=0.15
#
# Closed-form shares:
#   rank_decay: c3a4=46.0%, c3e4=36.8%, c3b5=11.5%, c3d5=5.7%
#   policy:     c3a4=7.7%,  c3e4=61.5%, c3b5=19.2%, c3d5=11.5%
#
# Delta on c3e4 (the sac candidate, where policy boosts it most):
#   61.5 - 36.8 = 24.7pp. Asserting >= 15pp.
# Delta on c3a4 (rank 1, where policy suppresses it most):
#   7.7 - 46.0 = -38.3pp. Asserting <= -20pp.
# ---------------------------------------------------------------------------
def test_policy_based_base_weight():
    print("\n=== Test 8: POLICY-BASED BASE WEIGHT ===")
    board = chess.Board(BOARD_FEN)
    n_trials = 5000

    # High-sac style ensures applied_bias=True so the sampler actually runs.
    high_sac_style = _make_style(
        sufficient=True, sacrifice_frequency=0.15,
        queens_stay_on_rate=0.5,  # centered=0 -> no qt tilt
        queen_trade_move_number=None,
    )

    # --- with policy field (the live patched path) -----------------------
    policy_cands = [
        # rank 1: quiet, LOW policy
        {"move": "c3a4", "score": 12, "wdl": {"win": 0.50, "draw": 0.04, "loss": 0.46},
         "policy": 0.10},
        # rank 2: sac-looking, HIGH policy
        {"move": "c3e4", "score": -8, "wdl": {"win": 0.43, "draw": 0.04, "loss": 0.53},
         "policy": 0.50},
        # rank 3: quiet, medium policy
        {"move": "c3b5", "score": 8,  "wdl": {"win": 0.49, "draw": 0.045, "loss": 0.465},
         "policy": 0.25},
        # rank 4: NOT sac-looking (net_loss=2 < 3 threshold), low policy
        {"move": "c3d5", "score": 30, "wdl": {"win": 0.52, "draw": 0.04, "loss": 0.44},
         "policy": 0.15},
    ]
    print(f"  policy_cands:")
    for i, c in enumerate(policy_cands):
        print(f"    [{i}] {c['move']:8}  policy={c['policy']}")

    policy_picks = Counter()
    rng = random.Random(1729)
    for _ in range(n_trials):
        policy_picks[rerank_candidates(
            candidates=policy_cands, style=high_sac_style, board=board, rng=rng,
        )["chosen_index"]] += 1

    # --- same candidates WITHOUT policy field (the fallback path) --------
    rank_decay_cands = [
        {"move": "c3a4", "score": 12, "wdl": {"win": 0.50, "draw": 0.04, "loss": 0.46}},
        {"move": "c3e4", "score": -8, "wdl": {"win": 0.43, "draw": 0.04, "loss": 0.53}},
        {"move": "c3b5", "score": 8,  "wdl": {"win": 0.49, "draw": 0.045, "loss": 0.465}},
        {"move": "c3d5", "score": 30, "wdl": {"win": 0.52, "draw": 0.04, "loss": 0.44}},
    ]
    rd_picks = Counter()
    rng = random.Random(1729)  # same seed for fair comparison
    for _ in range(n_trials):
        rd_picks[rerank_candidates(
            candidates=rank_decay_cands, style=high_sac_style, board=board, rng=rng,
        )["chosen_index"]] += 1

    print(f"\n  Empirical distribution over {n_trials} trials each:")
    print(f"    {'idx':>3}  {'move':>6}  {'policy_pct':>11}  {'rank_decay_pct':>14}  {'delta_pp':>9}")
    for idx in range(4):
        c = policy_cands[idx]
        p_pct = policy_picks.get(idx, 0) / n_trials * 100
        r_pct = rd_picks.get(idx, 0) / n_trials * 100
        delta = p_pct - r_pct
        marker = " <-- sac-looking" if reranker_mod._is_live_sac_move(
            board, c["move"]
        ) else ""
        print(f"    {idx:>3}  {c['move']:>6}  {p_pct:>10.2f}%  {r_pct:>13.2f}%  {delta:>+8.2f}pp{marker}")

    # c3e4 (index 1) is the sac candidate with high policy.
    e4_policy_pct = policy_picks.get(1, 0) / n_trials * 100
    e4_rd_pct = rd_picks.get(1, 0) / n_trials * 100
    delta_pp = e4_policy_pct - e4_rd_pct

    # Expected: policy e4 ~61.5%, rank_decay e4 ~36.8%, delta ~24.7pp.
    # Assert >= 15pp to allow sampling noise.
    assert delta_pp >= 15.0, (
        f"policy vs rank_decay head-to-head: c3e4 (sac + high policy) should "
        f"be picked >= 15pp more often under policy than rank_decay; got "
        f"policy={e4_policy_pct:.2f}% rank_decay={e4_rd_pct:.2f}% "
        f"(delta={delta_pp:+.2f}pp)"
    )
    print(f"\n  [PASS] policy c3e4_pct ({e4_policy_pct:.2f}%) - rank_decay "
          f"c3e4_pct ({e4_rd_pct:.2f}%) = {delta_pp:+.2f}pp  (>= 15pp required)")

    # c3a4 (rank 1) should be picked MUCH LESS under policy (low policy)
    # than under rank-decay (where it dominates).
    a4_policy_pct = policy_picks.get(0, 0) / n_trials * 100
    a4_rd_pct = rd_picks.get(0, 0) / n_trials * 100
    a4_delta = a4_policy_pct - a4_rd_pct
    # Expected: policy a4 ~7.7%, rank_decay a4 ~46.0%, delta ~-38.3pp.
    # Assert <= -20pp (large margin for sampling noise).
    assert a4_delta <= -20.0, (
        f"policy vs rank_decay: c3a4 (rank-1, low policy) should be picked "
        f"much less under policy than rank_decay; got policy={a4_policy_pct:.2f}% "
        f"rank_decay={a4_rd_pct:.2f}% (delta={a4_delta:+.2f}pp)"
    )
    print(f"  [PASS] policy c3a4_pct ({a4_policy_pct:.2f}%) << rank_decay "
          f"c3a4_pct ({a4_rd_pct:.2f}%) -- rank-1 low-policy suppressed")

    # base_source: should be "policy" for the policy-cand run and
    # "rank_decay" for the rank-decay-cand run.
    policy_result = rerank_candidates(
        candidates=policy_cands, style=high_sac_style, board=board,
        rng=random.Random(0),
    )
    rd_result = rerank_candidates(
        candidates=rank_decay_cands, style=high_sac_style, board=board,
        rng=random.Random(0),
    )
    assert policy_result["base_source"] == "policy", (
        f"with policy fields, base_source should be 'policy'; got "
        f"{policy_result['base_source']!r}"
    )
    assert rd_result["base_source"] == "rank_decay", (
        f"without policy fields, base_source should be 'rank_decay'; got "
        f"{rd_result['base_source']!r}"
    )
    # Per-row base_source in the policy result's breakdown.
    for row in policy_result["bias_breakdown"]["weights"]:
        assert row["base_source"] == "policy", (
            f"per-row base_source should be 'policy'; got {row}"
        )
    print(f"  [PASS] base_source: policy run='policy', rank_decay run='rank_decay'")


# ---------------------------------------------------------------------------
# Test 9: BASE-SOURCE REPORTING (mixed).
#
# Some candidates have policy, some don't -> base_source should be
# "mixed". This is the corner case where the patch partially works
# (e.g. some multipv slots lost their policy token due to a partial
# parse). Verifies the audit field is correct.
# ---------------------------------------------------------------------------
def test_base_source_mixed():
    print("\n=== Test 9: BASE-SOURCE REPORTING (mixed) ===")
    # _derive_base_source is the canonical source of truth for the
    # top-level base_source label. Test it directly across the three
    # cases, then verify the reranker's top-level field matches.
    all_policy = [
        {"move": "e2e4", "policy": 0.5},
        {"move": "d2d4", "policy": 0.3},
    ]
    none_policy = [
        {"move": "e2e4"},
        {"move": "d2d4"},
    ]
    mixed = [
        {"move": "e2e4", "policy": 0.5},
        {"move": "d2d4"},  # no policy
    ]
    empty = []

    assert reranker_mod._derive_base_source(all_policy) == "policy"
    assert reranker_mod._derive_base_source(none_policy) == "rank_decay"
    assert reranker_mod._derive_base_source(mixed) == "mixed"
    assert reranker_mod._derive_base_source(empty) == "rank_decay"
    print(f"  [PASS] _derive_base_source: all_policy='policy', "
          f"none_policy='rank_decay', mixed='mixed', empty='rank_decay'")

    # End-to-end: run the reranker on the mixed list. The default
    # candidate board is the starting position; a high-trade style
    # triggers a bias via the queen-trade signal (none of the
    # candidates are sac-looking or queen-trade at the start, so the
    # bias is a no-op -> default_top_candidate). We just need the
    # base_source field on the result.
    board = chess.Board()
    neutral_style = _make_style(
        sufficient=True, sacrifice_frequency=0.0,
        queens_stay_on_rate=0.5, queen_trade_move_number=None,
    )
    result = rerank_candidates(
        candidates=mixed, style=neutral_style, board=board,
        rng=random.Random(0),
    )
    assert result["base_source"] == "mixed", (
        f"mixed candidate list: base_source should be 'mixed'; got "
        f"{result['base_source']!r}"
    )
    print(f"  [PASS] reranker top-level base_source for mixed list = 'mixed'")


# ---------------------------------------------------------------------------
# Test 10: SETUP-SIGNATURE head-to-head.
#
# Two runs over the SAME candidate list + SAME board, differing only in
# the presence of `setup_signatures`:
#   * WITH-sig: a historic signature matching one candidate's resulting
#     shape (pawn + piece Jaccard composite ~0.86).
#   * NO-sig: setup_signatures=None (insufficient data; the reranker
#     falls back to no setup boost).
#
# We use the starting-position board so all four candidate moves
# (e2e4/d2d4/g1f3/c2c4) preserve the pawn skeleton well and trigger
# the boost in the WITH-sig run. The engineered signature "matches" e2e4
# at higher similarity than d2d4 (the historic has the e-pawn advanced),
# so e2e4 gets the higher setup_mult and theWITH-sig run should show
# measurably more e2e4 picks than NO-sig (where e2e4's rank-1 base
# dominates uniformly across the candidates).
#
# Expected empirical effect (closed-form at SETUP_SIGNATURE_BIAS_STRENGTH=2.5):
#   WITH-sig: e2e4's weight = 0.60 * 1 * 1 * 3.14 ~1.88; e2e4 share ~57%.
#   NO-sig:    e2e4's weight = 0.60 * 1 * 1 * 1.0 = 0.60; e2e4 share ~60%.
#
# Wait -- rank-1 already dominates by policy. So we ENGINEER the policies
# so rank-1 has lower policy: rank 1=0.15 (c3a4-quiet-> here e2e4),
# rank 2=0.50 (the move whose resulting board matches the historic sig)
# so the setup boost on rank-2 has to overcome a 3.33x rank-1 lead. With
# setup_mult of 3.14 on rank-2 (matching) vs 1.0 on rank-1, rank-2 weight
# becomes 0.50*3.14=1.57 vs rank-1's 0.15*1.0=0.15 -- rank-2 dominates.
#
# Assert: WITH-sig c3b5_pct (rank-2, setup-matching) > NO-sig c3b5_pct
#     by >= 25pp (clear tilt). NO-sig should stay near its base rate.
# ---------------------------------------------------------------------------
def test_setup_signature_head_to_head():
    print("\n=== Test 10: SETUP-SIGNATURE head-to-head ===")
    # Use the BOARD_FEN knight fixture from Test 1; we engineer a
    # signature matching c3b5 (rank 1 in this fixture becomes the
    # matching candidate rank-2 in our engineered policy set).
    board = chess.Board(BOARD_FEN)

    # Build the historic signature to match the BOARD AFTER playing c3b5
    # (rank 1's UCI in the original CANDIDATES fixture -- but we'll
    # re-order to make c3b5 rank 2 below so the test exercises a
    # non-rank-1 match per the design intent).
    b_after_b5 = board.copy(stack=False)
    b_after_b5.push(chess.Move.from_uci("c3b5"))
    pawns_b5, pieces_b5 = reranker_mod._pov_normalized_squares(b_after_b5, chess.WHITE)

    # Engineered redistribution: c3a4 rank 1 (low policy),
    # c3b5 rank 2 (high policy + setup-matching), c3e4 rank 3, c3d5 rank 4.
    low_policy_cands = [
        {"move": "c3a4", "score": 12, "wdl": {"win": 0.50, "draw": 0.04, "loss": 0.46}, "policy": 0.15},
        {"move": "c3b5", "score": 8,  "wdl": {"win": 0.49, "draw": 0.045, "loss": 0.465}, "policy": 0.50},
        {"move": "c3e4", "score": -8, "wdl": {"win": 0.43, "draw": 0.04, "loss": 0.53}, "policy": 0.25},
        {"move": "c3d5", "score": 30, "wdl": {"win": 0.52, "draw": 0.04, "loss": 0.44}, "policy": 0.10},
    ]

    # Signatures populated -> c3b5's resulting board matches the historic
    # snapshot; S ~ 1.0 (same shape, canonical-POV).
    matching_sig = {
        "pawn_squares": sorted(pawns_b5),
        "piece_squares": _pieces_by_type_dict(b_after_b5, chess.WHITE),
        "snapshot_ply": 14,
    }

    # Pre-flight: what does _candidate_setup_mult compute for each candidate?
    print(f"  pre-flight: setup mult per candidate (with matching sig at ply 14)")
    for c in low_policy_cands:
        rb = board.copy(stack=False)
        mv = chess.Move.from_uci(c["move"])
        if mv in rb.legal_moves:
            rb.push(mv)
            m, s, ply = reranker_mod._candidate_setup_mult(rb, chess.WHITE, [matching_sig])
            print(f"    {c['move']:8}  S={s:.4f}  mult={m:.4f}  matched_ply={ply}")
        else:
            print(f"    {c['move']:8}  illegal")

    style_with = _make_style(sufficient=True, sacrifice_frequency=0.0, queens_stay_on_rate=0.5)
    style_with["setup_signatures"] = [matching_sig]
    style_no = _make_style(sufficient=True, sacrifice_frequency=0.0, queens_stay_on_rate=0.5)
    style_no["setup_signatures"] = None

    n_trials = 5000
    rng_with = random.Random(4242)
    rng_no = random.Random(4242)
    with_picks = Counter()
    no_picks = Counter()
    for _ in range(n_trials):
        with_picks[rerank_candidates(
            candidates=low_policy_cands, style=style_with, board=board, rng=rng_with,
        )["chosen_index"]] += 1
        no_picks[rerank_candidates(
            candidates=low_policy_cands, style=style_no, board=board, rng=rng_no,
        )["chosen_index"]] += 1

    print(f"\n  Empirical distribution over {n_trials} trials each:")
    print(f"    {'idx':>3}  {'move':>6}  {'with_pct':>10}  {'no_pct':>10}  {'delta_pp':>10}")
    for idx in range(len(low_policy_cands)):
        c = low_policy_cands[idx]
        w_pct = with_picks.get(idx, 0) / n_trials * 100
        n_pct = no_picks.get(idx, 0) / n_trials * 100
        delta = w_pct - n_pct
        marker = " <-- setup-matching" if c["move"] == "c3b5" else ""
        print(f"    {idx:>3}  {c['move']:>6}  {w_pct:>9.2f}%  {n_pct:>9.2f}%  {delta:>+9.2f}pp{marker}")

    b5_with_pct = with_picks.get(1, 0) / n_trials * 100
    b5_no_pct = no_picks.get(1, 0) / n_trials * 100
    delta_pp = b5_with_pct - b5_no_pct
    assert delta_pp >= 25.0, (
        f"setup head-to-head: c3b5 (matching candidate) should be picked "
        f">= 25pp more under WITH-sig than NO-sig; got "
        f"with={b5_with_pct:.2f}% no={b5_no_pct:.2f}% (delta={delta_pp:+.2f}pp)"
    )
    print(f"\n  [PASS] with-sig c3b5_pct ({b5_with_pct:.2f}%) - no-sig c3b5_pct "
          f"({b5_no_pct:.2f}%) = {delta_pp:+.2f}pp  (>= 25pp required)")

    # Structural: setup_present + signals_applied + per-row fields.
    sample = rerank_candidates(
        candidates=low_policy_cands, style=style_with, board=board, rng=random.Random(0),
    )
    assert sample["applied_bias"] is True
    assert sample["source"] == "style_biased"
    assert sample["setup_present"] is True
    assert "setup_signature" in sample["bias_breakdown"]["signals_applied"]
    for row in sample["bias_breakdown"]["weights"]:
        assert "setup_S" in row and "setup_multiplier" in row and "setup_matched_ply" in row
        if row["move"] == "c3b5":
            assert row["setup_S"] is not None and row["setup_S"] > 0.7, (
                f"c3b5 should have a high setup_S; got {row['setup_S']}"
            )
            assert row["setup_matched_ply"] == 14, (
                f"c3b5 setup_matched_ply should be 14; got {row['setup_matched_ply']}"
            )
        else:
            # Other candidates also match the sig (all knight moves
            # produce a similar small piece-set delta) -- but their S
            # should be <= c3b5's. We just verify S is populated.
            assert row["setup_S"] is not None
    print(f"  [PASS] sample: applied_bias=True, setup_present=True, "
          f"signals_applied includes 'setup_signature', per-row setup_S/"
          f"setup_multiplier/setup_matched_ply populated")

    # Insufficient-setup regression: NO-sig must report setup_present=False
    # and signals_applied excludes setup_signature.
    no_sample = rerank_candidates(
        candidates=low_policy_cands, style=style_no, board=board, rng=random.Random(0),
    )
    assert no_sample["setup_present"] is False
    # When NO other biases fire (sac_freq=0 + qt_centered=0) the no-sig
    # run returns default_top_candidate with no bias_breakdown. That's
    # fine -- we just assert setup_present=False (the contract) and
    # that no setup_signal leaked through.
    if no_sample.get("bias_breakdown"):
        assert "setup_signature" not in no_sample["bias_breakdown"]["signals_applied"]
    print(f"  [PASS] no-sig: setup_present=False, signals_applied excludes "
          f"'setup_signature'")


# ---------------------------------------------------------------------------
# Test 11: SETUP INSUFFICIENT DATA + regression -- no signatures, no effect.
#
# Mirrors Test 2's insufficient-data regression but for setup specifically:
# setup_signatures=None OR [] must yield setup_mult=1.0 for every candidate,
# signals_applied must NOT include 'setup_signature', applied_bias reflects
# only other biases. Verifies the no-data path doesn't accidentally tilt.
# ---------------------------------------------------------------------------
def test_setup_insufficient_data():
    print("\n=== Test 11: SETUP INSUFFICIENT DATA ===")
    board = chess.Board(BOARD_FEN)
    cands = [
        {"move": "c3a4", "score": 12, "policy": 0.40},
        {"move": "c3e4", "score": -8, "policy": 0.20},
    ]
    # sac_freq=0.15 triggers the sac bias on c3e4 so applied_bias=True
    # and we can verify setup DOES NOT contribute even when other signals
    # active -- i.e. setup=None is genuinely no-op, not silent noise.
    style_setup_none = _make_style(sufficient=True, sacrifice_frequency=0.15)
    style_setup_none["setup_signatures"] = None

    style_setup_empty = _make_style(sufficient=True, sacrifice_frequency=0.15)
    style_setup_empty["setup_signatures"] = []

    for label, style in [("None", style_setup_none), ("[]", style_setup_empty)]:
        print(f"  setup_signatures={label}")
        result = rerank_candidates(
            candidates=cands, style=style, board=board, rng=random.Random(0),
        )
        assert result["applied_bias"] is True, (
            f"sac_freq=0.15 + c3e4 sac-looking -> applied_bias should be True "
            f"via sac signal alone; got {result}"
        )
        assert result["setup_present"] is False, (
            f"setup_signatures={label}: setup_present should be False"
        )
        assert "setup_signature" not in result["bias_breakdown"]["signals_applied"], (
            f"setup_signatures={label}: signals_applied should not include setup"
        )
        for row in result["bias_breakdown"]["weights"]:
            assert row["setup_multiplier"] == 1.0, (
                f"setup_signatures={label}: setup_multiplier should be 1.0; "
                f"got {row['setup_multiplier']}"
            )
            assert row["setup_S"] is None
            assert row["setup_matched_ply"] is None
        print(f"    [PASS] applied_bias=True (via sac), setup_present=False, "
              f"signals_applied={result['bias_breakdown']['signals_applied']}, "
              f"all setup_multiplier=1.0")


# ---------------------------------------------------------------------------
# Test 12: SETUP composition with SAC -- multiplicative composition.
#
# A candidate that's BOTH sac-looking AND setup-matching receives the
# product of the two multipliers. weight = base * sac_mult * qt_mult *
# setup_mult, where qt_mult=1 (centered=0). Verifies the three biases
# compose multiplicatively (no clamping, no short-circuit) and the
# per-row breakdown reports each component's contribution faithfully.
#
# Closed-form: c3e4 sac-looking with sac_freq=0.15 -> sac_mult = 1 + 4*0.15 = 1.6.
#              c3e4 setup-matching -> setup_mult = 1 + 2.5*0.86 ~3.15.
#              qt_mult = 1.0 (centered=0).
#              combo = 1.6 * 3.15 = 5.04.
# Assert each component equals the closed-form value within float tolerance.
# ---------------------------------------------------------------------------
def test_setup_composition_with_sac():
    print("\n=== Test 12: SETUP composition with SAC ===")
    board = chess.Board(BOARD_FEN)
    # Build a signature matching c3e4's resulting board (the sac-looking candidate).
    b_after_e4 = board.copy(stack=False)
    b_after_e4.push(chess.Move.from_uci("c3e4"))
    pawns_e4, pieces_e4 = reranker_mod._pov_normalized_squares(b_after_e4, chess.WHITE)
    sig_e4 = {
        "pawn_squares": sorted(pawns_e4),
        "piece_squares": _pieces_by_type_dict(b_after_e4, chess.WHITE),
        "snapshot_ply": 14,
    }

    style = _make_style(sufficient=True, sacrifice_frequency=0.15, queens_stay_on_rate=0.5)
    style["setup_signatures"] = [sig_e4]

    cands = [
        {"move": "c3a4", "score": 12, "policy": 0.40},
        {"move": "c3e4", "score": -8, "policy": 0.20},
        {"move": "c3b5", "score": 8,  "policy": 0.30},
        {"move": "c3d5", "score": 30, "policy": 0.10},
    ]
    result = rerank_candidates(
        candidates=cands, style=style, board=board, rng=random.Random(0),
    )
    print(f"  chosen: {result['chosen_move_uci']}; "
          f"signals_applied: {result['bias_breakdown']['signals_applied']}")
    for row in result["bias_breakdown"]["weights"]:
        if row["move"] == "c3e4":
            sac_m = row["sac_multiplier"]
            qt_m = row["qt_multiplier"]
            setup_m = row["setup_multiplier"]
            combo = row["bias_multiplier"]
            print(f"    c3e4: sac_mult={sac_m} qt_mult={qt_m} setup_mult={setup_m} "
                  f"combo={combo}")
            assert abs(sac_m - 1.6) < 1e-3, (
                f"c3e4 sac_mult should be 1.6 (sac_freq=0.15, STYLE_BIAS_STRENGTH=4); "
                f"got {sac_m}"
            )
            assert abs(qt_m - 1.0) < 1e-6, (
                f"c3e4 qt_mult should be 1.0 (centered=0); got {qt_m}"
            )
            # setup_mult: 1 + 2.5 * S where S is c3e4's exact similarity.
            # S = 1.0 exactly (the signature is c3e4's OWN resulting board).
            assert abs(setup_m - (1.0 + reranker_mod.SETUP_SIGNATURE_BIAS_STRENGTH * 1.0)) < 1e-6, (
                f"c3e4 setup_mult should be 1 + 2.5*1.0 = 3.5 (exact match to "
                f"its own resulting board); got {setup_m}"
            )
            expected_combo = sac_m * qt_m * setup_m
            assert abs(combo - expected_combo) < 1e-3, (
                f"c3e4 combo should equal sac*qt*setup = {expected_combo}; got {combo}"
            )
            # Combo must exceed sac or setup alone (multiplicative composition).
            assert combo > sac_m and combo > setup_m, (
                f"composition should exceed either signal alone; "
                f"got combo={combo} sac={sac_m} setup={setup_m}"
            )
            print(f"  [PASS] c3e4: composition sac*qt*setup = "
                  f"{sac_m}*{qt_m}*{setup_m} = {combo} (exceeds either alone)")


# ---------------------------------------------------------------------------
# Test 13: SETUP color-flip normalization -- the spec's <<4 invariant>.
#
# The same setup shape reached as WHITE vs as BLACK (after mirror) must
# produce the SAME canonical signature, and therefore the same
# `setup_mult` for the same resulting position. Locks the
# `_pov_snapshot_squares` color-symmetric pooling contract: a player's
# White and Black setups pool into one signature set so they reinforce
# each other.
#
# Test shape: build a " WHITE setup" board FEN and a "BLACK setup" FEN
# that are vertical mirrors of each other (same pawn skeleton + piece
# placement, just color-flipped). Push the same UCI (polarity-adjusted)
# and verify the candidate's setup_mult against the SAME historic
# signature is identical.
# ---------------------------------------------------------------------------
def test_setup_color_flip_normalization():
    print("\n=== Test 13: SETUP color-flip normalization ===")
    # White POV setup after 1.e4 e5 2.Nf3 Nc6 3.Bc4: a starting-position
    # Italian-shape. Black's mirror is ...e5 ...Nc6 ...Bc5 (mirror of Bc4).
    # We use board_after_white_move (pushed e2e4 mirror: e7e5) and
    # board_after_black_move (pushed e7e5 mirror: e2e4).
    # The KEY property: applying board.mirror() swaps colors AND vertical
    # axis, so a BLACK-side setup mirror maps to the WHITE-side shape
    # exactly. _pov_normalized_squares(b, BLACK) on the original =
    # _pov_normalized_squares(b.mirror(), WHITE) on the mirror.

    # White just played 1.e4: FEN with board.turn=BLACK (we're profiling
    # a WHITE-playing opponent; POV=WHITE).
    after_e4_white = chess.Board(
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    )
    # Black just played 1...e5: FEN with board.turn=WHITE (profiling a
    # BLACK-playing opponent; POV=BLACK).
    after_e5_black = chess.Board(
        "rnbqkbnr/pppp1ppp/8/4p3/8/8/PPPPPPPP/RNBQKBNR w KQkq e6 0 2"
    )

    pawns_w, pieces_w = reranker_mod._pov_normalized_squares(after_e4_white, chess.WHITE)
    pawns_b, pieces_b = reranker_mod._pov_normalized_squares(after_e5_black, chess.BLACK)

    assert pawns_w == pawns_b, (
        f"POV-normalized pawns should be identical for color-flipped "
        f"setups; got white={sorted(pawns_w)} black={sorted(pawns_b)}"
    )
    assert pieces_w == pieces_b, (
        f"POV-normalized pieces should be identical for color-flipped "
        f"setups; got white={sorted(pieces_w)} black={sorted(pieces_b)}"
    )
    print(f"  [PASS] POV-white pawns == POV-black pawns (after mirror)")
    print(f"  [PASS] POV-white pieces == POV-black pieces (after mirror)")

    # Now run the reranker on both boards with the SAME signature and
    # a candidate that mirrors across colors (e2e4 for white-pov board,
    # e7e5 for black-pov board). The setup_mult should be identical
    # because the resulting boards are POV-canonical identical.
    sig = {
        "pawn_squares": sorted(pawns_w),
        "piece_squares": _pieces_by_type_dict(after_e4_white, chess.WHITE),
        "snapshot_ply": 12,
    }

    # White-pov board: bot is white, candidate e4 was already played so
    # we use a NEXT candidate (Nf3 -- g1f3) for the live test. The
    # matching shape will be slightly different but symmetric.
    # Simpler: just check _candidate_setup_mult directly with the
    # resulting boards produced by pushing mirror-moves.
    cand_board_w = after_e4_white.copy(stack=False)
    # white's next move would be on the same side -- skip the push,
    # measure mult on the after-e4 board itself.
    m_w, s_w, ply_w = reranker_mod._candidate_setup_mult(
        cand_board_w, chess.WHITE, [sig]
    )
    # For black-pov, we measure mult on the after-e5 board (mirror)
    # with POV=BLACK.
    cand_board_b = after_e5_black.copy(stack=False)
    m_b, s_b, ply_b = reranker_mod._candidate_setup_mult(
        cand_board_b, chess.BLACK, [sig]
    )
    print(f"  white-pov: setup_mult={m_w:.6f}  S={s_w:.6f}  matched_ply={ply_w}")
    print(f"  black-pov: setup_mult={m_b:.6f}  S={s_b:.6f}  matched_ply={ply_b}")
    assert abs(m_w - m_b) < 1e-9, (
        f"color-flip: setup_mult must be identical for mirror-identical "
        f"setups; got white={m_w} black={m_b}"
    )
    assert abs(s_w - s_b) < 1e-9, (
        f"color-flip: setup_S must be identical for mirror-identical setups; "
        f"got white={s_w} black={s_b}"
    )
    assert ply_w == ply_b == 12
    print(f"  [PASS] color-flip: white-pov setup_mult == black-pov setup_mult "
          f"(={m_w:.6f}); S identical; matched_ply identical")


# ---------------------------------------------------------------------------
# Test 14: SNAPSHOT-WINDOW boundary -- _analyze_game captures only plies
# in [SETUP_SIGNATURE_PLY_MIN, SETUP_SIGNATURE_PLY_MAX].
#
# A game whose mainline ends at ply 9 (no plies in window) yields NO
# snapshots. A game that reaches ply 10+ yields snapshots ONLY for plies
# 10..20. Uses _analyze_game directly (not the reranker) so this is a
# unit test on the style layer's snapshot capture, not the reranker's
# similarity math.
# ---------------------------------------------------------------------------
def test_setup_snapshot_window_boundary():
    print("\n=== Test 14: SNAPSHOT-WINDOW boundary (plies 10-20 only) ===")
    import services.opponent_style as style_mod

    # A 9-ply game (white plays 5 moves, black plays 4; white's last move
    # at ply 9, still below PLY_MIN=10, so no snapshots regardless of
    # whose move it is).
    pgn_short = _minimal_pgn_with_moves(
        "e2e4/e7e5/g1f3/b8c6/f1c4/g8f6/d2d3", "white"
    )
    analyzed = style_mod._analyze_game(pgn_short, "magnus carlsen")
    assert analyzed is not None
    print(f"  short game ({analyzed['plies']} plies): "
          f"{len(analyzed['setup_snapshots'])} snapshots "
          f"(expected 0 -- below PLY_MIN=10)")
    assert analyzed["setup_snapshots"] == [], (
        f"short game (max ply < 10) should have NO snapshots; "
        f"got {analyzed['setup_snapshots']}"
    )
    print(f"  [PASS] short game ({analyzed['plies']} plies) yields 0 snapshots "
          f"(below window)")

    # A 20-ply game reaching plies 11..19 for the profiled WHITE side
    # (Italian Game classical mainline). The 5 in-window plies are
    # 11, 13, 15, 17, 19 (White's moves 6-10), so we expect 5 snapshots.
    pgn_long = _minimal_pgn_with_moves(
        "e2e4/e7e5/g1f3/b8c6/f1c4/f8c5/c2c3/g8f6/d2d3/d7d6/e1g1/a7a6/"
        "b2b4/c5b6/d3d4/e5d4/c3d4/c6e5/f3e5/d6e5",
        "white",
    )
    analyzed = style_mod._analyze_game(pgn_long, "magnus carlsen")
    assert analyzed is not None
    print(f"  long game ({analyzed['plies']} plies): "
          f"{len(analyzed['setup_snapshots'])} snapshots captured")
    print(f"    snapshot plies: "
          f"{[s['snapshot_ply'] for s in analyzed['setup_snapshots']]}")
    # All captured plies must be in [10, 20].
    for snap in analyzed["setup_snapshots"]:
        ply = snap["snapshot_ply"]
        assert style_mod.SETUP_SIGNATURE_PLY_MIN <= ply <= style_mod.SETUP_SIGNATURE_PLY_MAX, (
            f"snapshot ply {ply} outside [{style_mod.SETUP_SIGNATURE_PLY_MIN}, "
            f"{style_mod.SETUP_SIGNATURE_PLY_MAX}]"
        )
    # Every snapshot must have the required keys.
    for snap in analyzed["setup_snapshots"]:
        assert "pawn_squares" in snap and isinstance(snap["pawn_squares"], list)
        assert "piece_squares" in snap and isinstance(snap["piece_squares"], dict)
        for letter in ("N", "B", "R", "Q", "K"):
            assert letter in snap["piece_squares"], (
                f"piece_squares missing key '{letter}' in {snap}"
            )
    print(f"  [PASS] all snapshot plies inside [10, 20]; "
          f"all required keys present (pawn_squares, piece_squares[NBRQK], "
          f"snapshot_ply)")


# ---------------------------------------------------------------------------
# Helpers for Test 10/12/14: build a signature or PGN from raw inputs.
# ---------------------------------------------------------------------------
def _pieces_by_type(board: chess.Board, color: chess.Color) -> Dict:
    """Return {piece_type: [square_names]} for `color`'s pieces on `board`.

    Board is read as-is (not mirrored). For setup signatures consumed
    by the reranker, callers must mirror the board first if profiling
    BLACK -- see _pov_normalized_squares.
    """
    out: Dict[int, List[str]] = {}
    for ptype in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING):
        out[ptype] = sorted(
            chess.square_name(sq) for sq in board.pieces(ptype, color)
        )
    return out


def _pieces_by_type_dict(board: chess.Board, color: chess.Color) -> Dict[str, List[str]]:
    """Same as _pieces_by_type but keyed by single letters (N/B/R/Q/K)."""
    pieces = _pieces_by_type(board, color)
    return {
        "N": pieces[chess.KNIGHT],
        "B": pieces[chess.BISHOP],
        "R": pieces[chess.ROOK],
        "Q": pieces[chess.QUEEN],
        "K": pieces[chess.KING],
    }


def _minimal_pgn_with_moves(uci_moves: str, opponent_color: str) -> str:
    """Build a minimal PGN where the named side plays the given UCI moves.

    The PGN header marks the opponent as White or Black (casefold names
    'magnus_carlsen' or 'carlsen' resolve via _opponent_color). The
    mainline is just the given UCI moves back-and-forth; if the last
    ply is the opponent's, the count works out; otherwise the trailing
    side gets one extra move. Move legality is the caller's
    responsibility (we DON'T validate; malformed inputs surface as a
    ValueError from python-chess).
    """
    white = "Magnus Carlsen" if opponent_color == "white" else "Anon"
    black = "Anon" if opponent_color == "white" else "Magnus Carlsen"
    board = chess.Board()
    sans = []
    for uci in uci_moves.split("/"):
        mv = chess.Move.from_uci(uci)
        sans.append(board.san(mv))
        board.push(mv)
    # Pad to even length so the mainline cleanly ends on the opponent's
    # move (or whatever; the exact end-side doesn't matter for window
    # boundary testing -- the snapshot gate is per-ply either way).
    pgn = f'[Event "Tst"]\n[White "{white}"]\n[Black "{black}"]\n\n'
    pgn += " ".join(sans) + "\n"
    return pgn


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
    test_high_vs_low_queen_trade_preference()
    test_queen_trade_timing_window()
    test_policy_based_base_weight()
    test_base_source_mixed()
    test_setup_signature_head_to_head()
    test_setup_insufficient_data()
    test_setup_composition_with_sac()
    test_setup_color_flip_normalization()
    test_setup_snapshot_window_boundary()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()