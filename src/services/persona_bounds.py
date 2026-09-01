"""
Persona bounding / decay / phase-gate layer for the Engine Sparring reranker.

This sits BETWEEN the raw StyleScores produced by the shared feature
extractor and the eventual persona weight vectors (Attacker/Defender/
Sacrificer/Positional). It does NOT implement those weight presets -- it only
provides the infrastructure that will constrain them once they exist:

  * engine_trust():       how much the persona is allowed to sway the engine's
                          eval, decaying to zero as a candidate falls further
                          below the engine's best move.
  * game_phase():         a continuous 0..1 signal of how far into the endgame
                          the position is (used to damp king-pressure terms).
  * bounded_persona_bias(): the final gate -- clamp the aggregate persona
                          score, scale by engine_trust, return the bias to add
                          to the engine's own normalized score.

ARCHITECTURE NOTE -- where `phase` is applied
=============================================
The caller's plan is to apply `game_phase()` INSIDE the (not-yet-built)
persona weight functions -- specifically to damp king-pressure-DERIVED
subcomponents before they are aggregated into the single raw_persona_score
float. This layer agrees with that plan and does NOT try to apply `phase`
in bounded_persona_bias():

  * bounded_persona_bias() only sees the FINAL aggregate score, not the
    subcomponents, so it cannot selectively damp only the king-pressure
    contribution. Damping the whole aggregate by `phase` would also damp
    attacker/sacrificer/positional signals that should survive into the
    endgame, which is wrong.
  * `phase` is therefore accepted in the signature for API stability and
    deliberately left unused here. If a future task ever wants a phase-
    dependent adjustment to the FINAL bias (not subcomponent-specific), that
    is the only case where it would belong in this function.

PERSONA FEATURES DEPENDENCY
===========================
The shared feature extractor (compute_style_scores() -> StyleScores) has NOT
landed in this tree yet -- a search of src/, scripts/, and git history found
no such file or type. None of the functions here consume StyleScores: they
operate on (a) a pre-computed aggregate persona score passed in as a plain
float and (b) a chess.Board. So this layer is StyleScores-agnostic by design;
the one integration point (raw_persona_score <- weight-vector dot StyleScores)
is the future weight-vector task's responsibility.
"""
import math

import chess

# --- engine_trust constants ------------------------------------------------
# The trust value we want the smooth exponential to REACH at the hard reject
# boundary (-max_cp_drop). The tail below the boundary is then clamped to
# exactly 0.0. See the derivation in engine_trust().
_TRUST_BOUNDARY_EPSILON = 0.02

# --- game_phase constants --------------------------------------------------
# Material values for the non-pawn, non-king pieces used to measure how far
# into the endgame a position is. Kings and pawns are excluded (pawns don't
# attack the enemy king's shelter the way heavy/minor pieces do; kings are
# always present). Standard piece values.
_PIECE_VALUE = {
    chess.QUEEN: 9,
    chess.ROOK: 5,
    chess.BISHOP: 3,
    chess.KNIGHT: 3,
}
# Total non-pawn material at the start of a game:
#   2 sides * (1 queen*9 + 2 rooks*5 + 2 bishops*3 + 2 knights*3) = 2 * 31 = 62.
_STARTING_NON_PAWN_MATERIAL = 62.0


