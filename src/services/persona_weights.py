"""
Persona weight vectors (Attacker + Sacrificer) for the Engine Sparring reranker.

This module sits BETWEEN the shared feature extractor (persona_features.py,
which produces StyleScores) and the bounding gate (persona_bounds.py, whose
bounded_persona_bias() consumes this module's output). It provides:

  * normalize_style_scores(): maps the heterogeneous raw StyleScores fields
    onto comparable scales -- the unbounded signed gains are squashed into
    [-1, 1] with a smooth tanh curve; the already-bounded signals pass
    through unchanged.
  * canonicalize_by_score(): the pipeline's INPUT ADAPTER -- re-sorts the
    raw list returned by StockfishEngine.suggest() into strict descending
    score_cp order, because MultiPV search order is not guaranteed to be
    cp-sorted (see the function's docstring for the observed inversions).
  * attacker_score() / sacrificer_score(): the first two persona weight
    presets. Each applies phase gating internally (per the ARCHITECTURE NOTE
    in persona_bounds.py), then normalizes, then takes the weighted sum of
    the normalized fields. Both return a single raw_persona_score float in
    [-1, 1], ready for bounded_persona_bias(raw, engine_norm_cp, phase).

Defender and Positional personas are deliberately NOT built here (later
task). This module must not modify persona_features.py or persona_bounds.py.

NOTE ON SIGNATURES: the task sketch showed one-argument weight functions,
but PART 3 requires game_phase(board) to be applied INSIDE the persona
weight functions (persona_bounds.py's architecture note), so both take the
board as a second parameter and call game_phase(board) themselves.

WHY canonicalize_by_score() LIVES HERE (input-adapter placement)
================================================================
This module owns the reranker's per-candidate RANKING pipeline: the raw
suggest() list enters here, and persona_adjusted_score() is the final
per-candidate ranking number that leaves here. The canonicalized list is
that pipeline's input contract: every consumer that treats suggest() order
as a ranking (the integration/real-game harnesses below, and the future
reranker call site) must canonicalize FIRST, so that "ENGINE order" in any
debug output is guaranteed true cp order and "(persona order unchanged
from engine order)" can only mean the persona genuinely contributed
nothing -- never "the list needed re-sorting anyway". stockfish_engine.py
itself is deliberately NOT modified: this is a call-site concern, and the
engine wrapper's contract (returns Stockfish's own MultiPV order) is
correct as documented.

NORMALIZATION REFERENCE CONSTANT (STYLE_GAIN_REF)
=================================================
attack_gain and defense_gain are unbounded (~[-8, +20] documented typical
range, heterogeneous units). They are squashed with:

    normalized = tanh(value / STYLE_GAIN_REF)

Why tanh (and why not a min-max clip): a hard linear clip on the documented
[-8, +20] window maps EVERY value beyond it to the same endpoint, so the
biggest attacking swings (mates, multi-piece sacrifices) become
indistinguishable from each other and from +20 exactly where persona
differentiation matters most. tanh is smooth, odd-symmetric (0 -> 0,
sign-preserving), strictly monotonic, and asymptotic to +/-1, so outliers
compress gracefully instead of clipping.

Choosing the constant: the anchor is the strongest attack event a real
position produces. In the live fixture dump that is the mate-in-one Qxg7# at
attack_gain = +15.00 (king-zone pressure +9, adjacent attacks +3, check +1,
new open line +1, escape squares +1) -- the "queen lands in the king's ring
with an open line" ceiling. We want that ceiling to map to ~0.8, leaving
headroom above it:

    tanh(15 / REF) = 0.8  ->  15 / REF = atanh(0.8) = 0.5 * ln(9) = 1.09861
    REF_exact = 15 / 1.09861 = 13.654     chosen REF = 14.0

tanh(15 / 14) = 0.791 -- a shade under 0.8 (slightly more conservative
headroom above the mate-class ceiling). What that buys at other points:

    +20 (documented raw ceiling)  -> 0.891: still clearly separated from the
                                      0.791 mate-class value.
    +2  (Greek gift Bxh7+, small attack contribution under the by-design
         delta scoring)           -> 0.143: small but clearly positive.
    -8  (documented negative floor) -> -0.515: symmetric via tanh's oddness.

One constant is shared by attack_gain and defense_gain because both are sums
over the same subcomponent families and units philosophy; a shared reference
keeps the two directly comparable.

PHASE GATING -- what is damped and what is not (see _phase_damped_scores)
========================================================================
game_phase(board) (persona_bounds.py) runs INSIDE both persona functions --
that is why they take the board as their second argument. persona_bounds.py
pins this: bounded_persona_bias() only sees the final aggregate, so it
cannot selectively damp king-pressure terms, and damping the whole
aggregate there would also damp signals that should survive into the
endgame. The damping factor applied here is linear (1 - phase): full weight
at phase 0 (opening/middlegame), zero at phase 1 (bare endgame), smooth in
between -- the same no-cliff philosophy game_phase itself documents.

DAMPED (king-zone-pressure-derived attack subcomponents):
  * attack_sub.king_zone_pressure -- weighted crowding of the enemy king's
    2-square danger zone by whatever non-king pieces remain. As material
    leaves the board this metric trends to zero on its own, and residual
    pressure from the last few pieces converts poorly into a real attack;
    persona_bounds.py's game_phase doc explicitly designates "king-pressure
    terms" for endgame damping.
  * attack_sub.king_adjacent_attacks -- same family, one ring tighter: how
    many squares immediately adjacent to the enemy king are attacked.

NOT DAMPED:
  * attack_sub.checks -- a check is a concrete forcing event, not a pressure
    reading; endgame mating technique (including bare-king mates) runs
    through checks, so a check is as meaningful at phase 1 as at phase 0.
  * attack_sub.open_lines -- sliders with a clear ray into the enemy king's
    zone. King-zone-targeted by construction, but in thin material an open
    line to the king is the main delivery mechanism for whatever attack
    potential remains (rook/queen infiltration, mating nets), so its
    endgame value holds.
  * attack_sub.escape_square_pressure -- confinement of the enemy king
    (escape squares lost) is the core mechanism of endgame mating nets
    (KQvK / KRvK win by shrinking the box); arguably MORE valuable in the
    endgame, not less.
  * sacrifice_signal -- a piece sac in an endgame is still a sacrifice.
  * volatility -- captures/checks/swings stay meaningful in any phase.
  * defense_sub.* -- both personas built here weight defense_gain at 0.0,
    so defense-side damping would be a no-op for them; it is deferred to the
    Defender persona task rather than implemented speculatively.

The damping is applied to the RAW subcomponents BEFORE aggregation and
normalization (that is what the architecture note requires), and attack_gain
is rebuilt as the sum of the damped subcomponents so the documented
invariant attack_gain == sum(attack_sub) keeps holding.

UNIT RECONCILIATION -- persona bias vs engine centipawns
========================================================
bounded_persona_bias() returns score*trust in [-1, 1], while engine_norm_cp
is in raw centipawns. The original pipeline plan added them directly, which
makes the persona bias 1-2 orders of magnitude too small to ever change a
candidate's order (a +0.08 bias competing against an 8cp gap does nothing).
This layer owns the reconciliation:

    final = engine_norm_cp + PERSONA_BIAS_CP * bounded_persona_bias(...)

PERSONA_BIAS_CP = 100.0 -- a fully-trusted, maximally-preferred persona bias
is "worth" at most one pawn. Sizing reasoning:
  * it must reach the near-equal band (~20cp gaps, the regime the reranker
    exists for): realistic persona scores (0.1-0.8) at near-full trust then
    yield ~8-80cp of pull, enough to genuinely contest 1-20cp gaps;
  * B=75 (= max_cp_drop) was checked and FAILS: the near-equal fixture's
    Qd3 flip needs 8.07cp of bias against an 8.00cp gap, and 0.0807*75 =
    6.05 < 8.00 -- no reorder. B=100 yields 8.07 > 8.00;
  * B=150+ would hand a persona 1.5+ pawns of eval authority -- too much.

Trust contract ("a candidate more than ~75cp below the engine's best should
never plausibly win regardless of persona score"), enforced by two
mechanisms together:
  * rescue side -- engine_trust() is EXACTLY 0.0 at norm <= -max_cp_drop, so
    the scaled bias contributes exactly 0 there regardless of persona score
    (max gains elsewhere: ~59cp at -10, ~35cp at -20, ~12cp at -40, ~4cp at
    -60);
  * demotion side -- a maximally style-hated near-best move (bias -1.0 at
    trust 1.0) would otherwise sink to -100 and let a deep trust-zeroed
    candidate win BY DEFAULT. persona_adjusted_score() therefore floors the
    final at -max_cp_drop, but ONLY for candidates that started inside the
    trust window (norm > -max_cp_drop); candidates already at
    norm <= -max_cp_drop keep final == norm exactly, so the floor can never
    LIFT a hard-rejected candidate.

persona_bounds.py itself is NOT modified: the unit conversion is a
caller-side concern of this layer, and bounded_persona_bias()'s documented
[-1, 1] output contract stays untouched.
"""
import math
from dataclasses import replace

