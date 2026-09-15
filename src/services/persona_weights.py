"""
Persona weight vectors (Attacker + Sacrificer + Defender + Positional +
Gambiter) for the Engine Sparring reranker.

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
  * attacker_score() / sacrificer_score() / defender_score() /
    positional_score() / gambiter_score(): the persona weight presets. Each
    applies phase gating internally (per the ARCHITECTURE NOTE in
    persona_bounds.py), then normalizes, then takes the weighted sum of the
    normalized fields. Each returns a single raw_persona_score float in
    [-1, 1], ready for bounded_persona_bias(raw, engine_norm_cp, phase).

All five personas (Attacker, Sacrificer, Defender, Positional, Gambiter)
now exist here. Positional is structurally DIFFERENT from the other three
first-pass personas: it has no positive anchor at all (pure penalty persona
-- see its weight block for the argued design decision), and Gambiter is
structurally DERIVED: a literal 0.7/0.3 blend of the Attacker and Sacrificer
vectors (see its weight block). This module must not modify
persona_features.py or persona_bounds.py.

NOTE ON SIGNATURES: the task sketch showed one-argument weight functions,
but PART 3 requires game_phase(board) to be applied INSIDE the persona
weight functions (persona_bounds.py's architecture note), so all of them
take the board as a second parameter and call game_phase(board) themselves.

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
game_phase(board) (persona_bounds.py) runs INSIDE all four persona
functions --
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
  * defense_sub.* -- not damped on the ATTACK-side path: Attacker and
    Sacrificer weight defense_gain at 0.0, so defense-side damping would be
    a no-op for them, and _phase_damped_scores therefore leaves defense
    untouched. The Defender (below) is the first persona that weights
    defense_sub nonzero, so IT owns the defense-side damping decisions --
    see _defender_phase_damped_scores and the defense-side entries here.

The damping is applied to the RAW subcomponents BEFORE aggregation and
normalization (that is what the architecture note requires), and the
aggregates are rebuilt from the damped subcomponents so the documented
invariants attack_gain == sum(attack_sub) and defense_gain ==
sum(defense_sub) keep holding.

DEFENSE-SIDE PHASE GATING -- the Defender's per-subcomponent decisions
=======================================================================
The Defender weights defense_sub components nonzero, so phase damping had to
be decided for EACH defense subcomponent on its own logic. The resulting
taxonomy is NOT a mirror of the attack side (the attack side damps its two
king-zone PRESSURE readings; the defense side splits differently):

  DAMPED (STATIC shelter/coverage features -- what the position around my
  king LOOKS like, regardless of whether anything threatens it):
  * defense_sub.king_zone_defense -- delta of the friendly coverage of the
    mover's own king zone. A STATIC feature: friendly pieces cover the
    king's ring whether or not anything is attacking it, so unlike the
    pressure deltas it does NOT self-limit when the enemy force leaves the
    board. Residual coverage is anti-endgame advice -- it rewards parking
    pieces next to their own king instead of using them, and in king-and-
    pawn endings defense is handled by the king itself. Damped for the same
    reason attack-side king_zone_pressure is damped: what remains of a
    zone-coverage reading in thin material converts poorly into real
    defense.
  * defense_sub.pawn_shield -- delta of the pawn shelter in front of the
    king. Mostly self-limiting (no pawns near the king -> no shield), but
    its RESIDUAL meaning is "protection from heavy-piece attack", and the
    attacking force that makes shelter worth having is exactly what leaves
    the board as phase rises. A surviving shield pawn still helps a little
    against a remaining rook or queen, so it decays linearly rather than
    being gated off.

  NOT DAMPED (concrete delta/event features, plus the king-skill term):
  * defense_sub.enemy_pressure_reduction -- a DELTA: how much enemy
    pressure on the mover's own king zone the move removes. Self-limiting
    by construction (when the enemy has no pressure, before - after is
    exactly 0), so it needs no artificial damping; and when pressure DOES
    exist in an endgame (a rook or queen hovering over the king), removing
    it is the core of endgame defense -- dodging checks and escaping nets
    is precisely what this measures. Same class as attack-side checks: a
    concrete event, not a pressure reading.
  * defense_sub.line_blocking -- an enemy slider's ray to the mover's king
    SQUARE that exists before the move and is blocked after: a concrete
    tactical event (it requires an actual ray to exist), the exact mirror
    of attack-side checks. It also self-limits (blocking needs a piece),
    and thin-material mating nets are delivered through lines, so a block
    is as valuable at phase 1 as at phase 0.
  * defense_sub.king_mobility -- the king's available-squares delta. This
    one points the OPPOSITE way from the attack-side damped terms: king
    activity is arguably the single most important endgame skill (the
    active king decides king-and-pawn endings, offensively AND
    defensively), so its value RISES with phase. The linear (1 - phase)
    damping factor can only shrink a signal, never amplify it, so the
    correct in-mechanism implementation of "more valuable in the endgame"
    is to never damp it at all. A true phase-dependent AMPLIFICATION of
    king_mobility would need a mechanism the current damping factor cannot
    express; noted as a possible future refinement (same spirit as
    game_phase's smoothstep note).

  The Defender inherits the attack-side damping decisions verbatim by
  building on _phase_damped_scores() (it weights attack_gain, so the same
  king-pressure components must damp for it too).

GAMBITER PHASE GATING -- pure reuse, like Positional
====================================================
Gambiter weights defense_gain at 0.0 (see its weight block), so the
Defender's defense-side damping is irrelevant to it; and its nonzero terms
(attack_gain, sacrifice_signal, volatility) are exactly the components
_phase_damped_scores already governs: the attack-side kzp/kaa damping
applies to its attack_gain term (Gambiter weights attack_gain nonzero, so
it needs the same king-pressure damping Attacker/Sacrificer get), while
sacrifice_signal and volatility are conventionally never damped. No second
damping function is created for it -- reuse over reinvention, same as
Positional.

POSITIONAL PHASE GATING -- pure reuse, no third damping function
=================================================================
Positional weights defense_gain at 0.0 (see its weight block), so the
Defender's defense-side damping is irrelevant to it; and its nonzero terms
(volatility, attack_gain, sacrifice_signal) are exactly the components
_phase_damped_scores already governs: the attack-side kzp/kaa damping
applies to its attack_gain term, while sacrifice_signal and volatility are
conventionally never damped ("a piece sac in an endgame is still a
sacrifice"; "captures/checks/swings stay meaningful in any phase"). No
third damping function is created for it -- reuse over reinvention.

The one genuinely debatable case is ENDGAME VOLATILITY: in an endgame,
captures and checks are often the CORRECT moves (a winning capture in a
K+P ending is not "sharpness"), so penalizing volatility at full weight at
phase 1 could make a positional bot demote a routine winning trade that
sits near-equal with a quiet alternative. The existing convention is kept
for consistency (and because engine_trust caps the damage), and this is
recorded as a first-pass limitation to revisit: if endgame sparring shows
the persona refusing routine trades, a phase-dependent volatility damping
would be the fix -- and that WOULD be a case where Positional needs
different subcomponent damping decisions than the existing functions
provide, justifying a new damping function at that point.

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


def _defender_phase_damped_scores(scores: StyleScores, phase: float) -> StyleScores:
    """Defender-side phase damping: the attack-side decisions from
    _phase_damped_scores are inherited VERBATIM (the Defender weights
    attack_gain, so the same king-pressure-derived components must damp for
    it too), and the DEFENSE-side decisions documented in the module
    docstring's "DEFENSE-SIDE PHASE GATING" section are applied on top:

      damped (static shelter/coverage): king_zone_defense, pawn_shield
      not damped (delta/event + king skill): enemy_pressure_reduction,
          line_blocking, king_mobility

    defense_gain is rebuilt from the damped defense subcomponents to
    preserve the invariant defense_gain == sum(defense_sub). The input
    `scores` object is never mutated.
    """
    damped = _phase_damped_scores(scores, phase)
    damp = 1.0 - phase
    damped_defense_sub = replace(
        damped.defense_sub,
        king_zone_defense=damped.defense_sub.king_zone_defense * damp,
        pawn_shield=damped.defense_sub.pawn_shield * damp,
    )
    damped_defense_gain = (
        damped_defense_sub.enemy_pressure_reduction
        + damped_defense_sub.king_zone_defense
        + damped_defense_sub.line_blocking
        + damped_defense_sub.pawn_shield
        + damped_defense_sub.king_mobility
    )
    return replace(
        damped,
        defense_sub=damped_defense_sub,
        defense_gain=damped_defense_gain,
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


# --- Defender weights (first pass; tuned in a later step) ---------------------
# defense_gain     1.20 : dominant by definition -- the persona exists to pick
#                        maximum king safety, so defense_gain must dominate
#                        every other term by at least an order of magnitude in
#                        effect. The weight exceeds 1.0 BY DESIGN: the attack/
#                        sacrifice/volatility terms below are PENALTIES
#                        (negative weights), and the weights-must-sum-to-1.0
#                        constraint forces the positive term to carry their
#                        combined magnitude (1.20 - 0.05 - 0.05 - 0.10 = 1.00).
#                        Concretely, a defense_gain of +4 raw (the Be2 block
#                        fixture scores +4.06) yields ~0.33 of persona score,
#                        while the strongest realistic combined penalty
#                        (sacrifice + max volatility + big attack swing) costs
#                        at most ~0.2 -- real defense always outvotes the
#                        style aversions.
# attack_gain     -0.05 : mildly negative -- a defensive-minded player
#                        actively prefers calmer positions and does not value
#                        gratuitous threats, even good ones. This is a
#                        DELIBERATE asymmetry with the other two personas'
#                        0.0 on their non-dominant gain: Attacker/Sacrificer
#                        keep defense at 0.0 because a move can "block a
#                        check AND create a threat" and must not be punished
#                        for the threat side. The mirror argument ("a move
#                        can tuck the king away AND threaten") is weaker for
#                        the Defender, whose IDENTITY is avoiding sharpness;
#                        the magnitude stays at tie-breaker scale so it can
#                        never demote genuinely defensive play: sign-flipping
#                        a candidate via the attack term alone would require
#                        tanh(atk/14) > 24x tanh(def/14) -- i.e. defense_gain
#                        under ~0.6 raw (no real defensive content) AND
#                        attack_gain beyond ~+17 raw (near the documented +20
#                        ceiling, ignoring the other negative terms). That is
#                        the "attacking move dressed as defense" case, where
#                        the mild dislike IS the persona, not a bug.
# sacrifice_signal -0.05 : mildly negative -- unlike Attacker/Sacrificer's
#                        small POSITIVE weights, the Defender should not
#                        reward gambling material: a sacrifice hands the
#                        opponent compensation and sharpens the game, the
#                        opposite of the persona's goal. Kept at tie-breaker
#                        scale, and deliberately smaller than one might
#                        intuit because sacrifice and volatility OVERLAP (a
#                        real sacrifice is almost always also volatile: a
#                        capture plus a pressure swing), so a large sac
#                        penalty would double-count the same sharpness that
#                        the volatility term already penalizes.
# volatility      -0.10 : negative, the sign-flipped mirror of Attacker's
#                        +0.20 at half strength -- captures, checks and big
#                        pressure swings are exactly the sharpness a
#                        defensive player avoids. Volatility gets the
#                        LARGEST of the three penalties because it is the
#                        broadest sharpness signal (it fires on sharp QUIET
#                        moves too, e.g. double attacks, where the sacrifice
#                        and attack terms are silent).
# initiative_proxy 0.00 : no signal exists (always 0.0 by design in
#                        persona_features; would require lookahead).
# Weights sum to exactly 1.0 (1.20 - 0.05 - 0.05 - 0.10 = 1.00), BUT with
# negative weights the weighted sum is an AFFINE combination, not a convex
# one, so the [-1, 1] output range is NOT automatic: the raw total can reach
# about +/-1.6 in adversarial combinations (max defense vs max
# attack/sacrifice/volatility). The final clamp to [-1, 1] is therefore
# LOAD-BEARING for this persona, where it is vestigial safety for
# Attacker/Sacrificer.
_DEFENDER_W_DEFENSE = 1.20
_DEFENDER_W_ATTACK = -0.05
_DEFENDER_W_SACRIFICE = -0.05
_DEFENDER_W_VOLATILITY = -0.10
_DEFENDER_W_INITIATIVE = 0.0


def defender_score(scores: StyleScores, board: chess.Board) -> float:
    """Defender persona raw score in [-1, 1] (bounded_persona_bias input).

    `board` is the position BEFORE the move (the same position the scores
    were computed on); it is used only for game_phase(). Phase gating uses
    the Defender's own defense-side damping decisions
    (_defender_phase_damped_scores) on top of the inherited attack-side
    decisions. NOTE: because this persona's style weights are negative, the
    final clamp is load-bearing (not vestigial) -- see the weight-block
    comment.
    """
    phase = game_phase(board)
    n = normalize_style_scores(_defender_phase_damped_scores(scores, phase))
    total = (
        _DEFENDER_W_DEFENSE * n["defense_gain"]
        + _DEFENDER_W_ATTACK * n["attack_gain"]
        + _DEFENDER_W_SACRIFICE * n["sacrifice_signal"]
        + _DEFENDER_W_VOLATILITY * n["volatility"]
        + _DEFENDER_W_INITIATIVE * n["initiative_proxy"]
    )
    return max(-1.0, min(1.0, total))


# --- Positional weights (first-pass magnitudes calibrated at s = 0.80;
#     see CALIBRATION below) ---------------------------------------------------
#
# DESIGN DECISION -- PURE PENALTY PERSONA, NO POSITIVE ANCHOR (question (a),
# chosen over (b) after explicit consideration)
#
# The other three personas each have a StyleScores field that is a genuine
# POSITIVE signal for their identity (attack_gain, sacrifice_signal,
# defense_gain). Positional has no equivalent: "prefers quiet, sound moves"
# is the ABSENCE of what the others want, not a feature the extractor
# measures. The alternative (b) -- manufacturing a positive "quietness"
# anchor -- was considered and REJECTED on three grounds:
#
#   1. 1 - volatility (the obvious quietness candidate) re-expresses
#      negated volatility with extra steps: score = w*(1 - vol) + ... is
#      algebraically a constant w plus the existing penalty terms. The
#      constant is NOT harmless: it enters the final ranking as
#      B * w * trust(norm), which varies PER CANDIDATE with engine trust --
#      i.e. the persona would hand every quiet move a trust-gated flat
#      bonus, and at w = 0.3 that is ~+23cp of pull for a quiet move only
#      5cp below the engine's best (100 * 0.3 * trust(-5) = 23.1) -- far
#      stronger than any real anchor in this codebase (Defender's real
#      fixtures max out near +0.33 of score). The persona would flip from
#      tie-breaker to routine engine-overrider, contradicting its purpose.
#   2. Quietness is also INVARIENT to whether the quiet move improves the
#      position or does nothing: engine cp already ranks those, so a
#      quietness reward adds no information the eval does not carry --
#      unlike defense_gain (+4 on the Be2 fixture), which is a concrete
#      event family the eval does not style-weight.
#   3. The static defense subcomponents (pawn_shield, king_zone_defense)
#      ARE positional-ish ("structure around my king"), but reaching them
#      would mean splitting defense_gain into static vs reactive parts --
#      a new damping/selection mechanism -- and the result would be a
#      weaker Defender clone. Positional's distinction from Defender is
#      exactly that Defender PREFERS safety events while Positional only
#      AVOIDS sharpness events; blurring that costs both identities.
#
# ROLE CONSEQUENCE, STATED AND DEFENDED: with all-negative weights every
# candidate scores in ([-0.8, 0] -- so positional_score's role is RELATIVE
# ("least sharp among the engine's already-good options") and it can never
# become a strong positive biasing force the way defense_gain = +4 does for
# Defender. This is the CORRECT behavior, not a flaw: a Positional bot's
# whole point is "nudge toward the quietest of the engine's already-good
# options, never override anything". Its only possible ACTION at rerank
# time is demotion of sharpness (trust already guarantees it can never
# rescue a bad move; pure penalties additionally guarantee it can never
# push a sharp move up). And it degrades gracefully: when ALL candidates
# are fully quiet, every score ties at exactly 0 and the ranking falls
# back to cp order -- the engine's preference among genuinely-equal quiet
# moves is as good as any invented positional tiebreak.
#
# WEIGHTS (calibrated at s = 0.80 of the first-pass magnitudes,
# ratio-preserving -- they no longer sum to a round number BY DESIGN; a
# perfectly quiet move still scores exactly 0.0, the neutral anchor this
# persona lacks by design):
#
# CALIBRATION -- WHY THE MAGNITUDES ARE 0.8x THE FIRST PASS (measured, not
# re-argued). The first pass (vol -0.60 / atk -0.25 / sac -0.15) was set by
# magnitude reasoning alone. A follow-up measurement harness (fine numeric
# scan over a 3-run live-engine sweep) found that at s = 1.0 the persona's
# demotion of the sharp top candidate EXCEEDED what the two
# no-quiet-alternative fixtures can absorb -- a fixture built to show that
# no quiet alternative legitimately exists must not have one manufactured
# by a persona bias, the same "cannot conjure a quiet alternative" guarantee
# the other three personas already hold:
#   * obvious sacrifice (Bxh7+ vs Nxh7, 31cp draw): demotion 40.77cp vs an
#     allowed maximum of 36.15cp -> over by 4.61cp;
#   * sharp tactical no quiet alternative (Nxf7 vs d4, 31cp draw): demotion
#     35.44cp vs an allowed maximum of 31.00cp -> over by 4.44cp.
# The exact inertness boundary for those canonical draws is
#     s* = gap / (100 * (|P_sharp| - |P_quiet| * trust(-gap)))
# = 31/35.61 = 0.8704 (obvious sacrifice binds; sharp tactical 0.8747), so
# s = 1.0 sat ~15% PAST it. Worse, the reorder was NOT a stable property of
# the weights: candidate gaps jittered 11-69cp run-to-run from normal
# MultiPV noise (P values are bit-identical across runs; only the gaps
# move), so at s = 1.0 whether either fixture reorders on a given run is
# close to a coin flip around the ~31-36cp gap region.
# s = 0.80 keeps every relative structure of the design below EXACTLY (all
# three weights scale by the same factor, so the self-limiting ratio and
# the ordering arguments are untouched) and buys real slack against the
# canonical draws: demotions 32.62cp / 28.35cp against allowed maxima
# 35.12cp / 31.00cp -> ~2.5cp of headroom on both, instead of negative
# headroom. It also stays well below the measured up-side boundary: the
# first NEW reorder in a currently-inert fixture appeared at s ~1.05-1.25
# (tiny-gap draws), defensive consolidating held to s ~3.1-3.7, and
# castling was inert all the way to the s = 4 probe ceiling.
# HONEST LIMIT, recorded rather than glossed: no fixed s > 0 is inert for
# EVERY gap draw -- as the drawn gap shrinks, trust(-g) -> 1 and the
# boundary s*(g) -> 0, so a sub-28cp draw (observed once: 22cp) still
# reorders at s = 0.80. s = 0.80 therefore SHRINKS the coin-flip window (a
# reorder now needs a drawn gap under ~28cp, versus under ~36cp at
# s = 1.0) rather than closing it; closing it fully would take s -> 0
# (killing the persona) or a mechanism change, which is a separate
# decision.
#
# volatility      -0.48 : the dominant NEGATIVE term -- the negated mirror of
#                         Attacker's +0.70 anchor more than of its +0.20
#                         side-weight: for Positional, avoiding sharpness IS
#                         the identity, so volatility carries the persona the
#                         way attack_gain carries Attacker's. Volatility is
#                         also the BROADEST sharpness signal (it fires on
#                         sharp quiet moves -- double attacks, big swings --
#                         where sacrifice is silent and attack_gain small),
#                         which is exactly the "complications a positional
#                         player avoids".
# attack_gain     -0.20 : negative -- Positional actively avoids creating
#                         threats, even good ones; this is the SAME question
#                         Defender faced but answered DIFFERENTLY on its own
#                         terms: for Defender, attack-hate was a vestigial
#                         tie-breaker bolted onto a positive anchor (-0.05);
#                         for Positional, anti-threat is close to the CORE
#                         identity, so the term is 4x stronger. A side effect
#                         of the negative weight is a small CREDIT for
#                         de-escalating moves (attack_gain < 0) -- intended,
#                         and provably self-limiting: dropping attack
#                         pressure requires a king-zone pressure swing of the
#                         same magnitude, which raises volatility via its
#                         pressure-swing term (|delta|/6, averaged) at an
#                         effective rate (0.48/18 = 0.027 per raw point)
#                         that always EXCEEDS the credit rate (0.20/14 =
#                         0.014; the same 1.87x ratio as the first pass --
#                         uniform scaling preserves it) -- so a pure
#                         de-escalator is always net-negative and the
#                         theoretical +0.20 credit ceiling is unreachable in
#                         real positions (verified: atk -8 implies vol >= 1/3
#                         -> net -0.057, not +0.103).
# sacrifice_signal -0.12 : negative -- a sacrifice is definitionally not
#                         quiet. Smaller than the volatility term and than
#                         one might intuit because sacrifice and volatility
#                         OVERLAP (a real sac is almost always a capture plus
#                         a pressure swing); this weight prices only the
#                         INCREMENTAL gamble aspect (handing the opponent
#                         compensation) beyond what volatility already
#                         charges for the same move's sharpness.
# defense_gain     0.00 : decided EXPLICITLY, not defaulted. Zero, NOT small
#                         positive and NOT mildly negative:
#                           * positive would make Positional a weaker
#                             Defender (their distinction is precisely
#                             Defender-prefers / Positional-only-avoids);
#                           * the dominant real content of defense_gain is
#                             REACTIVE (the Be2 fixture: epr +2.00, blk +1.0
#                             of its +4.06) -- emergency play is the opposite
#                             of the calm, proactive improvement this
#                             persona means; when defense is FORCED the
#                             engine already ranks it first and trust
#                             handles it;
#                           * negative ("very active defense is also not
#                             quiet") would double-punish sharpness through
#                             a second door (sharpness is already fully
#                             priced by vol/atk/sac) and would penalize
#                             castling (defense +0.01) and king-tucking for
#                             no stylistic reason -- those moves are already
#                             scored 0.0 (perfectly quiet) here, which is
#                             the correct positional verdict.
# initiative_proxy 0.00 : no signal exists (always 0.0 by design in
#                         persona_features; would require lookahead).
# RANGE NOTE: all weights <= 0 and the inputs' signs bound the total to
# (-0.80, +0.20] in theory -- but the positive side requires attack_gain
# deep negative WITH volatility near zero, which cannot co-occur (see the
# attack_gain entry), and the negative side needs all three inputs at their
# extremes at once, so the practical range is [-0.78, 0] (documented attack
# ceiling +20 -> tanh(20/14) = 0.891: -0.48 - 0.20*0.891 - 0.12 = -0.778).
# The final clamp to [-1, 1] is therefore FULLY VESTIGIAL safety for this
# persona: the -0.80 theoretical floor is approached only at absurd tanh
# saturation (|attack_gain| >= ~266 with volatility 1.0 AND sacrifice 1.0)
# and the +0.20 theoretical ceiling is unreachable, so the clamp cannot
# bind on EITHER side at any real or synthetic input -- UNLIKE the
# Defender, whose clamp is load-bearing.
_POSITIONAL_W_VOLATILITY = -0.48
_POSITIONAL_W_ATTACK = -0.20
_POSITIONAL_W_SACRIFICE = -0.12
_POSITIONAL_W_DEFENSE = 0.0
_POSITIONAL_W_INITIATIVE = 0.0


def positional_score(scores: StyleScores, board: chess.Board) -> float:
    """Positional persona raw score in [-1, 1] (bounded_persona_bias input).

    PURE PENALTY persona (no positive anchor -- see the weight-block comment
    for the argued design decision): scores live in ~[-0.8, 0], 0.0 == a
    perfectly quiet move, and the persona's only rerank action is demoting
    sharpness among the engine's already-good options.

    `board` is the position BEFORE the move (the same position the scores
    were computed on); it is used only for game_phase(). Phase gating reuses
    _phase_damped_scores (attack side) only -- defense is weighted 0.0, so
    the Defender's defense-side damping is irrelevant here.
    """
    phase = game_phase(board)
    n = normalize_style_scores(_phase_damped_scores(scores, phase))
    total = (
        _POSITIONAL_W_VOLATILITY * n["volatility"]
        + _POSITIONAL_W_ATTACK * n["attack_gain"]
        + _POSITIONAL_W_SACRIFICE * n["sacrifice_signal"]
        + _POSITIONAL_W_DEFENSE * n["defense_gain"]
        + _POSITIONAL_W_INITIATIVE * n["initiative_proxy"]
    )
    return max(-1.0, min(1.0, total))


# --- Gambiter weights (derived fifth persona: a literal 0.7/0.3 blend of
#     the Attacker and Sacrificer first-pass vectors; the design decision was
#     "attack-led gambit seeker", so the blend is anchored on Attacker) -------
#
# DESIGN DECISION -- DERIVED, NOT HAND-TUNED: Gambiter's rerank personality
# is "mostly the Attacker's eye, with a Sacrificer's taste for the
# material gamble", and the cheapest correct way to express exactly that is
# a convex blend of the two proven vectors. Each weight below is literally
# 0.7 * Attacker's + 0.3 * Sacrificer's (recomputed from those personas'
# ACTUAL current constants; the persona_weights_test asserts the blend
# identity against the live constants, so if either parent's vector is ever
# retuned, this test fails and forces a conscious re-blend rather than
# silently drifting).
#
# attack_gain      0.55 : 0.7*0.70 (Attacker) + 0.3*0.20 (Sacrificer) --
#                         attack-led, as designed: the Gambiter still wants
#                         king pressure on every move, with a smaller
#                         sacrifice tilt than Attacker's own 0.70 anchor.
# volatility       0.17 : 0.7*0.20 + 0.3*0.10 -- sharp play is a mild
#                         plus, exactly between its two parents.
# sacrifice_signal 0.28 : 0.7*0.10 + 0.3*0.70 -- the blended-in Sacrificer
#                         share: nearly triple the Attacker's own 0.10
#                         tie-breaker, enough to push a genuinely
#                         sacrificial candidate past a pure-pressure one,
#                         but well short of Sacrificer's 0.70 dominance.
# defense_gain     0.00 : 0.7*0.0 + 0.3*0.0 -- both parents keep it at 0.0
#                         ("a move can be defensively sound AND attacking");
#                         the blend preserves that reasoning unchanged.
# initiative_proxy 0.00 : no signal exists (always 0.0 by design in
#                         persona_features; would require lookahead).
# Weights sum to exactly 1.0 (0.55 + 0.17 + 0.28 = 1.00) and are all
# non-negative, so the weighted sum of inputs that each live in [-1, 1] is
# guaranteed in [-1, 1]: the theoretical bound of |total| is exactly
# 1.0 = 0.55 + 0.17 + 0.28, reached only when EVERY input saturates its
# endpoint simultaneously (attack_gain's tanh never reaches 1.0 below raw
# ~+266 -- absurd), so the final clamp is VESTIGIAL safety for this persona,
# exactly like Attacker/Sacrificer (and unlike the Defender, whose negative
# weights make its clamp load-bearing). Verified in tests, not assumed.
_GAMBITER_W_ATTACK = 0.55
_GAMBITER_W_VOLATILITY = 0.17
_GAMBITER_W_SACRIFICE = 0.28
_GAMBITER_W_DEFENSE = 0.0
_GAMBITER_W_INITIATIVE = 0.0


def gambiter_score(scores: StyleScores, board: chess.Board) -> float:
    """Gambiter persona raw score in [-1, 1] (bounded_persona_bias input).

    LITERAL BLEND persona: 0.7 * Attacker + 0.3 * Sacrificer (per-field
    derivation in the weight-block comment). Because every persona score in
    this module is a weighted sum over the SAME normalized inputs (same
    phase gating, same normalize_style_scores), gambiter_score equals
    0.7*attacker_score + 0.3*sacrificer_score EXACTLY on any input where
    none of the three clamps bind -- asserted in the tests rather than
    assumed.

    `board` is the position BEFORE the move (the same position the scores
    were computed on); it is used only for game_phase(). Phase gating reuses
    _phase_damped_scores (attack side) -- attack_gain is weighted nonzero,
    so the same king-pressure damping Attacker/Sacrificer get applies here;
    defense_gain is weighted 0.0, so no defense-side damping is needed.
    """
    phase = game_phase(board)
    n = normalize_style_scores(_phase_damped_scores(scores, phase))
    total = (
        _GAMBITER_W_ATTACK * n["attack_gain"]
        + _GAMBITER_W_VOLATILITY * n["volatility"]
        + _GAMBITER_W_SACRIFICE * n["sacrifice_signal"]
        + _GAMBITER_W_DEFENSE * n["defense_gain"]
        + _GAMBITER_W_INITIATIVE * n["initiative_proxy"]
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
