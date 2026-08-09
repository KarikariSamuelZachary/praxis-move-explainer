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

    For each candidate i, compute a base weight from its
    `policy` field (the softmax probability of the candidate, exposed
    by scripts/maia3_patched_uci.py -- see the POLICY-PATCH NOTE in
    src/engines/maia_engine.py). If the policy field is absent (e.g.
    the patch is broken, the wrapper is missing from disk, or
    upstream maia3 changed its UCI format), fall back to the
    geometric rank-decay proxy at BASE_RANK_DECAY -- this is the
    documented coarse stand-in (see the "BASE_RANK_DECAY" paragraph
    below).

    The bias then multiplies this base weight upwards for
    sacrifice-looking candidates, scaling with the opponent's
    sacrifice_frequency. The same re-weighting pattern is used for
    the queen-trade signal (see decision (4) below) -- per-candidate
    multipliers compose multiplicatively on top of the base rate, so
    each signal tilts Maia's distribution independently.

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

(4) QUEEN-TRADE BIAS (multiplicative with sac, gated by a timing
    window): the opponent's `queens_stay_on_rate` (fraction of games
    ending with both queens on) and `queen_trade_move_number` (weighted
    mean ply at which the last queen was captured) are converted into a
    per-candidate multiplier.

    Derivation:
      qtp            = 1.0 - queens_stay_on_rate          (in [0, 1])
      centered       = 2.0 * qtp - 1.0                   (in [-1, 1])
      window_weight  = max(0, 1 - |candidate_ply -
                                  queen_trade_move_number| / half_width)
                                                            (in [0, 1])
      qt_mult        = clamp(1.0 + QUEEN_TRADE_BIAS_STRENGTH *
                              centered * window_weight * is_qt,
                              min=0.05)

    where `is_qt` is the per-candidate "this move captures a queen"
    indicator and `clamp` keeps weights strictly positive (random.choices
    rejects non-positive weights). The clamp is a defensive floor --
    in the calibration range used here (centered in [-1, 1],
    window_weight in [0, 1], strength=1.5) the floor only triggers at
    the extreme (centered=-1, is_qt=1, window_weight=1) which represents
    an opponent who NEVER trades queens and we're offering a queen-
    capture move at the most in-character moment -- the right
    behavior there is to strongly suppress but not literally zero out.

    Why a window_weight on `queen_trade_move_number`: the timing signal
    tells us WHEN the opponent typically trades queens. A queen trade at
    that ply is on-pattern; a queen trade 30 plies later is anti-pattern
    (game has already moved past the typical structure) and shouldn't be
    amplified. The window_weight makes the bias a local phenomenon --
    only fires in the neighborhood of the opponent's typical trade
    timing. `queen_trade_move_number=None` (no qualifying games) maps to
    window_weight=1.0 (no timing gate; only the preference signal from
    queens_stay_on_rate applies).

    Why multiplicative with the sac multiplier: the two signals are
    independent stylistic axes (material aggression vs. queen-trade
    structure preference). Either can apply on its own; both can apply
    on the same candidate without double-counting. The product form
    preserves each signal's relative strength while letting them
    compound on candidates that are both sac-looking AND queen-trades.

    Why not used for suppression of QUIET moves: the bias is symmetric
    around 1.0 -- if `centered<0` and `is_qt=1`, the weight is reduced
    (prefer not to trade queens). For `is_qt=0` the multiplier is 1.0
    regardless of centered, so quiet candidates are unaffected. This
    matters: a "queens-stay-on" opponent who happens to be in a
    position with a strong sac-looking non-queen-trade move should
    still be able to play that move at base rate.

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

BASE_RANK_DECAY sets how steeply base weights fall with rank in the
FALLBACK path (when the patched UCI wrapper isn't in use and the
candidate dict lacks a `policy` field). We use geometric base=0.5:
rank 1 -> 1.0, rank 2 -> 0.5, rank 3 -> 0.25, ...

  Justification: Maia's topk softmax typically concentrates 40-70% of
  mass on rank 1 and decays steeply (the standard "human-like" move
  distribution is long-tailed but rank-1-dominant). Base=0.5 reproduces
  that shape coarsely without claiming fidelity we don't have. The
  PATCHED path (candidate["policy"] present) is preferred when
  available -- it uses the actual softmax probability from the model
  and is strictly more accurate than any rank-decay proxy. The proxy
  remains as a defense-in-depth fallback: if the patch is broken, the
  reranker still works (no crash, no exception) but the base weight is
  approximated. verify_maia3_patch() in maia_engine.py catches the
  broken-patch case at startup and logs at ERROR.

QUEEN_TRADE_BIAS_STRENGTH tunes how strongly `queens_stay_on_rate` and
`queen_trade_move_number` tilt the per-candidate weight for a move that
captures a queen. Calibration at strength=1.5:

  Effective relative weight of a queen-trade candidate (peak timing,
  full window_weight=1):
    centered= 1.0  (opponent always trades)   -> mult = 1 + 1.5*1*1 = 2.5
    centered= 0.5  (opponent trades half)     -> mult = 1 + 1.5*0.5 = 1.75
    centered= 0.0  (neutral)                  -> mult = 1.0  (no effect)
    centered=-0.5  (opponent keeps queens)    -> mult = 1 - 1.5*0.5 = 0.25
    centered=-1.0  (opponent never trades)    -> clamped to 0.05 floor

  Outside the timing window (|candidate_ply - queen_trade_move_number|
  >= half_width) the mult is 1.0 -- the timing gate makes this a
  LOCAL bias, not a global one. Picked to be stronger than the sac
  signal at peak (sac max ~1.6x at sac_freq=0.15) because queen trade
  is a clearer stylistic preference (binary: did the opponent usually
  trade queens?) than sac frequency (continuous, noisy).

