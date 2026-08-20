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

(5) CASTLE-SIDE BIAS (multiplicative with sac, qt, setup; gated by
    castling rights and ply): the opponent's `castling_side_distribution`
    ({kingside, queenside, never} fractions) is converted into a per-
    candidate multiplier. The signal was already computed by
    compute_opponent_style in v1 but never consumed by the reranker;
    this wires it in.

    Derivation:
      (pref_side, pref_strength) = _castle_preference(castling_dist)
        where pref_side is "kingside" or "queenside" (or None if "never"
        dominates or the signal is too weak), and pref_strength is
        |kingside_frac - queenside_frac| in [0, 1].
      indicator_i = _castle_indicator(board, candidate_i, pref_side)
        in {+1, 0, -1}: +1 if the candidate helps the preferred castle
        side (clears a path square, or is the castle move itself on the
        preferred side), -1 if it hurts (king move losing rights,
        preferred-side rook move, piece moves INTO the preferred path,
        or the castle move on the non-preferred side), 0 otherwise.
      castle_mult_i = clamp(1 + CASTLE_BIAS_STRENGTH * pref_strength *
                              indicator_i,
                            min=CASTLE_WEIGHT_FLOOR)

    Gating: the bias only fires when ALL of:
      * pref_strength > CASTLE_PREFERENCE_THRESHOLD (0.3) -- below this
        the opponent's preference is too weak to tilt Maia's distribution.
      * current_ply <= CASTLE_BIAS_PLY_MAX (40) -- past move 20, castling
        decisions are settled and the bias becomes noise.
      * the bot still has castling rights on the preferred side -- once
        rights are lost (king moved, preferred-side rook moved, or the
        bot has already castled), the bias is moot and shouldn't punish
        the bot for game-state constraints it can't control.

    Why multiplicative with the other signals: castle-side preference is
    an independent stylistic axis from material aggression (sac), queen-
    trade structure, or setup-structure similarity. A high-sac opponent
    who also prefers kingside castling should see both biases compound on
    a sac-looking move that also clears the kingside path -- the product
    form preserves each signal's relative strength.

    Why the "wrong-side castle" gets indicator=-1 (not 0): playing O-O
    when the opponent prefers O-O-O is the clearest "bot is not mimicking
    the opponent" signal -- it's a binary, irreversible commitment to the
    opposite castle pattern. Penalizing it steers the bot toward the
    opponent's actual pattern. The penalty is capped by
    CASTLE_WEIGHT_FLOOR so the bot can still play O-O if Maia's base
    weight strongly favors it (the floor prevents the bias from
    zeroing out a clearly-good move).

(6) TRAP-MODE / MIRROR-MODE BRANCH (multiplicative with all existing
    signals; gated by an exploitability floor, one-ply reachability, and
    the existing sufficient-data contract).

    Product decision (explicit, not implicit): sparring is DRILL-WITH-
    FALLBACK. When the opponent has a known, statistically real blunder
    pattern reachable this move, the bot steers toward it (drill =
    trap-mode). When it doesn't, the bot plays like the opponent would
    (mirror = today's reranker). This is a deliberate product choice,
    recorded here so it isn't re-litigated in a future session.

    WHAT "EXPLOITABLE" MEANS. A position_key (as already defined by
    opponent_repertoire.py / the opponent_game_blunders table -- first
    4 FEN fields) is exploitable for a given opponent iff ALL of:

      (a) The opponent has blundered from this exact position_key >= 2
          times across their imported games (TRAP_MIN_HITS = 2, in
          opponent_traps.py).
      (b) The opponent has >= 5 total games in the imported dataset
          (TRAP_MIN_GAMES, in opponent_traps.py, reusing the same floor
          spirit as MIN_STYLE_GAMES / MIN_REPERTOIRE_SAMPLES elsewhere in
          the codebase -- kept as its own named constant, not imported,
          matching how STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR is a separate
          constant from the repertoire sampler's decay rate despite the
          same value).

    Both gates must pass. A 2-game opponent with 2 identical blunders
    does NOT count (fails gate b). A 50-game opponent with exactly 1 hit
    on a position_key does NOT count (fails gate a) -- single-occurrence
    "blunders" are not a pattern, they're noise; do not treat them as
    exploitable no matter how large the opponent's overall game count is.

    This is intentionally EXACT position_key matching, not a fuzzy
    family/motif match. A softer secondary key (same opening + same
    tactical motif) is a plausible v2 extension but out of scope here --
    starting with exact matching keeps the signal unambiguous and avoids
    inheriting the family-detection complexity already built for setup
    signatures (_filter_signatures_by_family) before there's evidence
    the trap signal needs it.

    REACHABILITY: STRICTLY ONE-PLY, NO LOOKAHEAD. The trap signal is
    evaluated per candidate, on the resulting board after that candidate
    is pushed -- exactly the same shape as _is_live_sac_move,
    _is_queen_trade_move, and _candidate_setup_mult. A candidate is
    "trap-triggering" iff its resulting position_key matches an
    exploitable key from the opponent's blunder table.

    There is NO multi-ply search for "is there a path toward a trap 3
    moves from now." This is a real, deliberate scope limit, not an
    oversight: trap-mode is OPPORTUNISTIC, not STRATEGIC. If the
    exploitable position is reachable only via a sequence of moves,
    trap-mode stays silent until the position is one candidate-move
    away. This matches every other signal in the reranker (all are
    one-ply-forward checks) and keeps the per-move cost O(candidates),
    not O(candidates x depth).

    SOUNDNESS BOUND (the load-bearing invariant). Trap-mode NEVER
    introduces a move outside the candidate list Maia already returned,
    and NEVER overrides Stockfish/Maia's implicit soundness filtering on
    that list. The mechanism is the same weighted-sample re-weighting
    used by every other signal -- trap-mode can only up-weight a trap-
    triggering candidate that Maia's own policy already considered
    plausible enough to return in the top-N. It cannot fabricate a
    move, and it cannot force selection of a candidate with near-zero
    base weight into certainty (same floor/ceiling discipline as the
    other multipliers -- see TRAP_WEIGHT below).

    This is what keeps trap-mode inside the existing safety envelope: it
    is structurally incapable of "playing an objectively bad move on
    purpose" to manufacture a trap. It can only tilt a decision Maia
    was already willing to make.

    BRANCH DECISION: PER-MOVE, NOT PER-GAME. At the top of
    rerank_candidates, after the existing setup/castle pre-computation,
    compute:

      trap_candidates = [i for i, c in enumerate(candidates)
                         if _is_trap_triggering(board, c["move"],
                                                 exploitable_keys)]
      trap_mode_active = bool(trap_candidates)

    where exploitable_keys is the set of position_keys that passed both
    gates above for this opponent (computed once per sparring session
    by compute_exploitable_traps in opponent_traps.py, passed in by the
    caller -- NOT recomputed per-candidate).

    - If trap_mode_active: this move is in trap-mode. Compute trap_mult
      per candidate (large boost for trap-triggering candidates, 1.0 for
      all others -- boost-only, same shape as setup). The existing sac /
      qt / setup / castle multipliers are STILL computed and STILL
      apply, multiplicatively, exactly as today. Trap-mode is "trap
      signal added to the stack," not "trap signal replaces the stack."
    - If not trap_mode_active: this move is in mirror-mode -- i.e.
      today's reranker, unchanged. No branch-specific code path; this is
      just the existing function body with trap_mult = 1.0 for every
      candidate (a no-op term in the product, not a separate code path).

    Because this is evaluated fresh every call to rerank_candidates
    (i.e. every move), a single sparring game naturally flips between
    trap-mode and mirror-mode move to move, with NO RESIDUAL STATE
    carried between moves. There is no "trap-mode session flag" anywhere
    -- statelessness here is a feature, not a gap: it's what makes the
    mixed-mode test case (Test 25) a correctness requirement, not an
    edge case to tolerate.

    DATA-FLOOR INTERACTION WITH THE EXISTING sufficient GATE. Trap-mode
    sits INSIDE the existing style["sufficient"] contract, unchanged. If
    style["sufficient"] is False, the function returns via the existing
    insufficient_data regression path exactly as today -- trap-mode is
    never evaluated, exploitable_keys is never consulted, full stop.
    The TRAP_MIN_GAMES / TRAP_MIN_HITS floor in opponent_traps.py is a
    SECOND, NARROWER GATE that only matters once the broader sufficient-
    data gate has already passed -- it doesn't replace or duplicate that
    gate, it adds a trap-specific bar on top of it.

    DERIVATION:
      trap_indicator_i = 1 if candidate i's resulting position_key is
                          in exploitable_keys, else 0
      trap_mult_i       = 1 + TRAP_WEIGHT * trap_indicator_i
      weight_i          = base_i * sac_mult_i * qt_mult_i * setup_mult_i
                           * castle_mult_i * trap_mult_i

    Boost-only (never below 1.0), matching setup's shape rather than sac/
    qt/castle's symmetric-boost-or-suppress shape -- there is no
    "opponent avoids this trap" signal to suppress toward; the absence
    of a trap match is simply neutral (1.0), not evidence of anything.

    DATA PLUMBING. rerank_candidates takes a new keyword-only argument
    `exploitable_trap_keys: Optional[set] = None`. None (the default)
    means "no trap data available" -- trap_mode_active is always False
    in that case, i.e. omitting the argument reproduces today's
    mirror-only behavior exactly. This keeps the function backward-
    compatible for any caller that hasn't been updated yet.

    TRANSPARENCY FIELDS (extending the existing return shape, same
    pattern as castle_preference_side / castle_preference_strength):
      * trap_mode_active: bool -- whether trap-mode fired on this
        specific move.
      * trap_candidate_count: int -- how many candidates in the input
        list were trap-triggering (0 when trap_mode_active is False).
      * Per-row breakdown: trap_indicator (bool) and trap_multiplier
        (float), same shape as the other per-row fields.
      * "trap" added to signals_applied when the trap multiplier
        actually deviates from 1.0 on at least one candidate (same
        actually-deviated-not-just-triggered discipline already used for
        sac/qt/setup/castle).

    EXPLICITLY OUT OF SCOPE: multi-ply trap-seeking / planning toward a
    trap 2+ moves away; fuzzy/family-based trap matching; a "trap-
    avoidance" mirror mode (steering AWAY from positions where the
    opponent plays well); any UI surfacing of trap_mode_active (the
    existing Opponent Prep page's "Traps He's Fallen For" section stays
    as-is; this spec only touches move-selection, not display).

