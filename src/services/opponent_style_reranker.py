"""
Style-bias re-ranker for Maia's best_move_candidates() output.

This module consumes three inputs and produces one choice:

  1. `candidates`: the list[dict] returned by Maia3Engine.best_move_candidates()
     -- each {"move": uci, "score": cp|None, "wdl": {win,draw,loss}|None}.
     This is Maia's ranked top-N moves by policy probability (rank 1 first).

  2. `style`: the dict returned by compute_opponent_style() -- contains
     "sufficient", "sacrifice_frequency", "opening_family_lean", etc.,
     i.e. an aggregate description of what kind of player this opponent is.

  3. `board`: the chess.Board the candidates are FOR, so we can push
     candidates onto a copy to check whether each is sacrifice-looking.

The output is a single chosen move plus transparency fields. See the
return-shape docstring on `rerank_candidates`.

================================================================================
DESIGN DECISIONS (recorded here because they shape the whole module)
================================================================================

(1) OPENING_FAMILY_LEAN IS NOT USED AS A PER-CANDIDATE BIAS in this v1
    re-ranker -- it is surfaced in the returned dict but never biases the
    weighted sample.

    Reasoning: family-lean is computed from the opening's early moves
    (the [Opening] / [ECOUrl] header on a completed game's PGN). The
    re-ranker fires once pick_repertoire_move() has already returned None
    -- i.e. once we are out of the opponent's known book -- so by
    definition the position is no longer in a recognizable opening
    family in any per-candidate sense. Without a classifier that tells
    us "candidate X leads to Sicilian-family positions" -- which would
    require an opening book / ECO lookup over the resulting position --
    family-lean has no per-candidate bias to apply.

    A bounded-scope alternative (apply family-lean only within the first
    N plies, deriving the current family from board.move_stack) was
    considered and rejected: pick_repertoire_move already covers the
    in-book phase, so the bounded scope would largely duplicate existing
    behavior. The signal lives in the style profile and waits for a
    different consumer (e.g. an opening-prep suggestion layer that
    classifies candidates by ECO family). The re-ranker surfaces
    "opening_family_lean" in the returned dict so that consumer / future
    UI features can read it, but does NOT use it for bias.

(2) LIVE SACRIFICE PROXY (see _is_live_sac_move): a candidate is
    "sacrifice-looking" iff BOTH:

      (a) NET MATERIAL AT STAKE meets SAC_MATERIAL_THRESHOLD. The moved
          piece's value minus the value of whatever the candidate itself
          captures (0 for a non-capture) must be >= SAC_MATERIAL_THRESHOLD.
          This nets out even trades that the raw "moved piece hangs to a
          cheaper attacker" check would otherwise flag -- e.g. NxB
          (knight=3, captured bishop=3) followed by ...pxN leaves a net
          material swing of 0, which is NOT a sacrifice in any meaningful
          sense. The threshold is shared with the offline heuristic so
          the live proxy and the offline aggregate agree on what counts.

      (b) OPPONENT CAN PROFITABLY RECAPTURE. The cheapest opponent
          attacker of the moved piece's destination square is worth
          strictly less than the moved piece's own value (so the opponent
          gains material by taking). The recapture check uses the moved
          piece's raw value -- the piece we already captured is gone from
          the opponent's perspective and doesn't enter their recapture
          math.

    Why both gates: (a) alone would flag a hanging knight that captured
    nothing (net_loss=3, threshold met) -- not enough, because the
    opponent might not be able to take it. (b) alone would flag NxB
    recaptured by a pawn -- not enough, because the captured-bishop
    comp makes the trade even. Together they isolate the case the
    offline heuristic is actually trying to detect: material given up
    without recoup, with the recoup available to the opponent right now.

    This is a live proxy for the OFFLINE aggregate signal
    (compute_opponent_style's "material drops by >=3 over a 3-ply
    look-ahead and doesn't recoup"). The offline heuristic needs future
    moves (the 3-ply window) to confirm the material doesn't come back;
    the live re-ranker scoring a not-yet-played candidate can't observe
    the future. The proxy instead reasons about OPPORTUNITY: if the
    opponent (whose turn it'll be after our candidate) CAN profitably
    capture the moved piece (cheapest attacker of the moved-piece's
    destination square is worth less than the moved piece's own value)
    AND the net material swing meets the threshold, the precondition for
    "material dropped without recoup within 3 plies" is satisfied -- the
    opponent probably captures, we probably don't get it back. This
    holds whether or not we have a defender: a defender recapture
    exchanges even or at a loss when the opponent's attacker is cheaper,
    which is exactly the "material given up" regime.

(3) BIAS MECHANISM: RE-WEIGHTED SAMPLE, not straight re-rank.

    For each candidate i, compute a base weight from its rank (rank-1
    gets the highest) -- Maia's actual softmax probabilities are NOT
    exposed in the candidate dict (see best_move_candidates docstring:
    only {move, score, wdl}), so we use rank-decay as a coarse base rate
    that preserves Maia's "rank 1 is overwhelmingly most likely for a
    1500 player" prior. The bias then multiplies this base weight
    upwards for sacrifice-looking candidates, scaling with the
    opponent's sacrifice_frequency.

    Straight re-ranking (always pick the highest-weighted candidate) is
    rejected because it would always force the sac-looking move to be
    played regardless of its base-rate -- a high-sac OPPONENT may have a
    sac-looking move at #5 with rank probability <5%, and forcing that
    move throws away Maia's human-like calibration. Re-weighted sample
    makes a high-sac opponent MORE LIKELY to pick the sac-looking
    candidate, but still lets them pick the quiet top candidate most of
    the time -- which is the human-like behavior we want for sparring.

    This mirrors pick_repertoire_move's use of random.choices with
    weighted frequencies, so it's idiomatic to this codebase.

================================================================================
CONSTANTS
================================================================================

STYLE_BIAS_STRENGTH (alpha) tunes how aggressively a high
sacrifice_frequency boosts sac-looking candidates. Picked via closed-form
calibration against the empirical range seen in opponent_style_test:

  Effective relative weight of a sac-looking candidate vs. a quiet one:
    weight_sac   = base * (1 + alpha * sac_freq * 1)   # indicator=1
    weight_quiet  = base * 1.0
    ratio         = 1 + alpha * sac_freq

  Calibration points (alpha = 4.0):
    sac_freq = 0.01 (very low)  -> ratio = 1.04  (barely moves the needle;
                                                 Maia #1 almost always wins)
    sac_freq = 0.05 (medium)    -> ratio = 1.20  (moves some close calls)
    sac_freq = 0.10 (high)      -> ratio = 1.40  (strong tilt toward sacs)
    sac_freq = 0.15 (very high) -> ratio = 1.60  (sac-looking #2 often
                                                 beats quiet #1 if their
                                                 base weights are close)

  These cover the range empirical-opponent_style_test fixture C produced:
  0.065 unweighted / 0.119 recency-weighted for the high-sac stretch.

BASE_RANK_DECAY sets how steeply base weights fall with rank. We use
geometric base=0.5: rank 1 -> 1.0, rank 2 -> 0.5, rank 3 -> 0.25, ...

  Justification: Maia's topk softmax typically concentrates 40-70% of
  mass on rank 1 and decays steeply (the standard "human-like" move
  distribution is long-tailed but rank-1-dominant). Base=0.5 reproduces
  that shape coarsely without claiming fidelity we don't have (since
  Maia's probs aren't exposed). When actual probs ARE eventually exposed
  in the candidate dict, swap the base weight formula to use them and
  drop BASE_RANK_DECAY.

FAMILY_LEAN_DISABLED is a sentinel marking where family-lean WOULD be
applied if/when a candidate-family classifier ships. It is returned in
the bias_breakdown so that consumers can verify v1 made a no-bias
decision rather than silently swallowing the signal.
"""
import logging
import random
from typing import Any, Dict, List, Optional