QUEEN_TRADE_WINDOW_HALF_WIDTH is the ply-distance at which the timing
gate reaches zero. Set to 24 plies (12 fullmoves): a queen trade within
12 fullmoves of the opponent's typical trade point gets the full bias,
and a queen trade 12+ fullmoves away gets no bias. Wide enough to
absorb normal game-length variance; narrow enough to suppress the bias
on moves well past the opponent's typical trade timing.

QUEEN_TRADE_WEIGHT_FLOOR is the minimum per-candidate multiplier after
the queen-trade bias. Defensive: keeps weights strictly positive
(random.choices rejects non-positive weights). Only triggers at the
extreme (centered=-1, is_qt=1, full window); the typical-calibration
multipliers all sit comfortably above it.

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

# How strongly a high queen-trade preference tilts the weight for a
# move that captures a queen. See the module docstring's
# "QUEEN_TRADE_BIAS_STRENGTH" paragraph.
QUEEN_TRADE_BIAS_STRENGTH = 1.5

# Half-width (in plies) of the triangular timing window centered on the
# opponent's typical queen-trade ply. See the module docstring's
# "QUEEN_TRADE_WINDOW_HALF_WIDTH" paragraph.
QUEEN_TRADE_WINDOW_HALF_WIDTH = 24.0

# Defensive floor on the per-candidate queen-trade multiplier. Keeps
# weights strictly positive so random.choices doesn't reject the input.
# See the module docstring's "QUEEN_TRADE_WEIGHT_FLOOR" paragraph.
QUEEN_TRADE_WEIGHT_FLOOR = 0.05

# --- setup-structure signature (v1) ----------------------------------------
#
# Per-candidate multiplicative boost for moves whose resulting board shape
# (pawn skeleton + piece squares, POV-normalized per
# opponent_style._pov_snapshot_squares) matches a shape the profiled player
# has actually reached in their historic games. The signatures come from
# `style["setup_signatures"]` (produced by compute_opponent_style). See the
# design spec docstring in opponent_style.py for the rationale.
#
# SETUP_SIGNATURE_BIAS_STRENGTH = max boost strength. sig_mult = 1 + B * S
# where S in [0, 1] is the max Jaccard composite across all historic
# snapshots. With B = 2.5, sig_mult in [1.0, 3.5]: boost-only (no
# suppression of non-matching candidates), see the spec's 7.1.
# Default 2.5 keeps Maia's policy as the default and tilts toward
# setup-consistent moves when there's match evidence; tune empirically via
# Test 10's head-to-head distribution.
SETUP_SIGNATURE_BIAS_STRENGTH = 2.5

# Jaccard composite weights. Pawn structure leads (most stable oracle of
# opening setup in chess theory); piece placement disambiguates two setups
# with similar pawn skeletons but different piece development (e.g.
# ...Bf5 vs ...Bg4). The two weights MUST sum to 1.0; the composite is
# `S = w_pawn * J_pawn + w_piece * J_piece` in [0, 1].
SETUP_PAWN_WEIGHT = 0.65
SETUP_PIECE_WEIGHT = 0.35

# SETUP_FAMILY_DETECTION_THRESHOLD = min Jaccard composite a snapshot
# family must reach against the candidate's live position for the family
# to be considered a viable match. Below this we treat it as "no family
# evidence" and fall back to the UNFILTERED snapshot set (preserves the
# reranker's pre-family-filter behavior). The Jaccard here includes BOTH
# the user's pawn set AND the opponent's pawn set (POV-mirrored), since
# openings are defined by both sides' pawn shapes -- Italian (Black e5)
# and Scandinavian (Black d-pawn traded) share the user's pawn skeleton
# but differ in opp_pawn shape, so opp_pawn is what disambiguates.
# Default 0.5: a real match (profiled player played the same setup)
# typically scores S >= 0.85 with opp_pawns included, so 0.5 leaves
# headroom for pawn-count differences (e.g. transposed move order where
# one side hasn't pushed the border pawn yet) while filtering openings
# whose pawn shape is structurally different.
SETUP_FAMILY_DETECTION_THRESHOLD = 0.5

# Sentinel returned in bias_breakdown's "family_lean" key to mark that v1
# made an explicit no-bias decision rather than silently dropping the
# signal. See the module docstring's decision (1).
FAMILY_LEAN_DISABLED = "disabled_in_v1_no_candidate_family_classifier"


def _base_weight(rank: int) -> float:
    """Geometric base weight from a 1-indexed rank -- the FALLBACK path.

    rank=1 -> 1.0, rank=2 -> 0.5, rank=3 -> 0.25, ... Used when the
    candidate dict lacks a `policy` field (i.e. the patched UCI wrapper
    isn't in use). See the module docstring's "BASE_RANK_DECAY" note
    for the trade and the upgrade path.
    """
    return BASE_RANK_DECAY ** max(0, rank - 1)