def engine_trust(engine_norm_cp: float, max_cp_drop: float = 75.0) -> float:
    """How much persona freedom a candidate retains, as a function of how far
    it falls below the engine's best move.

    engine_norm_cp is (candidate_score - best_candidate_score), always <= 0
    (0 == this candidate IS the engine's best move). The function returns:

      1.0                    at engine_norm_cp == 0   (full persona freedom)
      -> smooth exponential decay as cp drops
      ~0.02  (then hard 0.0) at engine_norm_cp == -max_cp_drop
      0.0                    at engine_norm_cp <= -max_cp_drop (hard reject)

    Derivation of the decay constant k
    ----------------------------------
    We want trust(x) = exp(k * x) (with x <= 0) such that at the boundary the
    exponential has decayed to a small epsilon, then clamp the tail:

        exp(k * (-max_cp_drop)) = epsilon
        -k * max_cp_drop = ln(epsilon)
        k = -ln(epsilon) / max_cp_drop = ln(1/epsilon) / max_cp_drop

    With epsilon = 0.02 and max_cp_drop = 75.0:

        k = -ln(0.02) / 75.0
          = 3.912023005... / 75.0
          = 0.0521603067...

    So the curve is trust(x) = exp(0.0521603067 * x), clamped to [0, 1]:

        trust(0)    = 1.0
        trust(-20)  = exp(-1.0432)  = 0.3523
        trust(-40)  = exp(-2.0864)  = 0.1241
        trust(-60)  = exp(-3.1296)  = 0.0437
        trust(-75)  = 0.0           (hard clamp at the boundary)

    This is a genuine exponential (NOT a linear ramp, NOT a step function).
    The only discontinuity is the intentional hard-reject step from ~0.02 to
    exactly 0.0 at the -max_cp_drop boundary.
    """
    if engine_norm_cp >= 0.0:
        return 1.0
    if engine_norm_cp <= -max_cp_drop:
        return 0.0
    k = -math.log(_TRUST_BOUNDARY_EPSILON) / max_cp_drop
    return math.exp(k * engine_norm_cp)


def _non_pawn_material(board: chess.Board) -> float:
    """Total material of queens/rooks/bishops/knights (kings and pawns
    excluded), summed across both sides."""
    total = 0.0
    for piece in board.piece_map().values():
        if piece.piece_type in (chess.PAWN, chess.KING):
            continue
        total += _PIECE_VALUE.get(piece.piece_type, 0)
    return total


def game_phase(board: chess.Board) -> float:
    """A continuous 0.0..1.0 estimate of how deep into the endgame a position
    is.

      0.0 = opening/middlegame (full weight on king-pressure terms)
      1.0 = deep endgame       (king-pressure terms should be heavily damped)

    Formula (v1 -- simple and smooth, no hard cliff):

        phase = clamp(1.0 - non_pawn_material / 62.0, 0.0, 1.0)

    where non_pawn_material excludes kings AND pawns (see
    _non_pawn_material). The denominator 62.0 is the non-pawn material of the
    starting position, so `phase` is simply the fraction of the starting
    non-pawn material that has been traded off, clamped to [0, 1].

    Worked examples (verified in persona_bounds_test.py):
      * Starting position (npm = 62)      -> phase = 1 - 62/62 = 0.0
      * Queenless middlegame (npm = 44)   -> phase = 1 - 44/62 = 0.2903
      * King + pawn endgame (npm = 0)     -> phase = 1 - 0/62  = 1.0

    This is deliberately unsophisticated: a single linear ramp over the full
    material range is a smooth transition zone (continuous, no cliff), and it
    reacts primarily to heavy-piece trades (queens/rooks), which is the
    dominant signal for "king-pressure is less relevant now". A smoothstep
    variant with plateau thresholds is a trivial future refinement if the
    linear ramp's early rise is ever deemed too aggressive.
    """
    npm = _non_pawn_material(board)
    raw = 1.0 - (npm / _STARTING_NON_PAWN_MATERIAL)
    return max(0.0, min(1.0, raw))


def bounded_persona_bias(
    raw_persona_score: float,
    engine_norm_cp: float,
    phase: float,
    max_cp_drop: float = 75.0,
) -> float:
    """The final bounding gate: clamp the aggregate persona score, scale it by
    engine trust, and return the bounded bias to ADD to the engine's own
    normalized score during reranking.

    Steps:
      1. Clamp raw_persona_score to [-1, 1] defensively. The future
         weight-vector dot product should already produce bounded scores, but
         this function does not trust that blindly.
      2. Multiply by engine_trust(engine_norm_cp, max_cp_drop). This is what
         keeps a strongly-persona-preferred candidate that is 70cp worse than
         the engine's best from leapfrogging near-best candidates.

    `phase` is accepted for API stability but intentionally NOT applied here:
    phase damping belongs INSIDE the future persona weight functions (it must
    see the king-pressure subcomponent to damp only that, not the whole
    aggregate). See the module docstring's ARCHITECTURE NOTE.
    """
    clamped = max(-1.0, min(1.0, raw_persona_score))
    return clamped * engine_trust(engine_norm_cp, max_cp_drop)