import chess

from services.persona_bounds import bounded_persona_bias, game_phase
from services.persona_features import StyleScores

# Full derivation in the module docstring (anchor: Qxg7# at +15.00 -> ~0.8).
STYLE_GAIN_REF = 14.0


# --- suggest() output canonicalization (pipeline input adapter) ---------------

def canonicalize_by_score(suggestions: list) -> list:
    """Re-sort raw StockfishEngine.suggest() output into strict descending
    score_cp order. Returns a NEW list; the input is never mutated.

    WHY THIS EXISTS -- MultiPV list order is not guaranteed cp-sorted
    ------------------------------------------------------------------
    suggest() returns candidates in Stockfish's own MultiPV search order,
    and at adjacent ranks that order is NOT always descending by score_cp.
    Observed live in 2 of the 9 real-game test positions
    (persona_real_games_test.py):

      * game 0 move 10: Bh3+ (score_cp 101) listed BEFORE Nh3 (111);
      * game 1 move 22: Qb4 (400) listed BEFORE Qb7 (411).

    The scores themselves are fine -- only their LIST positions were
    swapped. Every consumer that treats the raw list order as a ranking
    silently trusts an assumption that is sometimes false; this helper is
    the one place that assumption is made true.

    Contract:
      * strictly descending by score_cp after the call;
      * STABLE: entries with EQUAL score_cp keep their original relative
        order (Python's sorted() guarantee). A cp tie is genuinely
        unrankable by the engine alone, and the caller's own tie-breaking
        (e.g. the persona bias) stays in charge;
      * returns a new list; the input list and its dicts are untouched;
      * expects the plain dicts suggest() returns
        ({"uci", "san", "score_cp"}); no other schema is assumed and no
        chess validation is performed.

    Where it must be called: at every call site that consumes suggest()
    output as a RANKING, before any norm/bias math or display. See the
    module docstring's placement note.
    """
    return sorted(suggestions, key=lambda s: -s["score_cp"])