def _candidate_base_weight(candidate: Dict[str, Any], rank: int) -> tuple:
    """Base weight for sampling, plus a flag indicating which path was used.

    Returns (weight, used_policy). `used_policy=True` means the
    candidate's `policy` field was present and used; `used_policy=False`
    means the rank-decay proxy was used (patch missing or policy value
    malformed). The flag is surfaced in the per-row breakdown so
    operators can see which path the reranker took.

    A candidate's policy is considered usable iff it parses to a float
    in (0, 1]. We don't require it sum to 1.0 across the candidate
    list (Maia-3's topk returns only the top-N; the rest of the mass is
    on candidates not returned by analyse()).
    """
    policy = candidate.get("policy")
    if policy is not None:
        try:
            p = float(policy)
            if 0.0 < p <= 1.0:
                return p, True
        except (TypeError, ValueError):
            pass
    return _base_weight(rank), False


def _candidate_ply(board: chess.Board) -> int:
    """1-indexed half-move ply at which a candidate move from `board` lands.

    White's first move is ply 1, black's first ply 2, etc. Used to compare
    against the opponent's `queen_trade_move_number` (a ply count) for the
    queen-trade timing window. Verified by hand against the standard
    start position: start -> 1, after 1.e4 -> 2, after 1.e4 e5 -> 3.
    """
    return board.fullmove_number * 2 - (1 if board.turn == chess.WHITE else 0)


def _is_queen_trade_move(board: chess.Board, candidate_uci: str) -> bool:
    """A candidate is a queen-trade move iff it captures a queen on its
    destination square.

    Defensive on every edge: non-legal candidate, unparseable UCI, or
    a move to an empty square all return False. En-passant and
    promotions are handled implicitly (en passant never captures a
    queen; promotion captures on the destination square use the same
    `piece_at(to_square)` lookup).
    """
    try:
        candidate = chess.Move.from_uci(candidate_uci)
    except ValueError:
        return False
    if candidate not in board.legal_moves:
        return False
    captured = board.piece_at(candidate.to_square)
    return captured is not None and captured.piece_type == chess.QUEEN


def _queen_trade_window_weight(
    current_ply: int,
    opponent_trade_ply: Optional[float],
    half_width: float = QUEEN_TRADE_WINDOW_HALF_WIDTH,
) -> float:
    """Triangular timing window in [0, 1] centered on opponent_trade_ply.

    Returns 1.0 if we're exactly at the typical trade ply, 0.0 if we're
    half_width or more plies away, and a linear ramp in between. Used to
    gate the queen-trade bias so it only fires in the neighborhood of
    the opponent's typical trade point.

    `opponent_trade_ply=None` (no qualifying games for this opponent)
    returns 1.0 -- no timing info means we don't gate, only the
    `queens_stay_on_rate` preference signal applies.
    """
    if opponent_trade_ply is None or opponent_trade_ply <= 0:
        return 1.0
    delta = abs(current_ply - float(opponent_trade_ply))
    if delta >= half_width:
        return 0.0
    return 1.0 - (delta / half_width)


def _pov_normalized_squares(
    board: chess.Board, side_just_moved: chess.Color
) -> tuple:
    """Return (pawn_set, piece_set, opp_pawn_set) for `board` normalized
    to side_just_moved's POV.

    Mirrors `opponent_style._pov_snapshot_squares`'s convention so the
    resulting-board signature computed here is directly comparable to the
    historic snapshots stored in `style["setup_signatures"]` (both are
    POV-normalized to "the profiled player advances from rank 1").

    Returns three frozensets of square names:
      * pawn_set      -- all pawn squares (POV-normalized)
      * piece_set     -- union of all N/B/R/Q/K squares (POV-normalized)
      * opp_pawn_set  -- opponent's pawns (POV-normalized via mirror);
                         used ONLY by `_filter_signatures_by_family` to
                         disambiguate openings whose user-side pawn shape
                         is identical (e.g. Italian vs Scandinavian).
                         `_setup_similarity` ignores this field -- the
                         setup_mult bias is computed on user-side shape
                         only, matching the spec.

    The two scoring sets are returned as separate frozensets because the
    Jaccard composite weights them differently (pawn structure leads; see
    SETUP_PAWN_WEIGHT). The piece set is one flattened union (not
    per-piece-type) to keep the math simple and cheap; per-piece-type
    Jaccard was considered and deferred as marginal-signal-vs-cost.
    """
    if side_just_moved == chess.BLACK:
        b = board.mirror()
    else:
        b = board
    pawn_set = frozenset(
        chess.square_name(sq) for sq in b.pieces(chess.PAWN, chess.WHITE)
    )
    piece_set = frozenset(
        chess.square_name(sq)
        for ptype in (chess.KNIGHT, chess.BISHOP, chess.ROOK,
                      chess.QUEEN, chess.KING)
        for sq in b.pieces(ptype, chess.WHITE)
    )
    opp_pawn_set = frozenset(
        chess.square_name(sq) for sq in b.pieces(chess.PAWN, chess.BLACK)
    )
    return pawn_set, piece_set, opp_pawn_set