import chess

from services.opponent_style import (
    SAC_MATERIAL_THRESHOLD,
    _PIECE_VALUE,
)

log = logging.getLogger(__name__)

# How strongly a high sacrifice_frequency boosts sac-looking candidates.
# See the module docstring's calibration table above.
STYLE_BIAS_STRENGTH = 4.0

# Base weight for rank r in 1..N: weight = BASE_RANK_DECAY ** (r - 1).
# See the module docstring's "BASE_RANK_DECAY" paragraph above.
BASE_RANK_DECAY = 0.5

# Sentinel returned in bias_breakdown's "family_lean" key to mark that v1
# made an explicit no-bias decision rather than silently dropping the
# signal. See the module docstring's decision (1).
FAMILY_LEAN_DISABLED = "disabled_in_v1_no_candidate_family_classifier"


def _base_weight(rank: int) -> float:
    """Geometric base weight from a 1-indexed rank.

    rank=1 -> 1.0, rank=2 -> 0.5, rank=3 -> 0.25, ... This is a coarse
    proxy for Maia's softmax probability, which is not exposed in the
    candidate dict (see the module docstring's "BASE_RANK_DECAY" note
    for the trade and the upgrade path).
    """
    return BASE_RANK_DECAY ** max(0, rank - 1)


def _is_live_sac_move(board_before: chess.Board, candidate_uci: str) -> bool:
    """Static live proxy for the offline sacrifice heuristic.

    See the module docstring's decision (2) for the full rule
    justification. Short version: a candidate is "sacrifice-looking" iff
    BOTH (a) the net material at stake -- the moved piece's value minus
    whatever the candidate itself captured -- meets SAC_MATERIAL_THRESHOLD,
    AND (b) after pushing the candidate on a board copy, the moved piece
    is attacked by an opponent piece whose value is strictly less than
    the moved piece's value (so the opponent can profitably recapture).

    The net-material gate (a) is what stops even trades (e.g. NxB then
    ...pxN) from being misflagged as sacrifices -- without it, the raw
    "knight hangs to a cheaper pawn" check would fire on a 0-net-swing
    trade that isn't a sacrifice in any meaningful sense. The threshold
    is shared with compute_opponent_style's offline heuristic so the
    live proxy and the aggregate agree on what "sac-looking" means.

    Returns False defensively on any edge case (non-legal candidate,
    non-piece move, pawn moves (below threshold), candidates that leave
    the opponent in check (king-safety modeling is out of scope for v1)).
    """
    try:
        candidate = chess.Move.from_uci(candidate_uci)
    except ValueError:
        return False
    if candidate not in board_before.legal_moves:
        # Maia's candidates are legal by construction; defensive guard.
        return False

    piece = board_before.piece_at(candidate.from_square)
    if piece is None:
        return False

    value = _PIECE_VALUE.get(piece.piece_type, 0)
    if value < SAC_MATERIAL_THRESHOLD:
        # Pawns (value 1) and low-value pieces don't register -- matches
        # the offline heuristic, which also skips pawn gambits in v1.
        return False

    # Net material at stake = moved piece's value MINUS whatever the
    # candidate itself captures. The captured piece's comp is already in
    # our favor (we took it), so it nets out the loss from a potential
    # recapture. Without this, an even trade like NxB (3-3=0) followed
    # by ...pxN would be misflagged -- the raw "knight hangs to a pawn"
    # signal fires when the actual net swing is zero.
    captured_piece = board_before.piece_at(candidate.to_square)
    captured_value = (
        _PIECE_VALUE.get(captured_piece.piece_type, 0)
        if captured_piece is not None else 0
    )
    net_loss = value - captured_value
    if net_loss < SAC_MATERIAL_THRESHOLD:
        # Net material at stake below the sacrifice threshold -- even
        # if the opponent recaptures, we're not down >= threshold points
        # net. Short-circuits before the (more expensive) attacker
        # enumeration below.
        return False

    board_after = board_before.copy(stack=False)
    try:
        board_after.push(candidate)
    except (AssertionError, ValueError):
        # python-chess pushes should not fail here (the move is legal),
        # but if it does, treat as not-sac-looking rather than crash.
        return False

    # Skip checks -- king-safety modeling is out of scope for v1's
    # static proxy (a candidate leaving the opponent in check doesn't
    # allow the opponent to recapture normally; their first move is
    # forced to address the check). Conservative default: not sac-looking.
    if board_after.is_check():
        return False

    opp_color = board_after.turn  # the side to move AFTER our candidate

    attackers = board_after.attackers(opp_color, candidate.to_square)
    if not attackers:
        return False

    # Cheapest opponent attacker value.
    min_attacker_value = min(
        _PIECE_VALUE.get(board_after.piece_at(sq).piece_type, 0)
        for sq in attackers
    )

    # The opponent can profitably recapture iff their cheapest attacker
    # is worth strictly less than the moved piece's raw value. The
    # captured piece we already took is gone from the opponent's POV, so
    # it doesn't enter their recapture incentives -- this check is
    # independent of `captured_value`. If equal or greater, the
    # recapture trades even or loses material for the opponent -- they
    # wouldn't take, so the moved piece isn't "hanging" in the meaningful
    # sense of "material given up without recoup".
    # NB: KING = 0 in _PIECE_VALUE, so "king attacks knight" counts as
    # "sac-looking" -- modeling king safety beyond this static check is
    # a v2 concern (see the module docstring's documented false-positive
    # edge case).
    return min_attacker_value < value