def _squash_signed(value: float) -> float:
    """Smooth monotonic squash of an unbounded value into (-1, 1)."""
    return math.tanh(value / STYLE_GAIN_REF)


def normalize_style_scores(scores: StyleScores) -> dict[str, float]:
    """Map every raw StyleScores field onto a comparable scale.

    Returns a dict with the SAME field names as StyleScores:
      * attack_gain, defense_gain -> tanh-squashed into (-1, 1),
      * sacrifice_signal, volatility -> pass-through (already [0, 1]),
      * initiative_proxy -> pass-through (always 0.0).
    """
    return {
        "attack_gain": _squash_signed(scores.attack_gain),
        "defense_gain": _squash_signed(scores.defense_gain),
        "sacrifice_signal": scores.sacrifice_signal,
        "volatility": scores.volatility,
        "initiative_proxy": scores.initiative_proxy,
    }


def _phase_damped_scores(scores: StyleScores, phase: float) -> StyleScores:
    """Copy of `scores` with king-pressure-derived attack subcomponents
    scaled by (1 - phase).

    Which subcomponents this touches (and which it deliberately does not) is
    documented in the module docstring PHASE GATING section. attack_gain is
    rebuilt from the damped subcomponents to preserve the invariant
    attack_gain == sum(attack_sub). sacrifice_signal, volatility, and the
    defense side pass through untouched.
    """
    damp = 1.0 - phase
    damped_attack_sub = replace(
        scores.attack_sub,
        king_zone_pressure=scores.attack_sub.king_zone_pressure * damp,
        king_adjacent_attacks=scores.attack_sub.king_adjacent_attacks * damp,
    )
    damped_attack_gain = (
        damped_attack_sub.king_zone_pressure
        + damped_attack_sub.king_adjacent_attacks
        + damped_attack_sub.checks
        + damped_attack_sub.open_lines
        + damped_attack_sub.escape_square_pressure
    )
    return replace(
        scores,
        attack_sub=damped_attack_sub,
        attack_gain=damped_attack_gain,
        defense_sub=replace(scores.defense_sub),
    )