def _setup_similarity(
    cand_pawn: frozenset,
    cand_piece: frozenset,
    hist_pawn: frozenset,
    hist_piece: frozenset,
) -> float:
    """Jaccard composite similarity between a candidate's resulting board
    and one historic snapshot, both already POV-normalized.

    S = SETUP_PAWN_WEIGHT * J_pawn + SETUP_PIECE_WEIGHT * J_piece
    where each J is the standard Jaccard index over the corresponding set:
        J = |A ∩ B| / |A ∪ B|   in [0, 1].

    Empty-set convention: two empty sets are VACUOUSLY IDENTICAL (J=1.0),
    matching the standard mathematical convention. A real position in
    the [10, 20] ply window always has pawns (so J_pawn's empty-both
    case is a degenerate test-only corner), and always has a king (so
    J_piece can only be empty in malformed-input cases -- we still
    return J=1.0 rather than 0.0 to keep the "identical" semantics).

    Jaccard (not Dice) because both directions matter for setup matching:
    a candidate position with extra developed pieces the historic lacks
    isn't the same setup -- Jaccard penalizes that symmetrically, Dice
    would over-credit near-subsets. See the spec's 5.

    Returns 0.0 iff both unions are empty AND we fall through to the
    RV=0 fallback (never happens for any real position; defensive).
    """
    pawn_union = cand_pawn | hist_pawn
    piece_union = cand_piece | hist_piece
    j_pawn = (
        1.0 if not pawn_union
        else (len(cand_pawn & hist_pawn) / len(pawn_union))
    )
    j_piece = (
        1.0 if not piece_union
        else (len(cand_piece & hist_piece) / len(piece_union))
    )
    return SETUP_PAWN_WEIGHT * j_pawn + SETUP_PIECE_WEIGHT * j_piece


def _candidate_setup_mult(
    resulting_board: chess.Board,
    side_just_moved: chess.Color,
    hist_signatures: Optional[List[Dict[str, Any]]],
) -> tuple:
    """Compute the per-candidate setup-signature multiplier.

    Returns (sig_mult, best_S, matched_ply):
      * sig_mult  -- 1 + SETUP_SIGNATURE_BIAS_STRENGTH * max_S, in
                     [1.0, 1 + SETUP_SIGNATURE_BIAS_STRENGTH]. Boost-only
                     (never below 1.0). When `hist_signatures` is None or
                     empty, returns (1.0, None, None) -- the feature is a
                     no-op for this candidate (no historic evidence).
      * best_S    -- the max Jaccard composite across all historic
                     snapshots (None iff no signatures were available).
      * matched_ply -- the snapshot_ply of the best-matching historic
                     snapshot (None iff no signatures were available).
                     Surfaced in the per-row audit for operator inspection.

    The resulting_board is the board AFTER pushing the candidate; the
    caller is responsible for the push (and for not mutating the live
    board -- use board.copy(stack=False)). `side_just_moved` is the color
    of the side that just moved (i.e. the profiled player's color in the
    live sparring session), used for POV normalization.

    Cost: ~N signatures x 1 Jaccard composite per candidate. With N=250
    (the cap) and 5 candidates, total ~1250 Jaccards on ~15-element
    sets -- well under 1ms in Python. Cheap vs the 1-3s Maia inference.
    """
    if not hist_signatures:
        return 1.0, None, None

    cand_pawn, cand_piece, _ = _pov_normalized_squares(
        resulting_board, side_just_moved
    )

    best_s = 0.0
    best_ply: Optional[int] = None
    for snap in hist_signatures:
        # Historic snapshot shape: {"pawn_squares": [...],
        # "piece_squares": {"N": [...], ...}, "snapshot_ply": int}.
        # Frozen once for cheap intersection.
        hist_pawn = frozenset(snap.get("pawn_squares") or ())
        hist_piece_list = snap.get("piece_squares") or {}
        hist_piece = frozenset(
            sq
            for letter in ("N", "B", "R", "Q", "K")
            for sq in (hist_piece_list.get(letter) or ())
        )
        s = _setup_similarity(cand_pawn, cand_piece, hist_pawn, hist_piece)
        if s > best_s or (s == best_s and best_ply is None):
            # Strict-greater keeps the FIRST max (deterministic for ties);
            # the second clause picks any ply when best_s is still 0.0 so
            # a 0-similarity match still reports a ply (rare; only if
            # every signature shares nothing with the candidate).
            best_s = s
            best_ply = snap.get("snapshot_ply")

    sig_mult = 1.0 + SETUP_SIGNATURE_BIAS_STRENGTH * best_s
    return sig_mult, best_s, best_ply