def rerank_candidates(
    *,
    candidates: List[Dict[str, Any]],
    style: Dict[str, Any],
    board: chess.Board,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """Combine Maia's candidates with an opponent's style profile.

    Args:
        candidates: Ranked list of dicts from best_move_candidates(). Each
            dict must have at least a "move" key (UCI string); "score"
            and "wdl" are present in the prod shape but not required by
            this function. Must be non-empty and ordered rank 1 first.
            If empty, returns chosen_move_uci="" + applied_bias=False
            so the caller can fall through to its own default handling.
        style: Dict from compute_opponent_style(). Must contain at
            least "sufficient" (bool); when False, behavior is
            identical to unbiased Maia (top candidate returned) and no
            bias is applied -- this is the regression contract.
        board: The chess.Board the candidates are FOR (the same board
            best_move_candidates was called with). Used only to push
            candidate copies for live-sac detection -- never mutated.
        rng: Optional pre-seeded random.Random instance; if None, the
            module-level `random` is used (matches pick_repertoire_move's
            pattern). Tests should pass a seeded Random for
            reproducibility; production should pass None.

    Returns:
        dict with:
            chosen_move_uci: str        -- the chosen candidate's UCI
            chosen_index: int           -- index in the input list
            applied_bias: bool          -- True iff sufficient=True AND
                                           at least one candidate was
                                           sac-looking (i.e. real bias
                                           was applied to the sample).
                                           False if insufficient or no
                                           candidate triggered the
                                           sac indicator.
            source: str                  -- "style_biased_sacrifice" |
                                           "default_top_candidate"   |
                                           "insufficient_data"        |
                                           "no_candidates"
            sacrifice_frequency: float|None -- surfaced from style for
                                               transparency; what the
                                               caller can use to AUDIT
                                               what the re-ranker saw.
            opening_family_lean: dict|None -- surfaced from style for
                                              transparency (NOT used as
                                              a bias -- see decision 1
                                              in the module docstring).
            bias_breakdown: dict|None  -- per-candidate weight trace
                                           so callers can introspect
                                           what the re-ranker actually
                                           saw and how it weighted.
                                           Only populated when
                                           applied_bias=True.
            game_count: int             -- surfaced from style, raw
                                           game count, for transparency

    Contracts:
      * Insufficient data (style["sufficient"]=False): returns
        candidates[0] deterministically, applied_bias=False,
        source="insufficient_data" -- the regression contract.
      * Sufficient data but no candidate is sac-looking: returns
        candidates[0] deterministically (the rank-1 base weight is the
        maximum possible; with no bias differential the sampler can
        only ever pick rank 1), applied_bias=False,
        source="default_top_candidate".
      * Sufficient data and >=1 candidate is sac-looking: weighted
        random sample using weights[i] = base(rank_i) *
        (1 + alpha * sac_freq * sac_indicator_i). applied_bias=True,
        source="style_biased_sacrifice", bias_breakdown populated.

    Idempotency / purity:
      * Does not mutate `board` (uses board.copy(stack=False)).
      * Does not mutate `candidates`.
      * Reads `style` but does not mutate it.
    """
    if not candidates:
        return {
            "chosen_move_uci": "",
            "chosen_index": -1,
            "applied_bias": False,
            "source": "no_candidates",
            "sacrifice_frequency": style.get("sacrifice_frequency"),
            "opening_family_lean": style.get("opening_family_lean"),
            "bias_breakdown": None,
            "game_count": style.get("game_count", 0),
        }

    # --- contract: insufficient data is the regression path ---------------
    if not style.get("sufficient", False):
        top = candidates[0]
        return {
            "chosen_move_uci": top.get("move", ""),
            "chosen_index": 0,
            "applied_bias": False,
            "source": "insufficient_data",
            "sacrifice_frequency": style.get("sacrifice_frequency"),
            "opening_family_lean": style.get("opening_family_lean"),
            "bias_breakdown": None,
            "game_count": style.get("game_count", 0),
        }

    sac_frequency = style.get("sacrifice_frequency") or 0.0

    # --- compute per-candidate live-sac indicator and weights --------------
    # base_weight_i = geometric rank-decay. bias_multiplier_i depends on
    # whether the candidate flagged as sac-looking. The multiplicative
    # form preserves rank-decay as the base rate; bias is a relative tilt
    # applied on top.
    weights: List[float] = []
    sac_indicators: List[bool] = []
    breakdown_rows: List[Dict[str, Any]] = []
    for idx, candidate in enumerate(candidates):
        uci = candidate.get("move", "")
        is_sac = _is_live_sac_move(board, uci)
        sac_indicators.append(is_sac)
        base = _base_weight(idx + 1)
        bias_mult = 1.0 + STYLE_BIAS_STRENGTH * sac_frequency * (1.0 if is_sac else 0.0)
        weight = base * bias_mult
        weights.append(weight)
        breakdown_rows.append({
            "index": idx,
            "move": uci,
            "rank": idx + 1,
            "base_weight": round(base, 4),
            "sac_indicator": is_sac,
            "bias_multiplier": round(bias_mult, 4),
            "weight": round(weight, 4),
        })

    # --- if no candidate is sac-looking, the bias has no effect -- reflect
    # that honestly in the result
    any_sac = any(sac_indicators)
    if not any_sac:
        # Sampling would still be a no-op: weights are all just
        # geometric rank-decay, and rank 1 has the largest weight. The
        # sampler MIGHT pick a non-top candidate (it's random), but
        # statistically the expected choice is rank 1 -- and "no bias
        # applied" is the honest source label. Return rank 1
        # deterministically: same behavior the insufficient path uses,
        # same behavior as today's default Maia, no random drift for
        # consumers that didn't opt into style biasing.
        top = candidates[0]
        return {
            "chosen_move_uci": top.get("move", ""),
            "chosen_index": 0,
            "applied_bias": False,
            "source": "default_top_candidate",
            "sacrifice_frequency": sac_frequency,
            "opening_family_lean": style.get("opening_family_lean"),
            "bias_breakdown": None,
            "game_count": style.get("game_count", 0),
        }

    # --- weighted sample ----------------------------------------------------
    sampler = rng if rng is not None else random
    chosen_index_in_weights = sampler.choices(
        list(range(len(candidates))),
        weights=weights,
        k=1,
    )[0]
    chosen_in_weights: int = chosen_index_in_weights

    return {
        "chosen_move_uci": candidates[chosen_in_weights].get("move", ""),
        "chosen_index": chosen_in_weights,
        "applied_bias": True,
        "source": "style_biased_sacrifice",
        "sacrifice_frequency": sac_frequency,
        "opening_family_lean": style.get("opening_family_lean"),
        "bias_breakdown": {
            "weights": breakdown_rows,
            "family_lean": FAMILY_LEAN_DISABLED,
        },
        "game_count": style.get("game_count", 0),
    }