# --- Attacker weights (first pass; tuned in a later step) ---------------------
# attack_gain      0.70 : dominant by definition -- the persona exists to pick
#                        maximum king pressure. 0.70 rather than ~1.0 so that a
#                        move that is ALSO sharp and sacrificial can outscore a
#                        pure-pressure move; the reranker consumes orderings,
#                        not magnitudes.
# volatility       0.20 : "somewhat positive" -- attacking play tends to be
#                        sharper (captures, checks, big pressure swings), so an
#                        equally-pressuring volatile move slightly outranks a
#                        quiet one.
# sacrifice_signal 0.10 : "mildly positive" -- attacks and sacrifices
#                        correlate (established in the design), but a direct
#                        non-sacrificial kill (e.g. a mate-in-one queen
#                        capture) must NOT be penalized for not being a
#                        gamble, so this stays a small tie-breaker, never a
#                        driver.
# defense_gain     0.00 : deliberately NOT negative. A move can be both
#                        defensively sound AND attacking (block a check AND
#                        create a threat); punishing defense would push the
#                        persona away from objectively good moves.
# initiative_proxy 0.00 : no signal exists (always 0.0 by design in
#                        persona_features; would require lookahead).
# The weights sum to exactly 1.0, so the weighted sum of inputs that each
# live in [-1, 1] is guaranteed to land in [-1, 1] with no clipping needed.
_ATTACKER_W_ATTACK = 0.70
_ATTACKER_W_VOLATILITY = 0.20
_ATTACKER_W_SACRIFICE = 0.10
_ATTACKER_W_DEFENSE = 0.0
_ATTACKER_W_INITIATIVE = 0.0


def attacker_score(scores: StyleScores, board: chess.Board) -> float:
    """Attacker persona raw score in [-1, 1] (bounded_persona_bias input).

    `board` is the position BEFORE the move (the same position the scores
    were computed on); it is used only for game_phase().
    """
    phase = game_phase(board)
    n = normalize_style_scores(_phase_damped_scores(scores, phase))
    total = (
        _ATTACKER_W_ATTACK * n["attack_gain"]
        + _ATTACKER_W_VOLATILITY * n["volatility"]
        + _ATTACKER_W_SACRIFICE * n["sacrifice_signal"]
        + _ATTACKER_W_DEFENSE * n["defense_gain"]
        + _ATTACKER_W_INITIATIVE * n["initiative_proxy"]
    )
    return max(-1.0, min(1.0, total))