def _filter_signatures_by_family(
    signatures: Optional[List[Dict[str, Any]]],
    cand_pawn: frozenset,
    cand_opp_pawn: frozenset,
    player_color: Optional[str] = None,
    threshold: float = SETUP_FAMILY_DETECTION_THRESHOLD,
) -> tuple:
    """Filter historic setup-signatures by opening family before setup_mult.

    This is the fix for the "Scandinavian Ne2 vs Bd2" bug. Without family
    filtering, the reranker compared a candidate's resulting board against
    the WHOLE snapshot pool, which for the user (200 games, 7 Scandi)
    contains ~190 Italian/Scotch/Caro-Kann etc snapshots whose user-side
    pawn shape happens to look a lot like Scandi's (both have e4 and d3
    pawns for White). The non-Scandi majority drowned Scandi's signal,
    and the bot picked Ne2 (a move the user has played in Scandi, but
    also happens to match Caro-Kann-ish shapes).

    Family detection algorithm:
      * For each snapshot that has a `family` tag, compute the Jaccard
        of `cand_pawn ∪ cand_opp_pawn` against the snapshot's
        `pawn_squares ∪ opp_pawn_squares`.
      * The `cand_opp_pawn` set disambiguates openings that share
        user-side pawn skeleton: Italian Black has e5 pawn still on the
        board, Scandinavian Black has the d-pawn traded -- so the union
        Jaccard differs between the two families.
      * Per family, track the MAX Jaccard across snapshots in that
        family (the best single historic match) and also the vote count
        (how many snapshots in that family exceeded `threshold`).
      * Pick the family with the highest max Jaccard. Ties broken by
        votes (a family with more match evidence wins ties). We use
        MAX Jaccard (not vote-count) as the primary key because for
        minority openings like Scandi (7 games) a vote-count winner
        would always be the dominant family (Caro-Kann 105 games) even
        when Scandinavian has a perfect S=1.0 match.
      * Return the filtered snapshot list (snapshots in the winning
        family, optionally also matching player_color).

    Returns:
      (filtered_sigs, family_label, confidence)
        * filtered_sigs: list of snapshots in the winning family.
          Returned as-is (caller iterates for setup_mult). When the
          input lists have no `family` tags (i.e. snapshots produced
          before this feature shipped), returns the ORIGINAL list
          unchanged + family_label=None+confidence=None -- this is the
          backward-compat path that preserves the pre-filter behavior
          for existing tests and for any style cache produced before
          the upgrade.
        * family_label: str (e.g. "Scandinavian Defense") or None.
        * confidence: float in [0, 1] = the winning family's MAX
          Jaccard (the strongest single-snapshot match in that family).
          None on the backward-compat path.

    Edge cases:
      * Input signatures is None/empty -> returns (None, None, None).
        Caller in `rerank_candidates` treats this as "no setup signal"
        and setup_mult=1.0 for every candidate.
      * No snapshot has a `family` tag (all-snapshots-untagged path) ->
        returns (signatures, None, None) -- backward-compat.
      * The winning family's max Jaccard is BELOW `threshold` ->
        returns (signatures, None, None) -- i.e. "no family evidence"
        so we DON'T restrict to a single family (which would be a
        misleading confinement); preserve the unfiltered pool.
      * `player_color` filter: when supplied ("white"|"black"), only
        snapshots where `snap["player_color"] == player_color` are
        returned from the winning family. The user's Scandi games are
        all as white -- a position the user is playing AS BLACK should
        not borrow their white-Scandi snapshots.
    """
    if not signatures:
        return None, None, None

    # Build per-family aggregated state: family -> {max_s, votes}
    family_state: Dict[str, Dict[str, Any]] = {}
    any_tagged = False
    for snap in signatures:
        family = snap.get("family")
        if not family:
            continue
        any_tagged = True
        hist_pawn = frozenset(snap.get("pawn_squares") or ())
        hist_opp_pawn = frozenset(snap.get("opp_pawn_squares") or ())
        # Union Jaccard across both sides' pawn shapes -- this is the
        # disambiguating metric. Italian vs Scandinavian have identical
        # user-pawn skeletons but different opp-pawn shapes, so the
        # union Jaccard catches the difference. (We don't weight pieces
        # here intentionally: family detection is purely structural on
        # pawn shape; pieces vary too much move-to-move inside one
        # opening to be a reliable family marker.)
        union_cand = cand_pawn | cand_opp_pawn
        union_hist = hist_pawn | hist_opp_pawn
        if not union_cand and not union_hist:
            # Degenerate both-empty; vacuous match (J=1.0). Only
            # matters in test fixtures; defensive.
            j = 1.0
        elif not (union_cand and union_hist):
            # One side empty, the other not: zero overlap.
            j = 0.0
        else:
            j = len(union_cand & union_hist) / len(union_cand | union_hist)
        state = family_state.setdefault(family, {"max_s": 0.0, "votes": 0})
        if j > state["max_s"]:
            state["max_s"] = j
        if j >= threshold:
            state["votes"] += 1

    if not any_tagged:
        # Backward-compat: snapshots lack family tags (produced before
        # this feature shipped, or by an old test fixture). Return the
        # original list so behavior is unchanged.
        return list(signatures), None, None

    if not family_state:
        # All snapshots had empty/None family tags (shouldn't happen
        # given the any_tagged check above, but defensive): fallback
        # to unfiltered.
        return list(signatures), None, None

    # Pick winner: highest max Jaccard; ties broken by votes.
    best_family = None
    best_max_s = 0.0
    best_votes = -1
    for family, state in family_state.items():
        if state["max_s"] > best_max_s or (
            state["max_s"] == best_max_s and state["votes"] > best_votes
        ):
            best_family = family
            best_max_s = state["max_s"]
            best_votes = state["votes"]

    if best_max_s < threshold:
        # No family reached the match threshold -- preserve unfiltered
        # pool so the user's minority-opening signal isn't lost. This
        # is the "fall back, don't restrict" branch.
        return list(signatures), None, None

    # Filter to the winning family (and optionally player_color).
    filtered: List[Dict[str, Any]] = []
    for snap in signatures:
        if snap.get("family") != best_family:
            continue
        if player_color is not None and snap.get("player_color") != player_color:
            continue
        filtered.append(snap)
    if not filtered:
        # Threshold said match but filtering by color emptied the set
        # (e.g. user is playing black but their family match is only
        # in white-POV snapshots). Fall back to the unfiltered pool so
        # we don't silence the signal entirely.
        return list(signatures), None, None

    return filtered, best_family, best_max_s


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
            Optional fields consumed: "sacrifice_frequency" (sac
            multiplier), "queens_stay_on_rate" and
            "queen_trade_move_number" (queen-trade multiplier).
        board: The chess.Board the candidates are FOR (the same board
            best_move_candidates was called with). Used only to push
            candidate copies for live-sac detection and to inspect the
            destination square for queen-trade detection -- never
            mutated.
        rng: Optional pre-seeded random.Random instance; if None, the
            module-level `random` is used (matches pick_repertoire_move's
            pattern). Tests should pass a seeded Random for
            reproducibility; production should pass None.

    Returns:
        dict with:
            chosen_move_uci: str        -- the chosen candidate's UCI
            chosen_index: int           -- index in the input list
            applied_bias: bool          -- True iff sufficient=True AND
                                           at least one per-signal
                                           multiplier (sac_mult or
                                           qt_mult) actually deviated
                                           from 1.0 on at least one
                                           candidate. False if
                                           insufficient OR no
                                           candidate's bias_multiplier
                                           differs from 1.0 by more
                                           than 1e-9 (i.e. the bias
                                           was triggered in principle
                                           but its calibration was
                                           zero, e.g. sac_freq=0 or
                                           the queen-trade timing
                                           window is fully closed).
                                           "Indicator fired" alone is
                                           not enough -- the
                                           calibration has to actually
                                           move the multiplier.
            source: str                  -- "style_biased"             |
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
            base_source: str             -- "policy" | "rank_decay" |
                                           "mixed". Which base-weight
                                           path the reranker took.
                                           "policy" = every candidate
                                           had a usable policy field
                                           (the patched UCI wrapper
                                           is in use). "rank_decay" =
                                           no candidate had a policy
                                           field (patch missing; the
                                           reranker used the geometric
                                           rank-decay proxy as a
                                           fallback). "mixed" = some
                                           had it, some didn't. Useful
                                           for operators to audit
                                           whether the patch is live
                                           without re-running
                                           verify_maia3_patch().
            bias_breakdown: dict|None  -- per-candidate weight trace
                                           so callers can introspect
                                           what the re-ranker actually
                                           saw and how it weighted.
                                           Only populated when
                                           applied_bias=True.
                                           Shape:
                                             {
                                               "weights": [ {index,
                                                             move, rank,
                                                             base_weight,
                                                             base_source,
                                                             sac_indicator,
                                                             sac_multiplier,
                                                             qt_indicator,
                                                             qt_window_weight,
                                                             qt_multiplier,
                                                             bias_multiplier,
                                                             weight}, ... ],
                                               "family_lean": <sentinel>,
                                               "signals_applied":
                                                 ["sacrifice",
                                                  "queen_trade"]  # subset
                                             }
            game_count: int             -- surfaced from style, raw
                                           game count, for transparency

    Contracts:
      * Insufficient data (style["sufficient"]=False): returns
        candidates[0] deterministically, applied_bias=False,
        source="insufficient_data" -- the regression contract.
      * Sufficient data but no candidate's per-signal multiplier
        actually deviates from 1.0 (no sac candidates, or sac_freq=0;
        no queen-trade candidates, OR all queen-trade candidates have
        window_weight=0 / centered=0): returns candidates[0]
        deterministically, applied_bias=False,
        source="default_top_candidate". Same behavior as v1.
        "Indicator fired" is not enough -- the calibration has to
        actually move the multiplier off 1.0 for the bias to matter.
      * Sufficient data and >=1 candidate's per-signal multiplier
        actually deviates from 1.0: weighted random sample using
            weight[i] = base(rank_i) * sac_mult_i * qt_mult_i
        where:
            sac_mult_i = 1 + STYLE_BIAS_STRENGTH * sac_freq * sac_i
            qt_mult_i  = max(QUEEN_TRADE_WEIGHT_FLOOR,
                             1 + QUEEN_TRADE_BIAS_STRENGTH *
                                 centered * window_w * qt_i)
        applied_bias=True, source="style_biased", bias_breakdown
        populated (including the list of signals whose multiplier
        actually deviated from 1.0 on at least one candidate).

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
            "base_source": _derive_base_source([]),
            "setup_present": bool(style.get("setup_signatures")),
            "setup_family": None,
            "setup_family_confidence": None,
            "setup_filtered_count": 0,
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
            "base_source": _derive_base_source(candidates),
            "setup_present": bool(style.get("setup_signatures")),
            "setup_family": None,
            "setup_family_confidence": None,
            "setup_filtered_count": 0,
            "bias_breakdown": None,
            "game_count": style.get("game_count", 0),
        }

    # --- pre-compute signal strengths from the style profile -----------------
    sac_frequency = style.get("sacrifice_frequency") or 0.0

    # queens_stay_on_rate: float in [0, 1]. None (defensive) -> 0.5
    # (neutral, no preference), matching the pattern of "unknown signal
    # doesn't bias either direction".
    queens_stay_on_rate = style.get("queens_stay_on_rate")
    if queens_stay_on_rate is None:
        queens_stay_on_rate = 0.5
    queen_trade_pref = 1.0 - float(queens_stay_on_rate)
    qt_centered = 2.0 * queen_trade_pref - 1.0  # [-1, 1]

    queen_trade_move_number = style.get("queen_trade_move_number")
    current_ply = _candidate_ply(board)
    qt_window_w = _queen_trade_window_weight(
        current_ply, queen_trade_move_number
    )

    # setup-structure signatures from the style profile (None or a non-empty
    # list per compute_opponent_style's contract). Passed to
    # _candidate_setup_mult per candidate, with `side_just_moved` = the
    # side to move on `board` (the sparring bot's color -- the candidate,
    # once pushed, produces a resulting board on the OPPONENT's turn).
    setup_signatures = style.get("setup_signatures")
    setup_present = bool(setup_signatures)

    # Family-filter the snapshot pool once before the candidate loop.
    # We compute cand_pawn + cand_opp_pawn off the CURRENT board (the
    # board the candidates are for, BEFORE pushing any candidate -- the
    # family of the position doesn't change across candidate moves,
    # only the user-side pawn shape advances; using the pre-push board
    # saves N candidate-side snapshots of Jaccard work per family).
    #
    # If `_filter_signatures_by_family` finds a winning family above
    # SETUP_FAMILY_DETECTION_THRESHOLD, `setup_effective` is the
    # family-filtered subset. Otherwise it falls back to the unfiltered
    # pool (preserves pre-filter behavior, including a None-input that
    # makes setup_mult=1.0 for every candidate -- the standard no-signal
    # path).
    setup_filtered_family: Optional[str] = None
    setup_filtered_confidence: Optional[float] = None
    if setup_present and setup_signatures:
        live_pawn, _, live_opp_pawn = _pov_normalized_squares(board, board.turn)
        setup_effective, setup_filtered_family, setup_filtered_confidence = (
            _filter_signatures_by_family(
                setup_signatures, live_pawn, live_opp_pawn,
                player_color=None,  # color-filter is v2; v1 merges pool
            )
        )
    else:
        setup_effective = setup_signatures
    setup_filtered_count = (
        len(setup_effective) if setup_effective is not None else 0
    )

    # --- compute per-candidate live indicators and weights ------------------
    # base_weight_i = candidate["policy"] when the patched UCI wrapper
    # is in use (the actual softmax probability of the candidate), or
    # the geometric rank-decay proxy when it isn't. Three independent
    # multiplicative bias terms compose on top:
    #   sac_mult_i  = 1 + STYLE_BIAS_STRENGTH * sac_freq * sac_indicator_i
    #   qt_mult_i   = clamp(1 + QUEEN_TRADE_BIAS_STRENGTH *
    #                          qt_centered * qt_window_w * qt_indicator_i,
    #                       min=QUEEN_TRADE_WEIGHT_FLOOR)
    #   setup_mult_i = 1 + SETUP_SIGNATURE_BIAS_STRENGTH * max_similarity_i
    # weight_i = base_i * sac_mult_i * qt_mult_i * setup_mult_i
    weights: List[float] = []
    sac_mults: List[float] = []
    qt_mults: List[float] = []
    setup_mults: List[float] = []
    breakdown_rows: List[Dict[str, Any]] = []
    any_used_policy = False
    side_just_moved = board.turn
    for idx, candidate in enumerate(candidates):
        uci = candidate.get("move", "")

        is_sac = _is_live_sac_move(board, uci)
        sac_mult = 1.0 + STYLE_BIAS_STRENGTH * sac_frequency * (1.0 if is_sac else 0.0)
        sac_mults.append(sac_mult)

        is_qt = _is_queen_trade_move(board, uci)
        qt_mult_raw = 1.0 + QUEEN_TRADE_BIAS_STRENGTH * qt_centered * qt_window_w * (
            1.0 if is_qt else 0.0
        )
        qt_mult = max(QUEEN_TRADE_WEIGHT_FLOOR, qt_mult_raw)
        qt_mults.append(qt_mult)

        # Setup signature: push the candidate onto a board copy to get
        # the resulting board, then compute the POV-normalized similarity
        # to the historic snapshot set. board.copy(stack=False) is a
        # cheap root-pop-free shallow copy (we don't need move history
        # here, just the piece map). The push is reverted implicitly --
        # we don't reuse the copy across candidates.
        setup_mult = 1.0
        setup_S: Optional[float] = None
        setup_matched_ply: Optional[int] = None
        if setup_present and setup_effective:
            try:
                rb = board.copy(stack=False)
                mv = chess.Move.from_uci(uci) if uci else None
                if mv is not None and mv in rb.legal_moves:
                    rb.push(mv)
                    setup_mult, setup_S, setup_matched_ply = _candidate_setup_mult(
                        rb, side_just_moved, setup_effective
                    )
            except (ValueError, IndexError):
                # Defensive: a malformed UCI or an unexpected board state
                # should never silence the other biases -- fall back to
                # the no-effect setup_mult=1.0.
                setup_mult, setup_S, setup_matched_ply = 1.0, None, None
        setup_mults.append(setup_mult)

        base, used_policy = _candidate_base_weight(candidate, idx + 1)
        if used_policy:
            any_used_policy = True
        bias_mult = sac_mult * qt_mult * setup_mult
        weight = base * bias_mult
        weights.append(weight)
        breakdown_rows.append({
            "index": idx,
            "move": uci,
            "rank": idx + 1,
            "base_weight": round(base, 4),
            "base_source": "policy" if used_policy else "rank_decay",
            "sac_indicator": is_sac,
            "sac_multiplier": round(sac_mult, 4),
            "qt_indicator": is_qt,
            "qt_window_weight": round(qt_window_w, 4),
            "qt_multiplier": round(qt_mult, 4),
            "setup_S": (round(setup_S, 4) if setup_S is not None else None),
            "setup_multiplier": round(setup_mult, 4),
            "setup_matched_ply": setup_matched_ply,
            "bias_multiplier": round(bias_mult, 4),
            "weight": round(weight, 4),
        })

    # --- if no candidate's bias_multiplier actually deviates from 1.0, the
    # bias has no effect -- reflect that honestly in the result. Note this
    # is strictly stronger than "no indicator fired": a candidate CAN
    # trigger a signal (e.g. is_qt=True with a queen-capture move) but
    # have no effect on its weight if the signal's calibration is zero
    # (e.g. sac_freq=0, or window_weight=0, or centered=0). The right
    # "did anything tilt?" test is the actual multiplier, not the
    # indicator -- the indicator is the per-candidate TRIGGER for the
    # multiplier, but only the multiplier affects sampling.
    def _mult_deviated(mults: List[float]) -> bool:
        # Float compare with a tiny epsilon to absorb rounding noise; the
        # multipliers are computed from a closed-form product of
        # closed-form scalars so a true "exactly 1.0" is the only case
        # the no-bias path cares about.
        return any(abs(m - 1.0) > 1e-9 for m in mults)

    sac_actually_biased = _mult_deviated(sac_mults)
    qt_actually_biased = _mult_deviated(qt_mults)
    setup_actually_biased = _mult_deviated(setup_mults)
    # base_source: "policy" if every candidate had a usable policy,
    # "rank_decay" if every candidate was missing one, "mixed" if the
    # list had both. Surface this at the top level of the result so
    # operators can audit which path the reranker took. This is purely
    # informational -- the reranker still works with any combination.
    if any_used_policy:
        base_source = "policy" if all(
            row.get("base_source") == "policy" for row in breakdown_rows
        ) else "mixed"
    else:
        base_source = "rank_decay"
    if not (sac_actually_biased or qt_actually_biased or setup_actually_biased):
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
            "base_source": base_source,
            "setup_present": setup_present,
            "setup_family": setup_filtered_family,
            "setup_family_confidence": (
                round(setup_filtered_confidence, 4)
                if setup_filtered_confidence is not None else None
            ),
            "setup_filtered_count": setup_filtered_count,
            "bias_breakdown": None,
            "game_count": style.get("game_count", 0),
        }

    # --- record which signals actually contributed (so callers can audit
    # the bias breakdown without recomputing). A signal is "applied" iff
    # it actually tilted at least one candidate's weight.
    signals_applied: List[str] = []
    if sac_actually_biased:
        signals_applied.append("sacrifice")
    if qt_actually_biased:
        signals_applied.append("queen_trade")
    if setup_actually_biased:
        signals_applied.append("setup_signature")

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
        "source": "style_biased",
        "sacrifice_frequency": sac_frequency,
        "opening_family_lean": style.get("opening_family_lean"),
        "base_source": base_source,
        "setup_present": setup_present,
        "setup_family": setup_filtered_family,
        "setup_family_confidence": (
            round(setup_filtered_confidence, 4)
            if setup_filtered_confidence is not None else None
        ),
        "setup_filtered_count": setup_filtered_count,
        "bias_breakdown": {
            "weights": breakdown_rows,
            "family_lean": FAMILY_LEAN_DISABLED,
            "signals_applied": signals_applied,
        },
        "game_count": style.get("game_count", 0),
    }


def _derive_base_source(candidates: List[Dict[str, Any]]) -> str:
    """Returns "policy" | "rank_decay" | "mixed" for a candidate list.

    Used at the top of the return-shape so operators can audit which
    base-weight path the reranker took, even on the no-bias paths
    (insufficient_data, default_top_candidate, no_candidates) where no
    bias_breakdown is returned.

    Empty input -> "rank_decay" (no candidates means no policy to read;
    this is a vacuous label).
    """
    if not candidates:
        return "rank_decay"
    has_policy = 0
    has_rank = 0
    for c in candidates:
        policy = c.get("policy")
        if policy is not None:
            try:
                p = float(policy)
                if 0.0 < p <= 1.0:
                    has_policy += 1
                    continue
            except (TypeError, ValueError):
                pass
        has_rank += 1
    if has_policy and not has_rank:
        return "policy"
    if has_rank and not has_policy:
        return "rank_decay"
    return "mixed"