(7) AVERAGE-GAME-LENGTH CALIBRATION (multiplicative with all existing
    signals; gated by the existing sufficient-data contract). The
    opponent's `average_game_length` (recency-weighted mean plies per
    game, already computed by compute_opponent_style) is converted into
    a gentle per-candidate multiplier that tilts the bot's candidate
    selection toward the tempo profile of the opponent's games.

    INTUITION. Opponents whose games tend to be SHORT (low plies per
    game) play sharper, more tactical chess -- their games end early in
    blunders or time forfeits, and a sparring bot mimicking them should
    lean toward forcing/tactical candidates. Opponents whose games tend
    to be LONG (high plies per game) play quieter, more positional chess
    -- their games grind into endgames, and the bot should lean toward
    solid/quiet candidates. This is the weakest signal in the stack
    (GAME_LENGTH_BIAS_STRENGTH = 0.8, deliberately below STYLE_BIAS_
    STRENGTH = 4.0, QUEEN_TRADE_BIAS_STRENGTH = 1.5, CASTLE_BIAS_
    STRENGTH = 1.5, SETUP_SIGNATURE_BIAS_STRENGTH = 2.5, and
    TRAP_WEIGHT = 6.0) because game length is a noisy proxy for style:
    the mean smooths over individual-game variance (a short time-forfeit
    doesn't mean the opponent plays tactically), so the calibration is
    soft -- this is a calibration signal, not a dominant one.

    PER-CANDIDATE INDICATOR: _is_forcing_move (see below). A candidate is
    "forcing" iff it is a capture OR gives check -- the two move
    categories in chess that constrain the opponent's response. This is
    a BROADER and CHEAPER proxy for "tactical/aggressive" than
    _is_live_sac_move (which requires a net material loss >=
    SAC_MATERIAL_THRESHOLD AND a profitable recapture available); a
    forcing move here includes even trades, non-sac captures, and checks
    that don't win material. The two indicators are intentionally
    different: sac frequency profiles the opponent's tendency to give up
    material; game length profiles the opponent's overall tempo. A player
    who sacrifices often but plays long games (e.g. a romantic attacker
    whose attacks are repulsed) gets the sac boost on sac-looking moves
    AND the length suppress on forcing moves -- the two compose
    multiplicatively, each preserving its own axis.

    DERIVATION:
      game_length  = style["average_game_length"]  (weighted mean plies)
      centered     = clamp((GAME_LENGTH_REFERENCE_PLY - game_length)
                           / GAME_LENGTH_SCALE_PLY, -1.0, 1.0)
      is_forcing_i = _is_forcing_move(board, candidate_i)   (0 or 1)
      length_mult_i = clamp(1 + GAME_LENGTH_BIAS_STRENGTH
                            * centered * is_forcing_i,
                           min=GAME_LENGTH_WEIGHT_FLOOR)

    Centering: GAME_LENGTH_REFERENCE_PLY = 50.0 is the neutral midpoint
    (opponents at this length get length_mult=1.0 for every candidate).
    50 plies (~25 fullmoves) is the typical Chess.com blitz/rapid game
    length in the dev DB's provider mix. GAME_LENGTH_SCALE_PLY = 20.0 is
    the half-width of the linear ramp: an opponent 20 plies from the
    reference reaches the full +/-1.0 centered value, and opponents
    further than 20 plies away are clamped. 20 plies = 10 fullmoves, a
    meaningful difference in game length (the difference between a
    15-move blitz and a 25-move rapid).

    BOOST-OR-SUPPRESS SHAPE (not boost-only). Short-game opponents
    (centered > 0) get length_mult > 1.0 on forcing candidates (boost);
    long-game opponents (centered < 0) get length_mult < 1.0 on forcing
    candidates (suppress); quiet candidates always get length_mult = 1.0
    (the bias only acts on the forcing indicator, like qt only acts on
    queen-trade moves). This matches the qt/castle symmetric shape rather
    than the setup/trap boost-only shape, because game length has a
    natural DIRECTION (shorter = more tactical) -- the absence of a
    forcing candidate is neutral, not evidence of "long-game style".

    GATING: the bias only fires when ALL of:
      * style["sufficient"] is True (the outer gate -- insufficient data
        short-circuits before length is evaluated, same as every other
        signal).
      * style["average_game_length"] is not None (defensive --
        compute_opponent_style always returns a float when sufficient=True,
        but a hand-constructed style dict might omit it; None means "no
        length data" -> centered=0.0 -> no-op, matching the
        unknown-signal-is-neutral pattern used by queens_stay_on_rate).

    TRANSPARENCY FIELDS (extending the existing return shape, same
    pattern as castle_preference_side / trap_mode_active):
      * average_game_length: float | None -- surfaced from style for
        transparency; the raw input to the calibration.
      * length_centered: float -- the centered value in [-1, 1] used to
        compute the per-candidate multiplier; 0.0 means "no length bias".
      * Per-row: length_indicator (bool) and length_multiplier (float),
        same shape as the other per-row fields.
      * "game_length" added to signals_applied when the length multiplier
        actually deviates from 1.0 on at least one candidate (same
        actually-deviated-not-just-triggered discipline already used for
        sac/qt/setup/castle/trap).

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
# where S in [0, 1] is the max RECENCY-WEIGHTED Jaccard composite across
# all historic snapshots (each snapshot's Jaccard is multiplied by its
# per-game recency weight snap["weight"], tagged by
# compute_opponent_style). With B = 2.5, sig_mult in [1.0, 3.5]:
# boost-only (no suppression of non-matching candidates), see the spec's
# 7.1. Default 2.5 keeps Maia's policy as the default and tilts toward
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

# --- castle-side preference (v1) -------------------------------------------
#
# Per-candidate multiplicative bias for moves that help / hurt the
# opponent's preferred castling side. The signal comes from
# `style["castling_side_distribution"]` (produced by compute_opponent_style
# as a {kingside, queenside, never} fraction over the opponent's games).
# castling_side_distribution was already computed in v1 but never consumed
# by the reranker; this wires it in.
#
# Per-candidate indicator (see _castle_indicator):
#   +1  -> move helps preferred castle side (clears a path square, or is
#          the castle move itself on the preferred side)
#   -1  -> move hurts preferred castle side (king move losing rights,
#          preferred-side rook move, piece moves INTO preferred-side path,
#          OR the castle move on the non-preferred side)
#   0   -> neutral (pawn moves, moves on the other side of the board,
#          non-preferred-side rook moves, pieces clearing the non-preferred
#          path)
#
# castle_mult = clamp(1 + CASTLE_BIAS_STRENGTH * pref_strength * indicator,
#                     min=CASTLE_WEIGHT_FLOOR)
#
# CASTLE_BIAS_STRENGTH = 1.5: same magnitude as QUEEN_TRADE_BIAS_STRENGTH
# since castle-side is a similarly binary stylistic preference (kingside
# vs queenside, like trade-queens vs keep-queens). At strength=1.5:
#   pref_strength=0.3 (just above threshold), indicator=+1 -> mult = 1.45
#   pref_strength=0.5 (clear preference),    indicator=+1 -> mult = 1.75
#   pref_strength=0.8 (strong preference),   indicator=+1 -> mult = 2.20
#   pref_strength=0.8,                        indicator=-1 -> clamped to 0.05
# A "wrong-side castle" move gets the strongest penalty (indicator=-1 with
# the highest typical pref_strength) -- this is the clearest "bot is not
# mimicking the opponent" signal.
#
# CASTLE_PREFERENCE_THRESHOLD = 0.3: minimum |kingside_frac - queenside_frac|
# to apply the bias. Below this the opponent's preference is too weak to
# be worth tilting Maia's distribution. The threshold also implicitly
# handles the "never-dominant" case (where the difference is small because
# both castle fractions are small).
#
# CASTLE_BIAS_PLY_MAX = 40: castling decisions are typically settled by
# move 20 (ply 40). Past this point the bot has either castled already
# or committed to not castling, and the bias becomes noise. The outer
# gate also short-circuits when the bot has lost castling rights on the
# preferred side (e.g. the king has already moved), so the ply cap is a
# safety net for the rare "king still on original square at move 30 with
# both rights intact but midgame is well underway" case.
#
# CASTLE_WEIGHT_FLOOR = 0.05: defensive floor matching QUEEN_TRADE_WEIGHT_FLOOR
# so random.choices never sees a non-positive weight.
CASTLE_BIAS_STRENGTH = 1.5
CASTLE_PREFERENCE_THRESHOLD = 0.3
CASTLE_BIAS_PLY_MAX = 40
CASTLE_WEIGHT_FLOOR = 0.05

# --- trap-mode / mirror-mode branch (v1) -----------------------------------
#
# Per-candidate multiplicative boost for moves whose resulting position_key
# (first 4 FEN fields, same convention as opponent_repertoire._position_key
# and opponent_game_analysis._position_key_from_fen) matches an entry in
# `exploitable_trap_keys` -- the set of position_keys the opponent has
# blundered in >= TRAP_MIN_HITS times across >= TRAP_MIN_GAMES total games
# (gates applied by compute_exploitable_traps in opponent_traps.py).
#
# See the module docstring's decision (6) for the full branch spec
# (product decision, reachability, soundness bound, branch decision,
# data-floor interaction, derivation, transparency fields).
#
# TRAP_WEIGHT is picked so a trap-triggering candidate reliably dominates
# the sample when it fires, since drilling a *specific, already-proven*
# weakness is a stronger and more specific signal than any of the
# style-aggregate signals (sac frequency, queen-trade timing) -- this
# opponent has concretely blundered here before, more than once. At
# TRAP_WEIGHT = 6.0, notably higher than STYLE_BIAS_STRENGTH = 4.0 and
# QUEEN_TRADE_BIAS_STRENGTH = 1.5 / CASTLE_BIAS_STRENGTH = 1.5, on the
# reasoning that trap evidence is categorically stronger (a confirmed
# >=2x repeated failure) than a continuous style-aggregate.
#
# Calibration example (rank-decay base, no policy field, sac/qt/setup/
# castle all dormant so trap is the only signal):
#   rank 1 (quiet, no trap): base 1.0, trap_mult 1.0  -> weight 1.0
#   rank 3 (trap-triggering): base 0.25, trap_mult 7.0 -> weight 1.75
#   -> rank 3 share = 1.75 / (1.0 + ... + 1.75 + ...) dominant but not
#   deterministic. At a trap-triggering candidate sitting at rank 3
#   against a rank-1 quiet candidate, trap_mult = 1 + 6.0*1 = 7.0 gives
#   the trap candidate an effective weight of 1.75 vs the rank-1's 1.0
#   -- dominant but not deterministic (matches the "re-weighted sample,
#   not straight re-rank" philosophy already established in decision (3):
#   even a strongly-favored trap candidate shouldn't be played with 100%
#   certainty, or the bot stops looking human and starts looking like a
#   scripted trap-bot).
#
# Boost-only (never below 1.0), matching setup's shape rather than sac/
# qt/castle's symmetric-boost-or-suppress shape -- there is no "opponent
# avoids this trap" signal to suppress toward; the absence of a trap
# match is simply neutral (1.0), not evidence of anything.
#
# Tune this empirically against a head-to-head test the same way
# STYLE_BIAS_STRENGTH was calibrated -- this starting value is a reasoned
# default, not a measured one.
TRAP_WEIGHT = 6.0

# --- average-game-length calibration (v1) -----------------------------------
#
# Per-candidate multiplicative bias that tilts the bot's candidate
# selection toward the tempo profile of the opponent's games. The signal
# comes from `style["average_game_length"]` (already computed by
# compute_opponent_style as the recency-weighted mean plies per game).
#
# See the module docstring's decision (7) for the full spec (intuition,
# per-candidate indicator, derivation, centering, boost-or-suppress
# shape, gating, transparency fields).
#
# GAME_LENGTH_BIAS_STRENGTH = 0.8: deliberately the WEAKEST signal in
# the stack (below STYLE_BIAS_STRENGTH=4.0, QUEEN_TRADE_BIAS_STRENGTH=1.5,
# CASTLE_BIAS_STRENGTH=1.5, SETUP_SIGNATURE_BIAS_STRENGTH=2.5, and
# TRAP_WEIGHT=6.0). Game length is a noisy proxy for style: the mean
# smooths over individual-game variance (a short time-forfeit doesn't
# mean the opponent plays tactically), so the calibration is soft. At
# strength=0.8:
#   centered=+1.0 (short games), is_forcing=1 -> mult = 1.8  (max boost)
#   centered= 0.0 (reference)  , is_forcing=1 -> mult = 1.0  (no effect)
#   centered=-1.0 (long games) , is_forcing=1 -> mult = 0.2  (suppress)
#   centered=anything          , is_forcing=0 -> mult = 1.0  (quiet neutral)
# The suppress direction (0.2) is above the 0.05 floor, so the floor
# never triggers at this strength -- it's kept for defensive consistency
# with the other signals' floors.
#
# GAME_LENGTH_REFERENCE_PLY = 50.0: the neutral midpoint. Opponents at
# this weighted-mean length get length_mult=1.0 for every candidate.
# 50 plies (~25 fullmoves) is the typical Chess.com blitz/rapid game
# length in the dev DB's provider mix. Revisit when the dev DB has a
# meaningful classical-games population (which would shift the reference
# upward toward 60-70 plies).
#
# GAME_LENGTH_SCALE_PLY = 20.0: the half-width of the linear centering
# ramp. An opponent 20 plies from the reference reaches the full +/-1.0
# centered value; opponents further away are clamped. 20 plies = 10
# fullmoves, a meaningful game-length difference.
#
# GAME_LENGTH_WEIGHT_FLOOR = 0.05: defensive floor matching the other
# signals' floors so random.choices never sees a non-positive weight.
GAME_LENGTH_BIAS_STRENGTH = 0.8
GAME_LENGTH_REFERENCE_PLY = 50.0
GAME_LENGTH_SCALE_PLY = 20.0
GAME_LENGTH_WEIGHT_FLOOR = 0.05


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
      * sig_mult  -- 1 + SETUP_SIGNATURE_BIAS_STRENGTH * max_weighted_S, in
                     [1.0, 1 + SETUP_SIGNATURE_BIAS_STRENGTH]. Boost-only
                     (never below 1.0). When `hist_signatures` is None or
                     empty, returns (1.0, None, None) -- the feature is a
                     no-op for this candidate (no historic evidence).
      * best_S    -- the max RECENCY-WEIGHTED Jaccard composite across all
                     historic snapshots (None iff no signatures were
                     available). Each snapshot's Jaccard is multiplied by
                     its per-game recency weight (snap["weight"], default
                     1.0 for backward compat), so recent setups dominate
                     the max and old setups act as a soft prior.
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
        # "piece_squares": {"N": [...], ...}, "snapshot_ply": int,
        # "weight": float (optional, default 1.0)}.
        # Frozen once for cheap intersection.
        hist_pawn = frozenset(snap.get("pawn_squares") or ())
        hist_piece_list = snap.get("piece_squares") or {}
        hist_piece = frozenset(
            sq
            for letter in ("N", "B", "R", "Q", "K")
            for sq in (hist_piece_list.get(letter) or ())
        )
        s = _setup_similarity(cand_pawn, cand_piece, hist_pawn, hist_piece)
        # Recency weighting: multiply the Jaccard similarity by the
        # snapshot's per-game recency weight (tagged by
        # compute_opponent_style as snap["weight"]). Recent setups
        # dominate the max; old setups act as a soft prior (their S is
        # scaled down, so a perfect old match scores lower than a good
        # recent match). Default weight=1.0 for backward compat with
        # test fixtures that don't tag a weight.
        snap_weight = float(snap.get("weight") or 1.0)
        weighted_s = s * snap_weight
        if weighted_s > best_s or (weighted_s == best_s and best_ply is None):
            # Strict-greater keeps the FIRST max (deterministic for ties);
            # the second clause picks any ply when best_s is still 0.0 so
            # a 0-similarity match still reports a ply (rare; only if
            # every signature shares nothing with the candidate).
            best_s = weighted_s
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
    the WHOLE snapshot pool, which for the user (500 games, 7 Scandi)
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


def _is_trap_triggering(
    board: chess.Board,
    candidate_uci: str,
    exploitable_keys: Optional[set],
) -> bool:
    """Return True iff pushing `candidate_uci` produces a position whose
    `position_key` (first 4 FEN fields) is in `exploitable_keys`.

    This is the per-candidate one-ply reachability check for trap-mode
    (decision (6)). It has exactly the same shape as `_is_live_sac_move`,
    `_is_queen_trade_move`, and `_candidate_setup_mult`: push the
    candidate onto a board copy, inspect the resulting board, and
    classify. See the module docstring's decision (6) "REACHABILITY:
    STRICTLY ONE-PLY, NO LOOKAHEAD" paragraph for why this is a
    deliberate scope limit (trap-mode is opportunistic, not strategic;
    no multi-ply search toward a trap 2+ moves away).

    `exploitable_keys` is the set of `position_key` strings that passed
    both exploitability gates (TRAP_MIN_HITS and TRAP_MIN_GAMES) for this
    opponent, computed once per sparring session by
    `compute_exploitable_traps` in `opponent_traps.py` and passed in by
    the caller. None or an empty set means "no trap data available" ->
    always returns False -> `trap_mode_active` is False -> the reranker
    stays in mirror-mode (today's behavior, unchanged). This is the
    backward-compatibility path for callers that haven't been updated to
    pass the new argument.

    The `position_key` convention matches `opponent_repertoire._position_key`
    and `opponent_game_analysis._position_key_from_fen` exactly: the first
    4 FEN fields (piece placement + side to move + castling rights + en
    passant square). The blunder table records `position_key` from
    `fen_before` (the position BEFORE the opponent's blunder move), so
    the side-to-move in the stored key is the OPPONENT's. When the bot
    pushes its candidate, the resulting board has the OPPONENT's turn --
    so the resulting `position_key` is directly comparable to the stored
    blunder keys: a match means the bot's candidate leads to exactly the
    position the opponent previously blundered in.

    Defensive on every edge: non-legal candidate, unparseable UCI, or a
    push failure all return False (no trap signal rather than a crash --
    matches the other per-candidate helpers' defensive convention).
    """
    if not exploitable_keys:
        # None or empty set -> no trap data -> mirror-mode (no-op).
        return False
    try:
        candidate = chess.Move.from_uci(candidate_uci)
    except ValueError:
        return False
    if candidate not in board.legal_moves:
        # Maia's candidates are legal by construction; defensive guard.
        return False
    board_after = board.copy(stack=False)
    try:
        board_after.push(candidate)
    except (AssertionError, ValueError):
        # python-chess pushes should not fail here (the move is legal),
        # but if it does, treat as not-trap-triggering rather than crash.
        return False
    resulting_key = " ".join(board_after.fen().split()[:4])
    return resulting_key in exploitable_keys


def _is_forcing_move(board: chess.Board, candidate_uci: str) -> bool:
    """A candidate is 'forcing' iff it is a capture OR gives check.

    Captures and checks are the two 'forcing' move categories in chess
    (the opponent's response is constrained: they must recapture or
    address the check). This is a BROADER and CHEAPER proxy for
    'tactical/aggressive' than `_is_live_sac_move` (which requires a net
    material loss >= SAC_MATERIAL_THRESHOLD AND a profitable recapture
    available); a forcing move here includes even trades, non-sac
    captures, and checks that don't win material. The two indicators are
    intentionally different -- see the module docstring's decision (7)
    for why they profile independent stylistic axes (sac frequency
    profiles material aggression; game length profiles overall tempo).

    Used by the game-length calibration bias as the per-candidate
    indicator: short-game opponents boost forcing candidates, long-game
    opponents suppress them. Has the same one-ply-forward shape as
    `_is_live_sac_move`, `_is_queen_trade_move`, `_is_trap_triggering`,
    and `_candidate_setup_mult`: inspect the board after pushing the
    candidate onto a copy, classify, return.

    Defensive on every edge (non-legal candidate, unparseable UCI, push
    failure) -> returns False, matching the other per-candidate helpers'
    convention.
    """
    try:
        candidate = chess.Move.from_uci(candidate_uci)
    except ValueError:
        return False
    if candidate not in board.legal_moves:
        # Maia's candidates are legal by construction; defensive guard.
        return False
    # Capture (handles all capture types including en-passant via
    # board.is_capture, which checks piece_at(to_square) for normal
    # captures and the en-passant target for e.p. captures).
    if board.is_capture(candidate):
        return True
    # Check: push the candidate on a copy, see if the opponent (the
    # side to move after our candidate) is in check. board.is_check()
    # returns True iff the side to move is in check, which after our
    # push is the opponent -- exactly "did our move give check".
    board_after = board.copy(stack=False)
    try:
        board_after.push(candidate)
    except (AssertionError, ValueError):
        return False
    return board_after.is_check()


# --- castle-side preference helpers ----------------------------------------
#
# Three helpers that feed the castle_mult bias term in rerank_candidates.
# _castle_preference extracts the preferred side + strength from the style
# dict; _castle_path_squares is a static lookup; _castle_indicator classifies
# a candidate move's effect on the preferred castle side.


def _castle_path_squares(color: chess.Color, side: str) -> set:
    """Squares between king and rook that must be empty for castling.

    For kingside (short castle): the two squares between king and h-rook.
    For queenside (long castle): the three squares between king and a-rook.
    The king's and rook's own squares are NOT included -- only the path
    the king passes through (and the rook passes through for queenside).
    """
    if color == chess.WHITE:
        if side == "kingside":
            return {chess.F1, chess.G1}
        return {chess.B1, chess.C1, chess.D1}  # queenside
    # BLACK
    if side == "kingside":
        return {chess.F8, chess.G8}
    return {chess.B8, chess.C8, chess.D8}  # queenside


def _castle_preference(castling_dist: Optional[Dict[str, Any]]) -> tuple:
    """Extract (preferred_side, strength) from castling_side_distribution.

    Returns (pref_side, pref_strength) where:
      * pref_side is "kingside" | "queenside" | None
      * pref_strength is |kingside_frac - queenside_frac| in [0, 1], or 0.0

    None is returned when:
      * castling_dist is empty/None
      * "never" is the largest fraction (opponent doesn't castle enough
        to extract a side preference -- the signal is too noisy)
      * kingside and queenside fractions are exactly tied

    The "never-dominant" guard is important: an opponent with
    {"never": 0.7, "kingside": 0.2, "queenside": 0.1} has a 0.1
    kingside-queenside gap, which would technically clear a low threshold
    -- but the dominant "never" signal means the opponent mostly doesn't
    castle at all, so steering the bot toward kingside would be reading
    a signal that isn't there. The guard returns None instead.
    """
    if not castling_dist:
        return None, 0.0

    try:
        kingside = float(castling_dist.get("kingside", 0.0) or 0.0)
        queenside = float(castling_dist.get("queenside", 0.0) or 0.0)
        never = float(castling_dist.get("never", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None, 0.0

    # If "never" dominates, the signal is too noisy to use -- the opponent
    # doesn't castle enough for a side preference to be meaningful.
    if never >= kingside and never >= queenside:
        return None, 0.0

    if kingside > queenside:
        return "kingside", kingside - queenside
    if queenside > kingside:
        return "queenside", queenside - kingside
    return None, 0.0  # exact tie -- no preference


def _castle_indicator(
    board: chess.Board,
    candidate_uci: str,
    pref_side: str,
) -> int:
    """Classify a candidate move's effect on the preferred castle side.

    Returns +1, -1, or 0:
      +1  -> move helps preferred castle side:
             * the castle move itself on the preferred side (O-O when
               pref=kingside, O-O-O when pref=queenside)
             * a piece move that clears a preferred-side path square
               (e.g. Ng1-f3 when pref=kingside; Bf1-c4 when pref=kingside;
               Nb1-c3 when pref=queenside; Bc1-d2 when pref=queenside)
      -1  -> move hurts preferred castle side:
             * the castle move on the NON-preferred side (O-O when
               pref=queenside; O-O-O when pref=kingside) -- the clearest
               "bot is not mimicking the opponent" signal, since it's a
               binary irreversible commitment to the opposite pattern
             * any non-castle king move (loses all castling rights)
             * the preferred-side rook moving (loses preferred-side rights;
               identified by the from_square's file: h-file for kingside,
               a-file for queenside)
             * a piece move TO a preferred-side path square (blocks the
               king's path)
      0   -> neutral:
             * pawn moves (pawns don't block the castling path -- the
               king and rook pass through rank 1, pawns are on rank 2+)
             * non-preferred-side rook moves (don't affect preferred-side
               rights)
             * pieces clearing the non-preferred path (doesn't help the
               preferred side, doesn't hurt either)
             * moves on the other side of the board

    Defensive: returns 0 on any edge case (non-legal candidate, unparseable
    UCI, missing piece). The outer gate (castle_window_active in
    rerank_candidates) already ensures the bot has castling rights on the
    preferred side before this function is called, so the rook-file check
    is safe (a rook on the h-file with kingside rights intact is the
    starting h-rook; a rook that's moved would have already lost rights).
    """
    try:
        candidate = chess.Move.from_uci(candidate_uci)
    except ValueError:
        return 0
    if candidate not in board.legal_moves:
        return 0

    # The castle move itself.
    if board.is_kingside_castling(candidate):
        return +1 if pref_side == "kingside" else -1
    if board.is_queenside_castling(candidate):
        return +1 if pref_side == "queenside" else -1

    piece = board.piece_at(candidate.from_square)
    if piece is None:
        return 0

    # Non-castle king move -- loses all castling rights, including the
    # preferred side.
    if piece.piece_type == chess.KING:
        return -1

    # Rook move -- loses castling rights on the rook's side. Identify which
    # side by the from_square's file: h-file (file 7) = kingside rook,
    # a-file (file 0) = queenside rook. A rook on any other file has
    # already moved (and would have lost rights), so the outer gate would
    # have short-circuited -- but defensively return 0 for those.
    if piece.piece_type == chess.ROOK:
        from_file = chess.square_file(candidate.from_square)
        if from_file == 7:  # h-file -> kingside rook
            return -1 if pref_side == "kingside" else 0
        if from_file == 0:  # a-file -> queenside rook
            return -1 if pref_side == "queenside" else 0
        return 0

    # Other piece moves -- check if it clears or blocks a preferred-side
    # path square. A piece moving FROM a path square clears it (+1); a
    # piece moving TO a path square blocks it (-1).
    pref_path = _castle_path_squares(board.turn, pref_side)
    if candidate.from_square in pref_path:
        return +1
    if candidate.to_square in pref_path:
        return -1
    return 0


def rerank_candidates(
    *,
    candidates: List[Dict[str, Any]],
    style: Dict[str, Any],
    board: chess.Board,
    rng: Optional[random.Random] = None,
    exploitable_trap_keys: Optional[set] = None,
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
            "queen_trade_move_number" (queen-trade multiplier),
            "castling_side_distribution" (castle-side multiplier).
        board: The chess.Board the candidates are FOR (the same board
            best_move_candidates was called with). Used only to push
            candidate copies for live-sac detection and to inspect the
            destination square for queen-trade detection -- never
            mutated.
        rng: Optional pre-seeded random.Random instance; if None, the
            module-level `random` is used (matches pick_repertoire_move's
            pattern). Tests should pass a seeded Random for
            reproducibility; production should pass None.
        exploitable_trap_keys: Optional set of `position_key` strings
            (first 4 FEN fields) the opponent has blundered in >= 2
            distinct games across >= 5 total imported games (gates
            applied by `compute_exploitable_traps` in opponent_traps.py).
            Computed ONCE per sparring session by the caller, NOT
            recomputed per move. When non-empty AND at least one
            candidate's resulting position_key is in the set, trap-mode
            fires: the matching candidate(s) get a boost-only
            `trap_mult = 1 + TRAP_WEIGHT` multiplied into the weight
            product (on top of sac/qt/setup/castle, NOT replacing them).
            None (the default) or an empty set means "no trap data
            available" -- trap_mode_active is always False and the
            reranker stays in mirror-mode (today's behavior, unchanged).
            See decision (6) in the module docstring for the full spec.

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
                                                             setup_S,
                                                             setup_multiplier,
                                                             setup_matched_ply,
                                                             castle_indicator,
                                                             castle_multiplier,
                                                              trap_indicator,
                                                              trap_multiplier,
                                                              length_indicator,
                                                              length_multiplier,
                                                              bias_multiplier,
                                                              weight}, ... ],
                                               "family_lean": <sentinel>,
                                               "signals_applied":
                                                 ["sacrifice",
                                                  "queen_trade"]  # subset
                                             }
            trap_mode_active: bool       -- whether trap-mode fired on
                                           this specific move (True iff
                                           exploitable_trap_keys was
                                           non-empty AND >=1 candidate's
                                           resulting position_key was in
                                           it). False when no trap data
                                           was passed OR no candidate
                                           matched. See decision (6).
            trap_candidate_count: int    -- how many candidates in the
                                           input list were trap-
                                           triggering (0 when
                                           trap_mode_active is False).
            average_game_length: float|None -- surfaced from style for
                                               transparency; the raw
                                               input to the game-length
                                               calibration (decision 7).
            length_centered: float         -- the centered value in
                                              [-1, 1] used to compute
                                              the per-candidate length
                                              multiplier; 0.0 means
                                              "no length bias" (None or
                                              reference-length
                                              average_game_length).
            game_count: int             -- surfaced from style, raw
                                           game count, for transparency

    Contracts:
      * Insufficient data (style["sufficient"]=False): returns
        candidates[0] deterministically, applied_bias=False,
        source="insufficient_data" -- the regression contract. Trap-mode
        is NEVER evaluated on this path (the outer sufficient gate
        short-circuits before exploitable_trap_keys is consulted), so
        trap_mode_active=False and trap_candidate_count=0 even when
        exploitable_trap_keys is non-empty. See decision (6)'s
        "DATA-FLOOR INTERACTION" paragraph.
      * Sufficient data but no candidate's per-signal multiplier
        actually deviates from 1.0 (no sac candidates, or sac_freq=0;
        no queen-trade candidates, OR all queen-trade candidates have
        window_weight=0 / centered=0; no setup match; castle dormant;
        AND no trap-triggering candidate OR exploitable_trap_keys is
        None/empty; AND length_centered=0 OR no forcing candidates):
        returns candidates[0] deterministically,
        applied_bias=False, source="default_top_candidate". Same
        behavior as v1. "Indicator fired" is not enough -- the
        calibration has to actually move the multiplier off 1.0 for the
        bias to matter.
      * Sufficient data and >=1 candidate's per-signal multiplier
        actually deviates from 1.0: weighted random sample using
            weight[i] = base(rank_i) * sac_mult_i * qt_mult_i
                              * setup_mult_i * castle_mult_i * trap_mult_i
                              * length_mult_i
        where:
            sac_mult_i = 1 + STYLE_BIAS_STRENGTH * sac_freq * sac_i
            qt_mult_i  = max(QUEEN_TRADE_WEIGHT_FLOOR,
                             1 + QUEEN_TRADE_BIAS_STRENGTH *
                                 centered * window_w * qt_i)
            setup_mult_i = 1 + SETUP_SIGNATURE_BIAS_STRENGTH * max_S_i
            castle_mult_i = max(CASTLE_WEIGHT_FLOOR,
                                1 + CASTLE_BIAS_STRENGTH *
                                    castle_pref_strength * castle_ind_i)
            trap_mult_i  = 1 + TRAP_WEIGHT * trap_indicator_i
            length_mult_i = max(GAME_LENGTH_WEIGHT_FLOOR,
                                1 + GAME_LENGTH_BIAS_STRENGTH *
                                    length_centered * is_forcing_i)
        applied_bias=True, source="style_biased", bias_breakdown
        populated (including the list of signals whose multiplier
        actually deviated from 1.0 on at least one candidate).

    Idempotency / purity:
      * Does not mutate `board` (uses board.copy(stack=False)).
      * Does not mutate `candidates`.
      * Reads `style` but does not mutate it.
      * Reads `exploitable_trap_keys` but does not mutate it. Does not
        hold any module-level mutable state related to trap-mode across
        calls -- trap_mode_active is recomputed fresh every call, so a
        single sparring game naturally flips between trap-mode and
        mirror-mode move to move with no residual state (see Test 25).
    """
    # Compute castle preference once at the top so all return paths
    # (including the early-return regression paths below) can surface
    # the same derived transparency fields. Cheap: one dict lookup +
    # float arithmetic; no board access.
    castle_pref_side, castle_pref_strength = _castle_preference(
        style.get("castling_side_distribution")
    )

    # Compute game-length centering once at the top so all return paths
    # can surface the same derived transparency field. Same pattern as
    # castle_pref_side/strength above. See decision (7).
    average_game_length = style.get("average_game_length")
    if average_game_length is None:
        length_centered = 0.0
    else:
        try:
            gl = float(average_game_length)
            length_centered = max(
                -1.0,
                min(1.0, (GAME_LENGTH_REFERENCE_PLY - gl) / GAME_LENGTH_SCALE_PLY),
            )
        except (TypeError, ValueError):
            length_centered = 0.0

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
            "castle_preference_side": castle_pref_side,
            "castle_preference_strength": round(castle_pref_strength, 4),
            "trap_mode_active": False,
            "trap_candidate_count": 0,
            "average_game_length": average_game_length,
            "length_centered": round(length_centered, 4),
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
            "castle_preference_side": castle_pref_side,
            "castle_preference_strength": round(castle_pref_strength, 4),
            "trap_mode_active": False,
            "trap_candidate_count": 0,
            "average_game_length": average_game_length,
            "length_centered": round(length_centered, 4),
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

    # castle-side preference window gate. castle_pref_side and
    # castle_pref_strength are already computed at the top of the
    # function; here we add the board-dependent checks (ply cap +
    # castling rights on the preferred side) to decide whether the
    # bias is live for this position. See the module docstring's
    # decision (5) and _castle_indicator for the full rule.
    castle_window_active = False
    if castle_pref_side is not None:
        bot_color = board.turn
        has_pref_rights = (
            board.has_kingside_castling_rights(bot_color)
            if castle_pref_side == "kingside"
            else board.has_queenside_castling_rights(bot_color)
        )
        castle_window_active = (
            castle_pref_strength > CASTLE_PREFERENCE_THRESHOLD
            and current_ply <= CASTLE_BIAS_PLY_MAX
            and has_pref_rights
        )

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

    # --- trap-mode / mirror-mode branch (decision 6) -----------------------
    # Compute which candidates are trap-triggering BEFORE the per-candidate
    # loop, so (a) trap_mode_active can be surfaced in the per-row
    # breakdown and the top-level return, and (b) the candidate loop can
    # do an O(1) set-membership lookup instead of re-pushing each
    # candidate's resulting board. trap_mode_active is True iff at least
    # one candidate's resulting position_key is in exploitable_trap_keys.
    # When False (no trap data passed OR no candidate matches), every
    # trap_mult is 1.0 -- a no-op term in the weight product, NOT a
    # separate code path. This is the mirror-mode fallthrough: today's
    # reranker behavior, unchanged (see Test 24).
    #
    # Statelessness is a feature here: trap_mode_active is recomputed
    # fresh every call to rerank_candidates, so a single sparring game
    # naturally flips between trap-mode and mirror-mode move to move
    # with NO residual state carried between moves (see Test 25).
    trap_candidate_indices: List[int] = []
    if exploitable_trap_keys:
        for i, c in enumerate(candidates):
            if _is_trap_triggering(board, c.get("move", ""), exploitable_trap_keys):
                trap_candidate_indices.append(i)
    trap_mode_active = bool(trap_candidate_indices)
    # Set for O(1) "is this candidate trap-triggering?" lookup in the loop.
    trap_candidate_set = set(trap_candidate_indices)

    # --- compute per-candidate live indicators and weights ------------------
    # base_weight_i = candidate["policy"] when the patched UCI wrapper
    # is in use (the actual softmax probability of the candidate), or
    # the geometric rank-decay proxy when it isn't. Six independent
    # multiplicative bias terms compose on top:
    #   sac_mult_i    = 1 + STYLE_BIAS_STRENGTH * sac_freq * sac_indicator_i
    #   qt_mult_i     = clamp(1 + QUEEN_TRADE_BIAS_STRENGTH *
    #                           qt_centered * qt_window_w * qt_indicator_i,
    #                        min=QUEEN_TRADE_WEIGHT_FLOOR)
    #   setup_mult_i  = 1 + SETUP_SIGNATURE_BIAS_STRENGTH * max_similarity_i
    #   castle_mult_i = clamp(1 + CASTLE_BIAS_STRENGTH *
    #                           castle_pref_strength * castle_indicator_i,
    #                        min=CASTLE_WEIGHT_FLOOR)
    #   trap_mult_i    = 1 + TRAP_WEIGHT * trap_indicator_i  (boost-only)
    #   length_mult_i  = clamp(1 + GAME_LENGTH_BIAS_STRENGTH *
    #                           length_centered * is_forcing_i,
    #                          min=GAME_LENGTH_WEIGHT_FLOOR)
    # weight_i = base_i * sac_mult_i * qt_mult_i * setup_mult_i
    #                  * castle_mult_i * trap_mult_i * length_mult_i
    weights: List[float] = []
    sac_mults: List[float] = []
    qt_mults: List[float] = []
    setup_mults: List[float] = []
    castle_mults: List[float] = []
    trap_mults: List[float] = []
    length_mults: List[float] = []
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

        # Castle-side preference: +1 if this candidate helps the opponent's
        # preferred castle side, -1 if it hurts, 0 if neutral. Only
        # computed when the outer gate (castle_window_active) is True --
        # otherwise every candidate gets indicator=0 -> castle_mult=1.0.
        castle_indicator_val = 0
        if castle_window_active:
            castle_indicator_val = _castle_indicator(
                board, uci, castle_pref_side
            )
        castle_mult_raw = (
            1.0 + CASTLE_BIAS_STRENGTH
            * castle_pref_strength * castle_indicator_val
        )
        castle_mult = max(CASTLE_WEIGHT_FLOOR, castle_mult_raw)
        castle_mults.append(castle_mult)

        # Trap-mode: boost-only multiplier for candidates whose resulting
        # position_key is in exploitable_trap_keys. trap_indicator is 1
        # iff this candidate's index was pre-computed as trap-triggering
        # (see trap_candidate_set above). Boost-only (never below 1.0),
        # matching setup's shape -- see decision (6)'s derivation. The
        # other signals (sac/qt/setup/castle) are still computed and
        # still apply, multiplicatively; trap-mode is "trap signal added
        # to the stack," not "trap signal replaces the stack."
        is_trap = idx in trap_candidate_set
        trap_mult = 1.0 + TRAP_WEIGHT * (1.0 if is_trap else 0.0)
        trap_mults.append(trap_mult)

        # Game-length calibration (decision 7): per-candidate forcing
        # indicator (capture or check) boosted for short-game opponents,
        # suppressed for long-game opponents. The bias only acts on
        # forcing candidates; quiet candidates get length_mult=1.0 (the
        # bias is neutral on the absence of a forcing move, not evidence
        # of "long-game style"). When length_centered=0.0 (None or
        # reference-length average_game_length), length_mult_raw=1.0 for
        # every candidate regardless of is_forcing -- the signal is a
        # no-op. The indicator is still computed and surfaced in the
        # per-row breakdown for transparency (operators can see which
        # candidates are forcing even when the bias is dormant).
        is_forcing = _is_forcing_move(board, uci)
        length_mult_raw = (
            1.0 + GAME_LENGTH_BIAS_STRENGTH * length_centered
            * (1.0 if is_forcing else 0.0)
        )
        length_mult = max(GAME_LENGTH_WEIGHT_FLOOR, length_mult_raw)
        length_mults.append(length_mult)

        base, used_policy = _candidate_base_weight(candidate, idx + 1)
        if used_policy:
            any_used_policy = True
        bias_mult = (
            sac_mult * qt_mult * setup_mult * castle_mult
            * trap_mult * length_mult
        )
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
            "castle_indicator": castle_indicator_val,
            "castle_multiplier": round(castle_mult, 4),
            "trap_indicator": is_trap,
            "trap_multiplier": round(trap_mult, 4),
            "length_indicator": is_forcing,
            "length_multiplier": round(length_mult, 4),
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
    castle_actually_biased = _mult_deviated(castle_mults)
    trap_actually_biased = _mult_deviated(trap_mults)
    length_actually_biased = _mult_deviated(length_mults)
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
    if not (sac_actually_biased or qt_actually_biased or setup_actually_biased or castle_actually_biased or trap_actually_biased or length_actually_biased):
        # Sampling would still be a no-op: weights are all just
        # geometric rank-decay, and rank 1 has the largest weight. The
        # sampler MIGHT pick a non-top candidate (it's random), but
        # statistically the expected choice is rank 1 -- and "no bias
        # applied" is the honest source label. Return rank 1
        # deterministically: same behavior the insufficient path uses,
        # same behavior as today's default Maia, no random drift for
        # consumers that didn't opt into style biasing.
        #
        # Note: if trap_mode_active were True, trap_mult would be
        # 1 + TRAP_WEIGHT = 7.0 on the trap-triggering candidate, which
        # deviates from 1.0 -- so this no-bias path can only be reached
        # when trap_mode_active is False. We still surface trap_mode_active
        # and trap_candidate_count (both 0 here) for return-shape
        # consistency with the style_biased path.
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
            "castle_preference_side": castle_pref_side,
            "castle_preference_strength": round(castle_pref_strength, 4),
            "trap_mode_active": trap_mode_active,
            "trap_candidate_count": len(trap_candidate_indices),
            "average_game_length": average_game_length,
            "length_centered": round(length_centered, 4),
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
    if castle_actually_biased:
        signals_applied.append("castle")
    if trap_actually_biased:
        signals_applied.append("trap")
    if length_actually_biased:
        signals_applied.append("game_length")

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
        "castle_preference_side": castle_pref_side,
        "castle_preference_strength": round(castle_pref_strength, 4),
        "trap_mode_active": trap_mode_active,
        "trap_candidate_count": len(trap_candidate_indices),
        "average_game_length": average_game_length,
        "length_centered": round(length_centered, 4),
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