# --- Sacrificer weights (first pass; tuned in a later step) -------------------
# sacrifice_signal 0.70 : "very heavily" -- definitionally almost the whole
#                         signal: the persona exists to find and reward
#                         material gambles, so the binary 1.0 lands 0.70 of
#                         the score on its own and dominates every other term.
# attack_gain      0.20 : "moderately positive" -- sacrifices are usually in
#                         service of an attack, so a sac WITH king pressure
#                         outranks a sac without one (a random piece giveaway
#                         in a quiet corner must not score like Bxh7+).
# volatility       0.10 : "positive" -- sacrifices are sharp by nature
#                         (capture + pressure swing); a small tie-breaker.
# defense_gain     0.00 : same reasoning as Attacker -- do not punish a move
#                         for ALSO being defensively sound (a blocked check
#                         that doubles as a sac offering is still a sac).
# initiative_proxy 0.00 : no signal exists.
# Weights sum to exactly 1.0 -> output guaranteed in [-1, 1].
# NOTE: Attacker and Sacrificer INTENTIONALLY correlate on genuinely
# sacrificial-attacking moves (both score Bxh7+ strongly positive and both
# rank it top) -- that is per the design, not a bug to eliminate. They
# diverge on non-sacrificial direct attacks: a mating queen capture scores
# high for Attacker but low for Sacrificer (no material is actually gambled).
_SACRIFICER_W_SACRIFICE = 0.70
_SACRIFICER_W_ATTACK = 0.20
_SACRIFICER_W_VOLATILITY = 0.10
_SACRIFICER_W_DEFENSE = 0.0
_SACRIFICER_W_INITIATIVE = 0.0


def sacrificer_score(scores: StyleScores, board: chess.Board) -> float:
    """Sacrificer persona raw score in [-1, 1] (bounded_persona_bias input).

    `board` is the position BEFORE the move; used only for game_phase().
    """
    phase = game_phase(board)
    n = normalize_style_scores(_phase_damped_scores(scores, phase))
    total = (
        _SACRIFICER_W_SACRIFICE * n["sacrifice_signal"]
        + _SACRIFICER_W_ATTACK * n["attack_gain"]
        + _SACRIFICER_W_VOLATILITY * n["volatility"]
        + _SACRIFICER_W_DEFENSE * n["defense_gain"]
        + _SACRIFICER_W_INITIATIVE * n["initiative_proxy"]
    )
    return max(-1.0, min(1.0, total))


# --- final per-candidate ranking number (unit reconciliation) -----------------
# Full derivation and trust-contract argument in the module docstring's
# UNIT RECONCILIATION section.
PERSONA_BIAS_CP = 100.0


def persona_adjusted_score(
    engine_norm_cp: float,
    raw_persona_score: float,
    phase: float,
    max_cp_drop: float = 75.0,
) -> float:
    """Final per-candidate ranking number, in CENTIPAWN scale.

    final = engine_norm_cp + PERSONA_BIAS_CP * bounded_persona_bias(...),
    floored at -max_cp_drop -- but ONLY for candidates whose engine_norm_cp
    sits inside the trust window (norm > -max_cp_drop). Candidates already at
    norm <= -max_cp_drop have trust exactly 0 (bias exactly 0) and keep
    final == norm: the floor exists to cap persona DEMOTION at the trust
    boundary and must never LIFT a hard-rejected candidate.

    Why the floor is needed at all: without it, a maximally style-hated
    near-best move (bias -1.0 at trust 1.0) is demoted by
    PERSONA_BIAS_CP centipawns, which could let a deep trust-zeroed
    candidate win by default -- violating "nothing >=75cp below the engine's
    best can ever win regardless of persona score".
    """
    bias = bounded_persona_bias(raw_persona_score, engine_norm_cp, phase, max_cp_drop)
    final = engine_norm_cp + PERSONA_BIAS_CP * bias
    if final < -max_cp_drop and engine_norm_cp > -max_cp_drop:
        final = -max_cp_drop
    return final
