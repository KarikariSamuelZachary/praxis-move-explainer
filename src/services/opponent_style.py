"""
Opponent style-bias aggregation layer (v1).

This module computes aggregate, recency-weighted style signals from an
opponent's already-imported games (the `opponent_games` table, same source
the per-position repertoire sampler consumes). It is deliberately
decoupled from `opponent_repertoire.py`:

  * `opponent_repertoire.py` answers "what single move does this opponent
    tend to play *in this position*?" — a per-position move sampler.
  * `opponent_style.py` answers "what kind of player *is* this opponent,
    across their whole history?" — a per-opponent aggregate style profile,
    feeding the move re-ranker as a bias layer on top of Maia + repertoire.

They share a data source (opponent_games) and the same conceptual handling
of sparse data (a floor constant) and aging (an exponential recency decay),
but they are separate signals feeding separate layers. Keeping them in
separate modules keeps `train.py` from having to import repertoire plumbing
when only the style layer is wired in (or vice versa), and lets the two
layers be tuned on independent timescales in the future.

Scope of v1 (this file), per the original spec's priority order:

  (1) SACRIFICE FREQUENCY. A material-delta heuristic computed from PGN
      alone — no Stockfish eval, no search. See SAC_MATERIAL_THRESHOLD
      and SAC_RECOUP_PLIES below for the exact rule and its limitations.

  (2) OPENING FAMILY LEAN. Which opening family each game falls into, and
      the recency-weighted fraction of the opponent's games in each family.

  (3) AVERAGE GAME LENGTH. Recency-weighted mean of total plies per game
      (half-moves, the same unit python-chess's `mainline_moves()` gives).
      Computed by replaying each game's mainline; no PGN header is trusted
      (Chess.com PGNs in our dev DB carry no `[PlyCount]`, and Lichess's
      optional `[PlyCount]` is redundant with the mainline length anyway,
      so replay is the provider-agnostic source).

  (4) CASTLING SIDE DISTRIBUTION. A 3-way recency-weighted fraction over
      {kingside, queenside, never castled} for the OPPONENT's castling
      move (not both sides — we profile the opponent's style, not the
      game's). Detected via `board.is_kingside_castling(move)` /
      `board.is_queenside_castling(move)` on the opponent's moves
      during the mainline replay; no PGN header carries castling side.

  (5) QUEEN-TRADE TIMING. Two SEPARATE stats (the spec is explicit that
      they must not be collapsed into one number):

        (a) `queen_trade_move_number`: for games where BOTH queens leave
            the board by game end, the recency-weighted average of the
            1-indexed PLY of the last queen capture in that game (half-
            move index, same unit as game length, so the two are on a
            comparable scale). None iff sufficient=True but no game in
            the sample had both queens leave the board (a conservative
            "no signal" rather than 0.0-as-signal).

        (b) `queens_stay_on_rate`: the recency-weighted fraction of games
            where BOTH queens are still on the board at game end. The
            symmetric complement-by-construction of (a)'s eligibility
            set, but reported as a standalone rate (the spec wants them
            as two keys, not as "1 - other"). The asymmetric middle case
            (exactly one queen on at end — one side queen traded, the
            other survived, possibly via promotion) contributes to
            NEITHER signal: (a)'s eligibility is "both off"; (b)'s
            numerator is "both on". This is the explicit reading of the
            spec, which pairs "both queens leave" with "queens stay on"
            as a conceptual pair.

  (6) DATA FLOOR + RECENCY WEIGHTING at the aggregate (per-opponent) level.
      The floor is lower than the per-position floor in opponent_repertoire
      (3 vs 5) for the reason the spec flagged: aggregates across a whole
      history need fewer games than per-position sampling.

DERIVABILITY (verified before implementation): all three new signals are
derivable purely by replaying each game's mainline with python-chess. No
PGN header is consulted for any of them — Chess.com PGNs (which dominate
our dev DB) carry no `[PlyCount]`, no castling-side header, and no queen-
trade header; Lichess's optional `[PlyCount]` is redundant with mainline
length, so mainline replay is the only provider-agnostic source.

DEFERRED signals (still NOT implemented here, beyond the original v1 set):
Stockfish-eval-aided sac/blunder discrimination, pawn-gambit detection
(threshold too high in v1), candidate-move family classification (the
input the opening-prep suggestion layer would need to consume
opening_family_lean as a bias). These remain v2+ concerns.
"""
import logging
import math
import re
from io import StringIO
from typing import Any, Dict, List, Optional

import chess
import chess.pgn
from psycopg2.extras import RealDictCursor

from core import database

log = logging.getLogger(__name__)

# --- aggregate floor -------------------------------------------------------
#
# Minimum effective sample size (sum of per-game recency weights) before
# this layer commits to a style profile. Below it `compute_opponent_style`
# reports `sufficient=False` and the caller falls through to default Maia
# behaviour — exactly the same contract `pick_repertoire_move` uses for
# the per-position case.
#
# The gate is on EFFECTIVE SAMPLE SIZE (sum of weights), not raw game
# count. Under exponential decay (lambda=1.0, half-life ~8.3 months),
# effective_sample_size is always <= raw game count, so this gate is
# stricter than a raw-count floor for stale opponents — a 500-game
# opponent whose games are all 4 years old has effective_sample_size
# ~= 500 * exp(-4.0) = ~9.1, which BARELY clears the floor (not trivially).
# A 500-game opponent whose games are all recent has effective_sample_size
# ~= 460, which trivially clears. The gate thus distinguishes recent-large
# from stale-large opponents — the spec's explicit requirement.
#
# Why 5.0 and not the old 3 (raw count):
#   * The old floor (MIN_STYLE_GAMES=3) was on RAW game count. Under decay,
#     3 recent games give effective_sample_size=3.0, which would FAIL the
#     new floor of 5.0. This is intentional: 3 recent games is thin for a
#     style profile (the old comment called it "barely-sufficient"), and
#     the raised import limit (500) means most opponents will have many
#     more games. A floor of 5.0 requires ~5 recent games' worth of
#     signal, which is a more defensible threshold.
#   * Under lambda=1.0, a 500-game opponent with all 4-year-old games has
#     effective_sample_size ~9.1, which clears 5.0 — but its signals are
#     heavily decayed (each game contributes 0.018 weight). This is the
#     "soft prior" behaviour the spec wants: old patterns still produce a
#     signal, just a weak one. To FULLY reject stale-large opponents the
#     floor would need to be >9.1, but that would also reject recent
#     small opponents (e.g. a recent 10-game opponent at ~9.2) — too
#     strict. The 5.0 floor is the calibrated balance.
#   * The old "rule of three" intuition (>=3 events) still holds for
#     RECENT games: 5 recent games give effective_sample_size=5.0, well
#     past the rule-of-three threshold. For stale games, the effective
#     sample size is lower, so more raw games are needed to clear the
#     floor — which is the desired behaviour.
#
# Existing test fixtures (all using end_time=0 or recent end_times) clear
# this floor: the 6-game signals fixture gives effective_sample_size=6.0,
# the 12-game established fixture gives ~11.0, and the 2-game thin fixture
# gives 2.0 (below floor, correctly insufficient).
#
# Tune up if noisy style signals leak into re-ranking; tune down if too
# many established-but-few-games opponents fall through to default Maia.
#
# OPEN QUESTION (same as the TC-axis gap below): this floor on the RECENCY
# axis means an opponent with a small number of OLD games (e.g. 8 games all
# 3+ years old -> eff ~0.4) is rejected entirely rather than softened into
# a weak prior. That is the small-stale case, and whether a sub-5.0-eff
# profile is useful-vs-noise is UNRESOLVED -- deliberately deferred to the
# self-play move-prediction calibration backlog, not chased here.
MIN_STYLE_EFF_SAMPLES = 5.0

# Legacy raw-count floor. Retained for backward-compat references in
# docstrings and tests, and as a secondary hard floor (compute_opponent_style
# also checks game_count >= 1 to avoid dividing by zero on an empty
# corpus). The PRIMARY gate is now MIN_STYLE_EFF_SAMPLES.
MIN_STYLE_GAMES = 3

# --- recency decay ---------------------------------------------------------
#
# Per-game exponential decay rate, per year, applied to per-game weights
# when computing the weighted average of each style signal.
#
# PROVISIONAL VALUE — pending empirical tuning. lambda=1.0 was chosen as a
# reasoned starting point (half-life = ln(2)/1.0 ~= 0.693 yr ~= 8.3 months),
# NOT a tuned value. A follow-up task will calibrate this against measured
# self-play move-prediction accuracy once that test harness exists; until
# then, do not treat 1.0 as final. The constant is named explicitly so the
# calibration task can grep for it.
#
# Rationale for 1.0 (vs the previous 0.5 / half-life ~1.39 yr): with the
# import limit raised to 500 games (~1-2 years of active play), a lambda of
# 0.5 left 2-year-old games at 37% weight — too much for "recent games
# dominate, older games act only as a soft prior." At lambda=1.0, a
# 1-year-old game carries 37% weight and a 2-year-old game carries 14%,
# which gives recent games the dominant voice while still letting old
# patterns contribute a soft prior (the spec's explicit ask).
#
# DECoupled from RECENCY_DECAY_LAMBDA_PER_YEAR in opponent_repertoire.py
# (which stays at 0.5 — repertoire move counts use mild decay only; see
# pick_repertoire_move's docstring). The two layers age on different
# timescales: openings evolve slowly (repertoire), but player style
# shifts can manifest within months (sac tendencies, castle preferences).
# Cross-service constant imports of magic tuning numbers are an awkward
# coupling, and the two layers may legitimately want different lifetimes.
STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR = 1.0

# Seconds per year (365.25 days, leap-year averaged).
_SECONDS_PER_YEAR = 365.25 * 86400.0

# --- sacrifice heuristic (v1) ---------------------------------------------
#
# A move by the opponent counts as a SACRIFICE in v1 iff both hold:
#
#   (a) At the END of a SAC_RECOUP_PLIES-ply look-ahead window starting at
#       the opponent's move, the opponent's static material
#       (P=1, N=3, B=3, R=5, Q=9, K=0) is at least SAC_MATERIAL_THRESHOLD
#       points lower than immediately before the move.
#
#   (b) At no intermediate ply inside that window does the opponent's
#       material recover to within SAC_RECOUP_TOLERANCE of its pre-move
#       level. "Recoup" means the lost material comes back — typically a
#       sacked piece gets cleared by a follow-up capture, or the opponent
#       wins equal-or-greater material back in the next exchange.
#
# Measuring the drop over the *window* (not strictly "immediately after the
# opponent's move") is what lets this catch the most common case: an
# opponent move that EXPOSES a piece, where the material actually leaves
# the board on the opponent-of-opponent's recapture reply. The window
# covers up to 3 plies of tactical context so a one-ply recapture after a
# hanging piece still registers as a sacrifice by the player who hung it.
#
# Defaults: threshold=3, recoup window=3 plies, tolerance=0.5.
#
# Intentional documented limitations of v1 (these are why Stockfish-based
# sac detection is on the deferred list, not because v1 is wrong but
# because no static rule can fully separate "sac" from "blunder"):
#
#   * CONFLATES SACRIFICE WITH HANGING-PIECE BLUNDER. A move that drops a
#     piece to a simple tactic and never recovers it scores the same as a
#     genuine positional sac — both trip (a)+(b). Distinguishing "intended
#     sacrifice with compensation" from "just hung a piece" requires
#     evaluation, which is deferred. For style profiling this is fine:
#     a player who frequently drops pieces *is* stylistically interesting
#     to a sparring partner (whether to punish or to distrust), regardless
#     of intent.
#
#   * DELIBERATELY MISSES PAWN GAMBITS. With threshold=3 a single-pawn
#     swing (delta=1) does not register. This is a noise/signal trade:
#     at threshold=1 every defended-pawn push that gets taken, every
#     isolated-dawn-pawn capture, every IQP structure would trip the
#     detector and the rate would be dominated by ordinary pawn play.
#     Pawn-gambit detection is deferred to a v2 that ships with eval
#     context (so "pawn given for clear compensation" can be told from
#     "pawn lost to inertia").
#
#   * RECOUP WINDOW OF 3 PLIES IS SHORT. A positional sac whose
#     compensation materialises 6-10 plies later (common in dragon-style
#     middlegames) is flagged as a sac by v1 because nothing recouped in
#     the first 3 plies — which is *correct* by the heuristic's purpose
#     ("material given up"), but means the rate may over-count "long"
#     positional sacs relative to "short" tactical ones. Acceptable for
#     v1; widen the window in v2 if the rate looks systematically high
#     on positional players.
#
#   * DOES NOT DISTINGUISH SACRIFICING COLOR. A sacrifice is counted when
#     the *opponent* gives up material. The opponent's own sacrifices are
#     the signal; sacrifices *by* the opponent-of-the-opponent (i.e. the
#     user who imported the game) never enter this counter. This matches
#     the spec: the layer profiles the imported opponent's style, not the
#     game's both-sided style.
#
#   * QUEEN TRADES FALSE-POSITIVE AS SACRIFICES. By the same (a)+(b) rule,
#     a clean queen trade (QxQ, then the recapture captures the recapturer)
#     registers as a sacrifice by whichever side loses their queen first
#     within the 3-ply window: their material drops by 9 with no recoup.
#     This is documented as a known v1 limit; the queen-trade timing
#     signals below (queen_trade_move_number + queens_stay_on_rate) surface
#     the underlying phenomenon separately, so a caller that wants to
#     distinguish "queen-trade sacs" from "real piece sacs" can do so by
#     cross-referencing the two signals. A v2 that ships with eval context
#     would also fix this from the sac side.
SAC_MATERIAL_THRESHOLD = 3
SAC_RECOUP_PLIES = 3
SAC_RECOUP_TOLERANCE = 0.5

# --- setup-structure signature (v1) ----------------------------------------
#
# A "setup signature" is a per-game snapshot of the PROFILED player's pawn
# skeleton + piece placement, taken at plies inside [PLY_MIN, PLY_MAX] after
# the profiled player's move. Aggregated across their historic games, the
# set of snapshots feeds the style-bias re-ranker's per-candidate
# `setup_mult` -- a multiplicative boost for moves whose resulting board
# shape is similar (Jaccard composite, computed in opponent_style_reranker)
# to a shape the profiled player has actually reached. This is the
# mechanism that makes the sparring bot play the profiled player's
# unusual setups (e.g. Scandinavian ...a6 ...Bf5 ...e6) once the bot is
# out of the per-position repertoire book, rather than always defaulting
# to Maia-3's aggregate-preferred move in that family.
#
# Why ply-window [10, 20]:
#   * ply >= 10: by half-move 10 the profiled player has made >=4 moves --
#     their unusual setup is plausibly in place. Earlier plies risk
#     capturing still-in-book positions.
#   * ply <= 20: below the typical middlegame where the *opponent's* choices
#     start to dominate the shape. The profiled player's setup-consistent
#     voice is loudest in the 10-20 ply window.
# Tunable constants; widening toward [10, 26] is reasonable if a player's
# setups form late (per-opening-family tuning is a v2 concern).
#
# SNAPSHOTS_MAX_PER_OPPONENT bounds the total snapshot dict payload surfaced
# by compute_opponent_style. Defensive bound on transport size, NOT a signal
# filter. Originally 250 (which truncated the snapshot pool to ~40 most-
# recent games worth and could exclude minority openings entirely -- the
# live diagnostic on iaminspiredbroo's 500-game corpus showed this: 7
# Scandinavian games were all dropped, leaving zero Scandinavian snapshots
# and breaking family-filtering for the user's preferred opening). Raised
# to 10000: ~2500 snapshots for a 500-game opponent, well within memory
# budget, and the per-candidate Jaccard scan (~1ms at this size) is
# still cheap vs the 1-3s Maia inference.
SETUP_SIGNATURE_PLY_MIN = 10
SETUP_SIGNATURE_PLY_MAX = 20
SETUP_SIGNATURE_MIN_GAMES = 3   # mirrors MIN_STYLE_GAMES; same rationale
SNAPSHOTS_MAX_PER_OPPONENT = 10000

# Static material values used by the sacrifice detector. King counts as 0
# so a king safety manouvre never inflates material.
_PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def _normalize_username(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def _material_for_color(board: chess.Board, color: chess.Color) -> int:
    """Sum P=1 N=3 B=3 R=5 Q=9 K=0 for `color`. King is 0 by design."""
    total = 0
    for piece_type, value in _PIECE_VALUE.items():
        total += value * len(board.pieces(piece_type, color))
    return total


def _game_recency_weight(end_time: int, now_unix: float) -> float:
    """exp(-lambda * age_years) for one game, with end_time=0 -> neutral 1.0.

    Mirrors the SQL expression in opponent_repertoire.py:
      age_seconds = max(0, now - end_time)
      weight = exp(-lambda * age_years) = exp(-lambda * age_seconds / SECONDS_PER_YEAR)
    """
    if not end_time or end_time <= 0:
        return 1.0
    age_seconds = max(0.0, now_unix - float(end_time))
    age_years = age_seconds / _SECONDS_PER_YEAR
    return math.exp(-STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR * age_years)


# --- time-control bucketing + similarity (v1) ------------------------------
#
# Players play differently in bullet, blitz, rapid, and classical. Mixing
# them without distinction adds noise to every style signal (a bullet sac
# rate is not a rapid sac rate). This section adds a per-game time-control
# similarity multiplier so that, when computing style for a sparring game,
# games from the SAME (or similar) time control count more heavily. The
# final per-game weight becomes:
#
#     w_i = _game_tc_weight(game_bucket, sparring_bucket)
#               * exp(-lambda * age_in_years_i)
#
# i.e. the PRODUCT of the time-control similarity and the recency decay
# above. Every style aggregate that already used the recency weight now
# uses this combined weight instead, so same-TC games dominate the
# aggregates while cross-TC games act as a down-weighted SOFT PRIOR (NOT
# purged -- a 0.2-weight bullet game still contributes to a rapid profile,
# just weakly). This is the same "old evidence survives as a soft prior"
# philosophy the recency decay uses, applied across the time-control axis.
#
# BUCKETING: four coarse buckets -- bullet / blitz / rapid / classical --
# the granularity the similarity matrix operates on (the spec: "keep it
# simple for v1"). The source `time_class` DB column (populated by the
# Chess.com / Lichess importers as "bullet"/"blitz"/"rapid"/"classical"/
# "daily"/"correspondence") is the PRIMARY source; the PGN `[TimeControl]`
# header ("base+inc" seconds, e.g. "180+2") is a FALLBACK for games whose
# `time_class` is missing or unrecognised (manual imports, edge cases).
# daily/correspondence map to the "unknown" bucket rather than "classical":
# correspondence is genuinely a different beast from OTB classical, and
# conflating them would penalise real correspondence games against a
# classical sparring session.
_TC_BUCKETS = ("bullet", "blitz", "rapid", "classical")

# SIMILARITY MATRIX. _TC_SIMILARITY[game_bucket][sparring_bucket] is the
# multiplier applied to a game whose bucket is `game_bucket` when the
# sparring session's bucket is `sparring_bucket`. Symmetric by construction.
# PROVISIONAL reasoned starting values (same status as the recency lambda --
# tune with measured self-play move-prediction accuracy once available):
#   * diagonal = 1.0  (same-TC games count fully)
#   * adjacent speeds drop gently (bullet<->blitz=0.5, blitz<->rapid=0.6,
#       rapid<->classical=0.7) -- neighbour speeds are related but not
#       identical.
#   * far pairs drop harder (bullet<->rapid=0.2, bullet<->classical=0.1,
#       blitz<->classical=0.3) -- a bullet sac rate tells you almost
#       nothing about a classical game.
_TC_SIMILARITY: Dict[str, Dict[str, float]] = {
    "bullet":    {"bullet": 1.0, "blitz": 0.5, "rapid": 0.2, "classical": 0.1},
    "blitz":     {"bullet": 0.5, "blitz": 1.0, "rapid": 0.6, "classical": 0.3},
    "rapid":     {"bullet": 0.2, "blitz": 0.6, "rapid": 1.0, "classical": 0.7},
    "classical": {"bullet": 0.1, "blitz": 0.3, "rapid": 0.7, "classical": 1.0},
}

# OPEN QUESTION (TC axis, mirrors the small-stale gap at
# MIN_STYLE_EFF_SAMPLES): the floor was validated against TC-mismatch NOT
# spuriously triggering it -- a 10-game all-cross-TC corpus under a
# mismatched sparring TC still clears (10 blitz x 0.6 = 6.0 >= 5.0). But
# whether a THIN cross-TC profile (e.g. 6 rapid-equivalent games' worth of
# blitz behaviour) is actually a better bias than falling through to
# default Maia is UNRESOLVED -- same open question as small-stale, just on
# the TC axis instead of the age axis. Deliberately deferred to the
# self-play move-prediction calibration backlog, not chased here.


def _bucket_from_tc_str(tc_str: str) -> str:
    """Map a "base+inc"/"base" time-control string to a coarse bucket.

    Used in two contexts: (a) the FALLBACK path when a game's DB `time_class`
    is missing -- here `tc_str` is a raw PGN `[TimeControl]` body in SECONDS
    (e.g. "180+2"); (b) the sparring-TC path, where `tc_str` may be an "M+I"
    minute label from the sparring prefill (e.g. "3+2", produced by
    `_time_control_label`). Both are disambiguated below.

    Estimates total game time as base + increment*40 (40 moves is a typical
    full-game move count -- the standard "estimated total time" heuristic
    chess platforms use internally) and buckets by the resulting seconds:
        < 180s  (3 min)  -> bullet
        < 600s  (10 min) -> blitz
        < 1800s (30 min) -> rapid
        else             -> classical
    These thresholds align with Chess.com/Lichess speed boundaries.

    MINUTES-vs-SECONDS disambiguation: real online controls have a base of
    >= 60 seconds, so a base value < 60 is interpreted as MINUTES (the
    "M+I" label form). A base >= 60 is interpreted as raw SECONDS (the PGN
    header form). The only failure case is the "60+0" minute label (a
    1-hour game, vanishingly rare in online play), which would be read as
    60 seconds -> bullet; acceptable for v1. Returns "unknown" for an
    unparseable string so the caller applies the safe neutral weight 1.0
    ("don't penalise what you can't classify").
    """
    m = _TC_BASE_INC_RE.match(tc_str)
    if m:
        base = int(m.group(1))
        inc = int(m.group(2))
    else:
        m = _TC_BASE_ONLY_RE.match(tc_str)
        if not m:
            return "unknown"
        base = int(m.group(1))
        inc = 0
    if base < 0 or inc < 0:
        return "unknown"
    seconds = base * 60 if base < 60 else base
    est_total = seconds + inc * 40
    if est_total < 180:
        return "bullet"
    if est_total < 600:
        return "blitz"
    if est_total < 1800:
        return "rapid"
    return "classical"


def _time_control_bucket(raw: Optional[str], pgn: str = "") -> str:
    """Resolve a time-control source to one of the coarse buckets or "unknown".

    `raw` is whatever time-control string the caller has: the DB `time_class`
    column value (bullet/blitz/rapid/classical/daily/correspondence), a raw
    "base+inc" PGN-header body, an "M+I" minute label, or a canonical bucket
    label the sparring endpoint received from the frontend. `pgn` is an
    optional full PGN string consulted ONLY when `raw` is empty/unrecognised,
    so a game with a missing `time_class` but a parseable `[TimeControl]`
    header still gets bucketed via the header fallback.

    Resolution order:
      1. `raw` matches a canonical bucket name (case-insensitive) -> that
         bucket. Covers the DB `time_class` column and any caller passing a
         bucket label directly.
      2. `raw` is "daily"/"correspondence" -> "unknown" (correspondence is
         genuinely different from OTB classical; don't penalise it).
      3. `raw` matches a "base+inc"/"base" pattern -> bucketed via
         `_bucket_from_tc_str`. Covers raw PGN headers AND "M+I" labels.
      4. `pgn` has a `[TimeControl]` header -> `_bucket_from_tc_str` on it.
         The fallback path for games whose `time_class` is missing.
      5. "unknown" -- the caller applies the neutral 1.0 weight so an
         unclassifiable game is never penalised, only flagged in the
         transparency output for visibility.
    """
    if raw:
        key = raw.strip().lower()
        if key in _TC_BUCKETS:
            return key
        if key in ("daily", "correspondence"):
            return "unknown"
        bucket = _bucket_from_tc_str(raw)
        if bucket != "unknown":
            return bucket
    if pgn:
        bucket = _bucket_from_tc_str(_time_control_header(pgn))
        if bucket != "unknown":
            return bucket
    return "unknown"


def _game_tc_weight(
    game_bucket: str, sparring_bucket: Optional[str]
) -> float:
    """Time-control similarity multiplier for one game vs the sparring session.

    Returns `_TC_SIMILARITY[game_bucket][sparring_bucket]`. Both the game
    and the sparring session must resolve to a KNOWN bucket
    (bullet/blitz/rapid/classical) for a non-1.0 weight; any "unknown" side
    yields the neutral 1.0 ("don't penalise what you can't classify"):
      * sparring bucket unknown/None -> 1.0 for every game (safe fallback;
        existing callers that don't pass a sparring TC are unaffected --
        the combined weight collapses to recency-only).
      * game bucket "unknown" -> 1.0 (an unclassifiable game is not
        penalised, only flagged in the transparency output).
    The product w = _game_tc_weight(...) * _game_recency_weight(...) is what
    every style aggregate in `compute_opponent_style` now uses, so same-TC
    games dominate while cross-TC games act as a down-weighted soft prior.
    """
    if not sparring_bucket or sparring_bucket == "unknown":
        return 1.0
    if game_bucket == "unknown" or game_bucket not in _TC_SIMILARITY:
        return 1.0
    return _TC_SIMILARITY[game_bucket][sparring_bucket]


def _opening_family(game: chess.pgn.Game) -> str:
    """Best-effort opening family label for a parsed game.

    Strategy (in order, first non-empty wins):

      1. The PGN `[Opening]` header, truncated at the first `:` so that
         `"Sicilian Defense: Najdorf Variation"` -> `"Sicilian Defense"`.
         Lichess populates this header on essentially every exported
         PGN. (Chess.com PGNs lack `[Opening]` entirely — see step 2.)

      2. The PGN `[ECOUrl]` header, parsed by `_family_from_ecourl` into
         a quoted family name. Chess.com exports a unique header
         `[ECOUrl "https://www.chess.com/openings/<hyphenated-path>"]`
         in place of an `[Opening]` header. The URL's path encodes the
         family + variation + move list all hyphen-separated; the helper
         extracts just the family. This is the path all 500 production
         opponent_games rows in the dev DB take (verified 2026-08-04), so
         it is the de facto live common case, not a corner case.

      3. The PGN `[ECO]` header's first letter (A/B/C/D/E) — a very
         coarse fallback (5 bins total) used only when neither `[Opening]`
         nor `[ECOUrl]` is present. python-chess has NO built-in ECO->name
         classifier (verified: no `chess.eco` module in 1.11.2), so we
         cannot go from an ECO code to a named family without shipping a
         full ECO lookup table — deferred to v2 only if real data shows
         games reaching this fallback.

      4. `"_unknown"` — the game contributes 0 to the named-family
         distribution but remains on the denominator, so a manual-import
         opponent without family labels doesn't silently inflate the
         named families' fractions.

    Family labels are returned as-is (no casefolding) because the upstream
    PGN spellings are canonical and consistent within a provider.

    Known cross-provider divergence (v1 limit, documented): for openings
    whose canonical family name does NOT end in a recognized family
    suffix word, the two providers' labels differ slightly. The
    canonical example is Ruy Lopez: Lichess `[Opening "Ruy Lopez: ..."]`
    -> `"Ruy Lopez"` (colon-truncated to the bare name); Chess.com
    `[ECOUrl "Ruy-Lopez-Opening-..."]` -> `"Ruy Lopez Opening"` (the
    family-suffix word "Opening" is part of Chess.com's URL path).
    An opponent who plays Ruy Lopez on Lichess and Chess.com would
    therefore have those games split across two bins. This is rare
    in practice (most family names DO end in the suffix words) and is
    not fixed in v1 because the dev DB has zero Lichess PGNs to test
    against — adding a provider-specific Ruy-Lopez special case now
    would be blind tuning. Revisit when the dev DB has a mixed-provider
    dataset.
    """
    opening = (game.headers.get("Opening") or "").strip()
    if opening:
        # Truncate at the first colon so "Ruy Lopez: Berlin Defense"
        # -> "Ruy Lopez". Any parenthesised annotation after the colon
        # ("Sicilian Defense: Najdorf, Adams Attack (10.g4)") is also
        # discarded by this same split.
        head = opening.split(":", 1)[0].strip()
        if head:
            return head
        return opening

    ecourl = (game.headers.get("ECOUrl") or "").strip()
    if ecourl:
        family = _family_from_ecourl(ecourl)
        if family:
            return family

    eco = (game.headers.get("ECO") or "").strip()
    if eco:
        return f"ECO-{eco[0].upper()}"
    return "_unknown"


# Family-suffix words used by `_family_from_ecourl` to identify the end of
# the family portion of a Chess.com ECOUrl path. Surveyed against the 156
# distinct ECOUrls present in the dev DB (2026-08-04); every such URL
# terminates its family name with one of: Opening, Defense, Game, Gambit,
# System, Attack. (Some family names are 3-4 tokens before the suffix word:
# "Kings Pawn Opening", "Kings Indian Attack", "Nimzowitsch Larsen Attack",
# "Van t Kruijs Opening" — so the rule must continue PAST tokens until the
# suffix is seen, not stop at a fixed token count.)
_FAMILY_SUFFIX_WORDS = (
    "opening", "defense", "game", "gambit", "system", "attack",
)

# Regex that locates the start of the move-list suffix in an ECOUrl path.
# Chess.com ECOUrls embed the move list in two forms, both of which this
# regex terminates (matching the EARLIEST hit, which is the start of the
# move list):
#   * `\d+\.` — a regular move like "5.Be3" in "...-Classical-Variation-5.Be3-Qf6"
#   * `\.{2,}\d` — a skip-notation like "..." followed by a digit, e.g.
#     "Sicilian-Defense...3.g3" (used by Chess.com for black-only move
#     lists where White's reply is elided). When this is concatenated
#     directly to the family word without a hyphen — the most common
#     real-world case we observed in the dev DB — a naive hyphen-split
#     produces a single token like "Defense...3.g3" which would NOT match
#     the family-suffix rule below; the regex truncation here preemptively
#     strips that move list so the family walk sees a clean "Defense"
#     token.
#
# The match's `start` is the position of the FIRST char of the move-list
# delimiter (the digit, for `\d+\.`; the first dot, for `\.{2,}\d`) — we
# truncate the path at that position, drop any trailing "-".
_ECOURL_MOVE_LIST_RE = re.compile(r"\d+\.|\.{2,}\d")


def _family_from_ecourl(ecourl: str) -> str:
    """Extract an opening family name from a Chess.com `[ECOUrl]` string.

    Chess.com exports opening names only inside a proprietary
    `[ECOUrl "https://www.chess.com/openings/<hyphenated-name>"]` PGN
    header — no `[Opening]` header. The path component after
    `/openings/` is the family + variation + move-list, all hyphen-
    separated (Chess.com uses "..." with no surrounding hyphen when
    concatenating a black-only move list with the family word, e.g.
    "Sicilian-Defense...3.g3-Nf6-4.d3-Nc6-5.Bg2"). Examples from real
    data, after the fix in this version:

        https://www.chess.com/openings/French-Defense-Winawer-Variation-4.Bd3-c5-5.exd5-Qxd5
            -> "French Defense"            (stops at "Defense")
        https://www.chess.com/openings/Scotch-Game-Classical-Variation-5.Be3-Qf6
            -> "Scotch Game"               (stops at "Game", drops Classical Variation + moves)
        https://www.chess.com/openings/Kings-Pawn-Opening-1...e5
            -> "Kings Pawn Opening"        (stops at "Opening", drops move list)
        https://www.chess.com/openings/Caro-Kann-Defense-Advance-Botvinnik-Carls-Defense-4.c3
            -> "Caro Kann Defense"         (stops at "Defense", drops sub-family + moves)
        https://www.chess.com/openings/Nimzowitsch-Larsen-Attack-1...g6
            -> "Nimzowitsch Larsen Attack" (stops at "Attack", 3-name family)
        https://www.chess.com/openings/Van-t-Kruijs-Opening
            -> "Van t Kruijs Opening"      (4-name family before "Opening")
        https://www.chess.com/openings/Sicilian-Defense...3.g3-Nf6-4.d3-Nc6-5.Bg2
            -> "Sicilian Defense"          (pre-truncated at "...3.g3" so the
                                              hyphen-split sees a clean "Defense" token,
                                              not the concatenated "Defense...3.g3")
        https://www.chess.com/openings/Slav-Defense...4.e3-b5-5.a4-b4-6.Na2
            -> "Slav Defense"              (same ellipsis-concatenation fix)

    Rule, in order:
      1. Strip the URL prefix (everything up to and including
         `/openings/`); return "" if nothing remains.
      2. Truncate the path at the first match of `_ECOURL_MOVE_LIST_RE`
         (a `\\d+\\.` regular-move marker OR a `\\.{2,}\\d` skip-notation
         marker). Strip any trailing hyphen left by the cut.
      3. Replace hyphens with spaces and walk tokens left-to-right. Stop
         AFTER the first token whose lowercase form ends with one of
         `_FAMILY_SUFFIX_WORDS`; return the accumulated tokens up to and
         including that suffix word. This is the "family".
      4. If no family-suffix word is found anywhere in the (already
         move-list-truncated) path, return the whole decoded path. This
         happens for family-less openings like "Van t Kruijs Opening",
         which has the suffix word "Opening" — they always match step 3.

    Edge case: a pathological URL where "Defense" or "Attack" appears AS
    PART OF the variation name before the real family (e.g. a hypothetical
    "Foo-Defense-Bar-Variation-Quux-Attack-...") would truncate early at
    the first "Defense". None of the 156 real-world ECOUrls in the dev DB
    pattern-match this anti-shape; revisit if real data emerges.
    """
    path = ecourl.strip()
    prefix = "https://www.chess.com/openings/"
    if path.startswith(prefix):
        path = path[len(prefix):]
    elif "/" in path:
        # Defensive: any URL-shaped input, take the last path segment.
        path = path.rstrip("/").rsplit("/", 1)[-1]

    if not path:
        return ""

    # --- step 2: truncate at the first move-list indicator ---
    m = _ECOURL_MOVE_LIST_RE.search(path)
    if m:
        path = path[: m.start()].rstrip("-")
    if not path:
        return ""

    # --- step 3: walk tokens, stop after the first family-suffix word ---
    tokens = path.split("-")
    accumulated: List[str] = []
    for token in tokens:
        accumulated.append(token)
        if token.lower().endswith(_FAMILY_SUFFIX_WORDS) and len(token) > 0:
            return " ".join(accumulated)
    # No family-suffix word found. Return the whole move-list-truncated
    # path with hyphens turned to spaces.
    return " ".join(accumulated).strip()


def _opponent_color(
    game: chess.pgn.Game, normalized_opponent: str
) -> Optional[chess.Color]:
    """Resolve which side of `game` the opponent played, or None if unclear."""
    white = _normalize_username(game.headers.get("White"))
    black = _normalize_username(game.headers.get("Black"))
    if white == normalized_opponent:
        return chess.WHITE
    if black == normalized_opponent:
        return chess.BLACK
    return None


def _is_sacrifice(
    board_before: chess.Board,
    move: chess.Move,
    mainline_after: List[chess.Move],
    move_index: int,
    opponent_color: chess.Color,
) -> bool:
    """Apply the v1 sacrifice heuristic to a single opponent move.

    `board_before` is the position immediately before `move` is played.
    `mainline_after` is the full mainline move list of the game, so we can
    peek ahead `SAC_RECOUP_PLIES` plies from `move_index` to test recoup.
    `move` itself is `mainline_after[move_index]`.

    The window consists of the plies from `move` itself through
    `mainline_after[move_index + SAC_RECOUP_PLIES]` (inclusive). Returns
    True iff:
      (a) at the end of the window the opponent's material is at least
          SAC_MATERIAL_THRESHOLD lower than immediately before the move, AND
      (b) at no intermediate ply in the window did the opponent's material
          recover to within SAC_RECOUP_TOLERANCE of its pre-move level.
    """
    material_before = _material_for_color(board_before, opponent_color)

    # Apply the move on a copy that we will extend into the look-ahead
    # window.
    board_window = board_before.copy(stack=False)
    board_window.push(move)
    material_now = _material_for_color(board_window, opponent_color)

    # Walk forward up to SAC_RECOUP_PLIES plies, checking at each step
    # whether opponent material recovers to within tolerance of the
    # baseline. The very common "opponent hangs a piece, opponent reply
    # captures it next ply" pattern only shows its material drop one ply
    # AFTER the opponent's own move — so we MUST NOT bail out early on the
    # opponent's move just because their own material is unchanged there;
    # the recapture happens in the look-ahead window, not on the move
    # itself.
    for look_idx in range(move_index + 1, move_index + 1 + SAC_RECOUP_PLIES):
        if look_idx >= len(mainline_after):
            break
        board_window.push(mainline_after[look_idx])
        material_now = _material_for_color(board_window, opponent_color)
        if material_now >= material_before - SAC_RECOUP_TOLERANCE:
            return False

    # Window ended without recoup. Was the final net loss large enough?
    return (material_before - material_now) >= SAC_MATERIAL_THRESHOLD


def _pov_snapshot_squares(
    board: chess.Board, pov: chess.Color
) -> Dict[str, Any]:
    """Extract pawn + piece squares from `board` normalized to `pov`'s view.

    Returns a snapshot dict shaped per the setup-signature contract:
        {
          "pawn_squares":      List[str],   # POV-normalized pawn squares
          "piece_squares":     Dict[str, List[str]],  # N/B/R/Q/K -> squares
          "opp_pawn_squares":  List[str],   # opponent's pawns, user-POV
                                             # mirrored to keep canonical
                                             # orientation. Used ONLY by
                                             # the reranker's family-detection
                                             # Jaccard (openings are defined
                                             # by BOTH sides' pawn shapes --
                                             # Scandinavian and Italian have
                                             # identical user POV pawns but
                                             # different opponent pawns);
                                             # setup_MULT scoring ignores
                                             # this field.
        }

    POV normalization:
      * pov == WHITE: read WHITE's pieces as-is (squares are rank 1-8).
      * pov == BLACK: mirror the board vertically first, then read WHITE's
        pieces of the mirrored board (which were originally BLACK's, now
        shown from BLACK's POV with squares on rank 1-8). This is the
        symmetric convention so a player's White and Black setups pool
        into one canonical view: a `...a6 ...e6` shape the profiled
        player played as BLACK and a `a3 ...e3` shape they played as
        WHITE (if any) both map to the same canonical signature.

    Square names are always post-mirror (chess.square_name on the
    possibly-mirrored board), so the reranker's opposite-side live board
    can apply the same mirror convention and compare apples-to-apples.

    Piece types are collapsed into single-letter keys to keep the
    serialized payload tight. The king is included -- castled-vs-uncastled
    setups differ by one king square in the comparison, giving the
    `setup_mult` cheap sensitivity to castling side without needing a
    separate fingerprint.

    Caller is expected to have already pushed the move that led to this
    board; this function is read-only and does not mutate `board`.
    """
    if pov == chess.BLACK:
        b = board.mirror()
    else:
        b = board
    pawn_squares = sorted(
        chess.square_name(sq) for sq in b.pieces(chess.PAWN, chess.WHITE)
    )
    piece_squares: Dict[str, List[str]] = {}
    for ptype, letter in (
        (chess.KNIGHT, "N"),
        (chess.BISHOP, "B"),
        (chess.ROOK, "R"),
        (chess.QUEEN, "Q"),
        (chess.KING, "K"),
    ):
        piece_squares[letter] = sorted(
            chess.square_name(sq)
            for sq in b.pieces(ptype, chess.WHITE)
        )
    # Opponent's pawns in the user's POV orientation (mirrored already
    # when pov==BLACK). Used only by the reranker's family detection
    # Jaccard to distinguish openings whose user-side pawn shape is
    # identical (e.g. Italian "e4+d3" vs Scandinavian "e4-traded+d3"
    # share White's pawn skeleton; the OPPONENT's pawn shape differs:
    # Italian Black has e5 pawn on the board, Scandinavian Black has
    # d-pawn traded). setup_MULT scoring ignores this field.
    opp_pawn_squares = sorted(
        chess.square_name(sq) for sq in b.pieces(chess.PAWN, chess.BLACK)
    )
    return {
        "pawn_squares": pawn_squares,
        "piece_squares": piece_squares,
        "opp_pawn_squares": opp_pawn_squares,
    }


def _analyze_game(
    pgn: str, normalized_opponent: str
) -> Optional[Dict[str, Any]]:
    """Parse one PGN enough to extract every per-game style signal.

    Returns None if the PGN fails to parse or the opponent's color cannot
    be resolved (the game contributes nothing to any signal).

    Otherwise returns a dict with the per-game primitives every aggregate
    signal in this module consumes:

      {
        "opponent_moves": int,                # opponent plies in this game
        "sacrifices": int,                   # moves flagged as sacrifices by v1
        "family": str,                       # opening family label
        "plies": int,                        # total half-moves in the mainline
        "opp_castled": str,                  # "kingside" | "queenside" | "never"
                                             #   — the OPPONENT's first castle,
                                             #     or "never" if they didn't
                                             #     castle at all in this game
        "last_queen_capture_ply": int|None,  # 1-indexed half-move ply at
                                             #   which the LAST queen of
                                             #   either color was captured,
                                             #   or None if no queen was
                                             #   captured in this game
        "queens_on_at_end": bool,            # True iff BOTH queens are on
                                             #   the board at game end
                                             #   (feeds queens_stay_on_rate)
"queens_off_at_end": bool,            # True iff BOTH queens are OFF
                                              #   the board at game end
                                              #   (feeds queen_trade_move_number
                                              #   eligibility; the asymmetric
                                              #   middle case — exactly one
                                              #   queen on — has both bools
                                              #   False and contributes to
                                              #   neither signal)
        "setup_snapshots": List[Dict],        # per-game setup-signature
                                              #   snapshots captured at
                                              #   plies in
                                              #   [SETUP_SIGNATURE_PLY_MIN,
                                              #    SETUP_SIGNATURE_PLY_MAX]
                                              #   after the profiled
                                              #   player's move, each
                                              #   POV-normalized to that
                                              #   player's color (see
                                              #   _pov_snapshot_squares).
                                              #   Feeds the reranker's
                                              #   `setup_mult` bias at
                                              #   sparring time. Empty
                                              #   list iff no profiled-
                                              #   player move fell in the
                                              #   window (impossible for a
                                              #   real game that reached
                                              #   ply 10+, but defensive).
        "result": str,                       # OPPONENT-POV result:
                                              #   "win"|"loss"|"draw"|"*"
                                              #   translated from the PGN
                                              #   [Result] header via the
                                              #   opponent's resolved
                                              #   color. Feeds
                                              #   compute_opening_results'
                                              #   per-opening W/L/D
                                              #   breakdown; paired with
                                              #   `family` below so the W/
                                              #   L/D buckets are the SAME
                                              #   buckets opening_family_lean
                                              #   uses (no forked binning).
      }

    All non-sacrifice signals here are derived by replaying the mainline
    with python-chess — no PGN header is consulted for them (verified
    pre-implementation: Chess.com carries no header for any of plies /
    castling side / queen-trade timing, and Lichess's optional
    `[PlyCount]` is redundant with `len(mainline)`).
    """
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None:
        return None
    opponent_color = _opponent_color(game, normalized_opponent)
    if opponent_color is None:
        return None

    # PGN `[Result]` header: "1-0" (white wins), "0-1" (black wins),
    # "1/2-1/2" (draw — also seen as "½-½"), or "*" (unfinished/aborted).
    # Normalized to "win"/"loss"/"draw"/"*" from the OPPONENT's POV — the
    # game-level result is flipped for the opponent-of-the-opponent. Used by
    # `compute_opening_results` for the per-opening W/L/D breakdown (the
    # spec for "Openings He Lost Against"); sharing the same `_analyze_game`
    # pass the other signals use means opening_family_lean and the new
    # per-opening W/L/D use the EXACT same bucketing `_opening_family`
    # produces (no forked/reimplemented binning).
    raw_result = (game.headers.get("Result") or "").strip()
    if opponent_color == chess.WHITE:
        if raw_result == "1-0":
            result: str = "win"
        elif raw_result == "0-1":
            result = "loss"
        elif raw_result in ("1/2-1/2", "½-½"):
            result = "draw"
        else:
            result = "*"
    else:
        if raw_result == "0-1":
            result = "win"
        elif raw_result == "1-0":
            result = "loss"
        elif raw_result in ("1/2-1/2", "½-½"):
            result = "draw"
        else:
            result = "*"

    mainline = list(game.mainline_moves())
    if not mainline:
        return None

    board = game.board()
    opponent_moves = 0
    sacrifices = 0
    opp_castled = "never"
    queen_capture_plies: List[int] = []
    # Per-game setup-signature snapshots. Captured AFTER the profiled
    # player's move at every ply inside [SETUP_SIGNATURE_PLY_MIN,
    # SETUP_SIGNATURE_PLY_MAX] (1-indexed half-moves). An evenly-paced
    # game yields ~5-6 snapshots (the profiled player's moves 5-10); the
    # cost is ~6 dict-appends per game so this is cheap on the mainline
    # walk we already do.
    setup_snapshots: List[Dict[str, Any]] = []

    for idx, move in enumerate(mainline):
        is_opponent_move = board.turn == opponent_color
        if is_opponent_move:
            opponent_moves += 1
            if _is_sacrifice(board, move, mainline, idx, opponent_color):
                sacrifices += 1
            # Track the opponent's FIRST castle. Castling rights are lost
            # after the first castle, so a second castle move by the same
            # side is impossible — the first observed is the only one and
            # also the canonical "castling side for this game".
            if opp_castled == "never":
                if board.is_kingside_castling(move):
                    opp_castled = "kingside"
                elif board.is_queenside_castling(move):
                    opp_castled = "queenside"
        # Queen-capture detection runs on EVERY move (either color), not
        # just the opponent's: a queen trade involves both a capturer and
        # a recapturer, and we want the LAST ply at which any queen left
        # the board, regardless of which side wielded the capturing move.
        captured = board.piece_at(move.to_square)
        if captured is not None and captured.piece_type == chess.QUEEN:
            # 1-indexed ply to match the user-facing "move number" idiom
            # (and to be on the same scale as `plies`).
            queen_capture_plies.append(idx + 1)
        board.push(move)

        # Setup signature: snapshot AFTER the just-pushed move, iff the
        # move was by the profiled player AND its 1-indexed ply is inside
        # the [PLY_MIN, PLY_MAX] window. `idx` is 0-indexed so ply=idx+1.
        # Snapshots are POV-normalized to the profiled player's color so
        # the player's White and Black setups pool into one canonical
        # view -- see _pov_snapshot_squares.
        if is_opponent_move:
            ply = idx + 1
            if SETUP_SIGNATURE_PLY_MIN <= ply <= SETUP_SIGNATURE_PLY_MAX:
                snapshot = _pov_snapshot_squares(board, opponent_color)
                snapshot["snapshot_ply"] = ply
                setup_snapshots.append(snapshot)

    white_queens = len(board.pieces(chess.QUEEN, chess.WHITE))
    black_queens = len(board.pieces(chess.QUEEN, chess.BLACK))
    queens_on_at_end = white_queens > 0 and black_queens > 0
    queens_off_at_end = white_queens == 0 and black_queens == 0

    return {
        "opponent_moves": opponent_moves,
        "sacrifices": sacrifices,
        "family": _opening_family(game),
        "opponent_color": "white" if opponent_color == chess.WHITE else "black",
        "plies": len(mainline),
        "opp_castled": opp_castled,
        "last_queen_capture_ply": (
            max(queen_capture_plies) if queen_capture_plies else None
        ),
        "queens_on_at_end": queens_on_at_end,
        "queens_off_at_end": queens_off_at_end,
        "setup_snapshots": setup_snapshots,
        # OPPONENT-POV result ("win"/"loss"/"draw"/"*") from the [Result]
        # header. Surfaced here so `compute_opening_results` reuses the
        # SAME `_opening_family(game)` call `opening_family_lean` already
        # makes in this function — no forked binning.
        "result": result,
    }


# --- preferred / most-common time control (sparring-page prefill) ----------
#
# Separate signal from the five aggregate style signals above: the
# opponent's most common `[TimeControl]` over their imported games, used by
# the Opponent Preparation / Sparring page to prefill the Time Control
# field when starting a sparring game (the spec: "prefill the Time Control
# field when starting a sparring game").
#
# Unlike the other signals (which are derived by replaying each game's
# mainline), this one is read straight from the PGN's `[TimeControl]`
# header (format "base+inc" in seconds, e.g. "180+2"). python-chess is NOT
# asked to build the full game tree here: we scan the PGN header block with
# a regex and stop, so the cost is sub-millisecond per game even on a
# 500-game opponent (no mainline replay). This is what lets us compute it
# inline in `list_opponent_profiles` (which already aggregates across many
# opponents) without blowing the sparring-page load budget.
#
# Top-N buckets by recency-weighted share are kept as named labels (e.g.
# "3+2", "10+0", "1+0"); anything outside the top few folds into "Other"
# so the sparring-page dropdown isn't polluted with one-off tournament
# controls. Recency weighting reuses the SAME decay as the other signals
# (STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR via _game_recency_weight) so all
# signals in this module age on the same timescale — do NOT redefine a
# decay rate here.
_TOP_TIME_CONTROL_BUCKETS = 4

# Match a PGN `[TimeControl "value"]` header line. PGN headers are
# bracketed and quoted; movetext never starts with `[Key` at column 0 so
# this anchored-multiline regex is header-section-specific without needing
# to find the blank-line separator.
_TC_HEADER_LINE_RE = re.compile(r'^\[TimeControl\s+"([^"]*)"\]', re.MULTILINE)

# `[TimeControl]` body formats we honour (per the PGN spec):
#   * "base+inc"  — base seconds + increment seconds (e.g. "180+2").
#   * "base"      — base seconds only (increment defaults to 0). Some
#                   exports emit this for increment-less time controls.
# Any other form (the spec also permits "*", "-", or hourglass forms) we
# treat as "no time control" rather than a malformed bucket — these are
# vanishingly rare in the providers we import from, and folding them into
# "Other" would conflate "different control" with "no signal".
_TC_BASE_INC_RE = re.compile(r"^\s*(\d+)\+(\d+)\s*$")
_TC_BASE_ONLY_RE = re.compile(r"^\s*(\d+)\s*$")


def _time_control_header(pgn: str) -> str:
    """Return the raw `[TimeControl]` body string from a PGN, or "".

    Reads ONLY the header block via a regex (no python-chess parsing) so
    this is cheap enough to run inline on every game in a per-opponent
    listing endpoint. Empty/missing header -> "" (caller treats as None).
    """
    if not pgn:
        return ""
    m = _TC_HEADER_LINE_RE.search(pgn)
    return m.group(1) if m else ""


def _time_control_label(tc_header: str) -> Optional[str]:
    """Map a PGN `[TimeControl]` body to a human-readable label, or None.

    Honoured formats (per the PGN spec): "base+inc" (seconds + increment)
    and the base-only form. Returns a "M+I" label where M is the base in
    whole minutes when the base divides cleanly by 60 (the overwhelmingly
    common case — every standard chess time control is minute-aligned),
    e.g. "180+2" -> "3+2", "600+0" -> "10+0", "60+0" -> "1+0". For a
    non-minute-aligned base (rare tournament controls like 90+30: back-to-
    back 45-minute halves) we keep a "Ns+I" label (seconds explicit) so the
    bucket is unambiguous rather than collapsing into Other silently.

    Returns None for empty / unparseable headers so the caller excludes
    the game from the denominator — same denominator-consistency contract
    the other signals use: a row without a parseable time control is a
    data-quality gap, not evidence that the opponent prefers "no time
    control".
    """
    if not tc_header:
        return None
    m = _TC_BASE_INC_RE.match(tc_header)
    if m:
        base_sec = int(m.group(1))
        inc = int(m.group(2))
    else:
        m = _TC_BASE_ONLY_RE.match(tc_header)
        if not m:
            return None
        base_sec = int(m.group(1))
        inc = 0
    if base_sec < 0 or inc < 0:
        return None
    if base_sec % 60 == 0:
        return f"{base_sec // 60}+{inc}"
    return f"{base_sec}s+{inc}"


def compute_time_control_distribution(games) -> Dict[str, Any]:
    """Recency-weighted distribution of an opponent's `[TimeControl]` headers.

    `games` is an iterable of dicts each with keys {"pgn", "end_time"} —
    the same row shape `compute_opponent_style` reads from
    `opponent_games`. Each game's PGN header is scanned (regex; NO mainline
    replay) for `[TimeControl]`, normalized to a human-readable "M+I" label
    via `_time_control_label`, and accumulated into a per-bucket weight
    using the SAME recency decay as the other signals
    (`STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR`, half-life ~8.3 months) via
    `_game_recency_weight`. Games with a missing or unparseable
    `[TimeControl]` header contribute to NEITHER numerator nor denominator
    (denominator consistency — a row without a time control is a data-
    quality gap, not a player-style signal).

    Gated by the legacy `MIN_STYLE_GAMES` raw-count floor constant (NOT
    the new `MIN_STYLE_EFF_SAMPLES` effective-sample-size gate that
    `compute_opponent_style` uses): below it, distribution and most_common
    are both None so the caller never prefills a Time Control field off a
    1- or 2-game opponent's history. This function feeds the sparring-page
    prefill (not the reranker), so the stricter recency-based gate is not
    applied here — a 3-game opponent with a clear time-control preference
    should still get the prefill even if the games are old.

    Top-N buckets by recency-weighted share are reported individually;
    everything else is folded into a single "Other" bucket so the sparring
    dropdown only surfaces the controls the opponent actually plays (the
    spec: "grouping anything outside the top few buckets into 'Other'").
    N is `_TOP_TIME_CONTROL_BUCKETS`.

    Returns a dict shaped:
        {
          "sufficient": bool,            # game_count >= MIN_STYLE_GAMES
          "game_count": int,            # raw count, for transparency
          "weighted_game_count": float, # sum of ALL per-game weights
                                         #   (incl. games w/o a TC header)
          "weighted_with_tc": float,    # sum of weights for games that
                                         #   had a parseable [TimeControl];
                                         #   denominator for the fractions
          "distribution": dict | None,   # {bucket: fraction} summing to
                                         #   ~1.0; top-N buckets by weight
                                         #   kept, rest folded into the
                                         #   "Other" bucket (Other always
                                         #   last regardless of weight, to
                                         #   keep the named buckets in
                                         #   descending rank). None iff
                                         #   below floor OR no game had a
                                         #   parseable TC header.
          "most_common": str | None,     # the top-ranked individual bucket
                                         #   (never "Other"); None iff
                                         #   distribution is None.
        }
    """
    rows = list(games)
    game_count = len(rows)
    if game_count < MIN_STYLE_GAMES:
        return {
            "sufficient": False,
            "game_count": game_count,
            "weighted_game_count": 0.0,
            "weighted_with_tc": 0.0,
            "distribution": None,
            "most_common": None,
        }

    import time as _time

    now_unix = _time.time()
    weighted_game_count = 0.0
    weighted_with_tc = 0.0
    bucket_weight: Dict[str, float] = {}

    for row in rows:
        end_time = int(row.get("end_time") or 0)
        pgn = row.get("pgn") or ""
        weight = _game_recency_weight(end_time, now_unix)
        weighted_game_count += weight

        tc_header = _time_control_header(pgn)
        tc_label = _time_control_label(tc_header)
        if tc_label is None:
            continue

        weighted_with_tc += weight
        bucket_weight[tc_label] = bucket_weight.get(tc_label, 0.0) + weight

    if weighted_with_tc <= 0.0 or not bucket_weight:
        return {
            "sufficient": True,
            "game_count": game_count,
            "weighted_game_count": round(weighted_game_count, 4),
            "weighted_with_tc": 0.0,
            "distribution": None,
            "most_common": None,
        }

    # Normalize to fractions (sum ~1.0) over the games that had a TC header.
    distribution_full: Dict[str, float] = {
        bucket: w / weighted_with_tc for bucket, w in bucket_weight.items()
    }
    # Keep the top-N individual buckets by weight; fold the rest into
    # "Other". The single most common INDIVIDUAL bucket is, by definition,
    # the top-ranked of these N — so we can read most_common off `top`
    # before "Other" is added, and "Other" can never win that rank.
    ranked = sorted(
        distribution_full.items(), key=lambda kv: kv[1], reverse=True
    )
    top = ranked[:_TOP_TIME_CONTROL_BUCKETS]
    rest = ranked[_TOP_TIME_CONTROL_BUCKETS:]
    most_common = top[0][0]
    distribution: Dict[str, float] = {
        bucket: round(frac, 4) for bucket, frac in top
    }
    # Re-sort the named (non-"Other") buckets by descending weight for
    # caller convenience + readability, matching the sort the existing
    # opening_family_lean / castling_side_distribution use.
    distribution = dict(
        sorted(distribution.items(), key=lambda kv: kv[1], reverse=True)
    )
    other_frac = sum(frac for _, frac in rest)
    if other_frac > 0.0:
        # "Other" always last, regardless of its summed weight, so the
        # named buckets stay in their ranked order and a caller scanning
        # the dict sees the real preferred controls first.
        distribution["Other"] = round(other_frac, 4)

    return {
        "sufficient": True,
        "game_count": game_count,
        "weighted_game_count": round(weighted_game_count, 4),
        "weighted_with_tc": round(weighted_with_tc, 4),
        "distribution": distribution,
        "most_common": most_common,
    }


# --- per-opening win/loss/draw breakdown (Opponent Prep "Openings He Lost") -
#
# The Train page's "Most Played Openings" view already shows opening
# FREQUENCY via `opening_family_lean`. The Opponent Preparation page needs
# a SECOND view of the SAME buckets: the opponent's actual result in each
# one — for the "Openings He Lost Against" panel. The contract (the spec):
#
#   * Reuse the EXACT same opening-bucketing logic as opening_family_lean
#     — same `_opening_family` extractor, same bins. Do NOT reimplement or
#     fork the binning. We achieve this by parsing each PGN through the
#     existing `_analyze_game` and reading its already-computed `family`
#     + the new `result` field — the same single python-chess parse the
#     other signals use, no second parse, no second extractor call path.
#
#   * For each bucket: win/loss/draw WEIGHTED counts and a win-rate % for
#     the OPPONENT in that opening, weighted by the SAME recency decay
#     already used everywhere else in this module
#     (`STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR` via `_game_recency_weight`).
#
#   * NO minimum-sample floor (the spec: "show every bucket with at least
#     one game, however small. This is deliberate, don't add filtering").
#     A 1-game opponent with a lone loss in the Sicilian WILL show a
#     Sicilian bucket with win_rate 0.0 — this is signal, not noise, for
#     a preparation page whose explicit purpose is "Openings He Lost
#     Against". This intentionally diverges from compute_opponent_style /
#     compute_time_control_distribution, which both gate on
#     MIN_STYLE_GAMES.
#
# DENOMINATOR / COUNT CONTRACT:
#   * `weighted_count` of each bucket = sum of that bucket's per-game
#     recency weights (same unit as `weighted_game_count` elsewhere).
#   * `win_rate` = weighted_wins / (weighted_wins + weighted_losses +
#     weighted_draws). Drawn games are in the DENOMINATOR (a draw is a
#     game played, not a win) but not the numerator (a draw is not a win).
#   * Games with result "*" (unfinished/aborted) contribute to NONE of
#     the three W/L/D weighted counts AND are excluded from the win_rate
#     denominator — an unfinished game tells us nothing about whether
#     the opponent tends to win or lose in that opening. They DO still
#     count toward the bucket's weighted_count (the game WAS played, we
#     have it on file), so weighted_count is the honest "how many of
#     this opponent's games were in this opening" while the W/L/D counts
#     sum to <= weighted_count by exactly the weighted "*" mass.
#   * Unparseable PGNs (and games where the opponent's color can't be
#     resolved) are excluded from every count here, matching the
#     denominator-consistency contract the other signals use.
def compute_opening_results(games) -> Dict[str, Any]:
    """Per-opening W/L/D breakdown for one opponent, weighted by recency.

    `games` is an iterable of dicts each with keys {"pgn", "end_time"} —
    the same row shape `compute_opponent_style` and
    `compute_time_control_distribution` read. Each PGN is parsed through
    the existing `_analyze_game` (the one `compute_opponent_style` uses
    for every other signal), so the `family` label assigned here is the
    EXACT label `opening_family_lean` assigns — no forked/reimplemented
    binning. The new `result` field `_analyze_game` now returns is
    consumed here; nothing else in `_analyze_game` was changed to serve
    this signal.

    NO minimum-sample floor: EVERY bucket with at least one parseable
    game is reported, however small (the spec is explicit — do not add
    filtering). A 1-game single-loss bucket shows `win_rate=0.0`; that's
    signal, not noise, for a "Lost Against" panel.

    Returns a dict shaped:
        {
          "game_count": int,                # raw count of input rows
          "weighted_game_count": float,     # sum of ALL per-game weights
                                             #   (incl. unparseable PGNs)
          "weighted_parseable_game_count": float,  # sum of weights for
                                                    #   parseable games;
                                                    #   denominator of
                                                    #   weighted_count
                                                    #   across buckets
          # Cross-game sacrifice frequency (recency-weighted). Sourced
          # from the same per-game loop that builds by_opening — no
          # second PGN parse, no second `_analyze_game` call. None iff
          # the corpus had zero opponent moves (empty corpus / every
          # game unparseable). The frontend uses this to derive a
          # playing-style pill on the Opponent Preparation page.
          "weighted_sacrifice_frequency": float | None,
          "by_opening": dict,                # {family: {
                                             #   "weighted_count": float,
                                             #   "weighted_wins":   float,
                                             #   "weighted_losses": float,
                                             #   "weighted_draws":  float,
                                             #   "win_rate":        float|None,
                                             # }} — win_rate is None iff
                                             # the bucket has zero
                                             # decided/drawn games (every
                                             # game in it was "*" aborted
                                             # OR the bucket is empty).
                                             # Sorted by descending
                                             # weighted_count so the
                                             # "Openings He Lost Against"
                                             # panel's most-played-first
                                             # ordering matches the
                                             # frequency panel's.
        }

    `by_opening` keys are the SAME family labels `opening_family_lean`
    returns — so the Opponent Preparation page's frequency and results
    views of the same opponent come back from one endpoint call
    (list_opponent_profiles) and zip together by key without a remap.
    """
    rows = list(games)
    game_count = len(rows)

    import time as _time

    now_unix = _time.time()
    weighted_game_count = 0.0
    weighted_parseable_game_count = 0.0
    # Cross-game style aggregates surfaced as top-level keys on the
    # response (separate from the per-bucket by_opening shape). Free
    # piggyback on the existing game loop — `_analyze_game` already
    # returns `sacrifices` and `opponent_moves` per game, so we sum the
    # weighted counts here without a second pass. The frontend reads
    # `weighted_sacrifice_frequency` to derive a "playing style" pill
    # (Passive / Balanced / Aggressive).
    weighted_sacrifices = 0.0
    weighted_opponent_moves = 0.0

    # Per-bucket accumulators. The shape mirrors the by_opening entry shape
    # (without win_rate, which is derived at the end) so the final assembly
    # is a 1:1 copy-out, not a transform.
    bucket_weight: Dict[str, float] = {}
    bucket_wins: Dict[str, float] = {}
    bucket_losses: Dict[str, float] = {}
    bucket_draws: Dict[str, float] = {}

    for row in rows:
        end_time = int(row.get("end_time") or 0)
        pgn = row.get("pgn") or ""
        weight = _game_recency_weight(end_time, now_unix)

        analyzed = _analyze_game(pgn, _normalize_username_for_result(row, pgn))
        if analyzed is None:
            weighted_game_count += weight
            continue

        weighted_game_count += weight
        weighted_parseable_game_count += weight
        # Piggyback: each game contributes its weighted sac count and
        # weighted opponent-move count. Denominator-guarded below (avoid
        # ZeroDivision when the corpus is empty / all unparseable).
        weighted_sacrifices += weight * float(analyzed.get("sacrifices", 0))
        weighted_opponent_moves += weight * float(
            analyzed.get("opponent_moves", 0)
        )

        family = analyzed["family"]
        result = analyzed["result"]

        bucket_weight[family] = bucket_weight.get(family, 0.0) + weight
        # "*" (unfinished/aborted) contributes to the bucket's
        # weighted_count only — it's in none of the W/L/D numerators and
        # excluded from win_rate's denominator. See the DENOMINATOR /
        # COUNT CONTRACT above.
        if result == "win":
            bucket_wins[family] = bucket_wins.get(family, 0.0) + weight
        elif result == "loss":
            bucket_losses[family] = bucket_losses.get(family, 0.0) + weight
        elif result == "draw":
            bucket_draws[family] = bucket_draws.get(family, 0.0) + weight
        # result == "*" -> no W/L/D accumulator touch.

    by_opening: Dict[str, Dict[str, Any]] = {}
    for family in bucket_weight:
        w_count = bucket_weight[family]
        w_wins = bucket_wins.get(family, 0.0)
        w_losses = bucket_losses.get(family, 0.0)
        w_draws = bucket_draws.get(family, 0.0)
        decided_or_drawn = w_wins + w_losses + w_draws
        if decided_or_drawn > 0.0:
            win_rate = w_wins / decided_or_drawn
        else:
            # Every game in this bucket was "*" (unfinished/aborted). The
            # bucket is real (weighted_count > 0) but we have no result
            # signal — None is the honest "absent", not 0.0-as-signal
            # (0.0 would imply "the opponent loses every game here" when
            # the truth is "we don't know"). Distinct from the
            # sufficient-floor None of compute_opponent_style per the
            # denominator-consistency docstring contract.
            win_rate = None
        by_opening[family] = {
            "weighted_count": round(w_count, 4),
            "weighted_wins": round(w_wins, 4),
            "weighted_losses": round(w_losses, 4),
            "weighted_draws": round(w_draws, 4),
            "win_rate": (round(win_rate, 4) if win_rate is not None else None),
        }

    # Sort by descending weighted_count so the "Openings He Lost Against"
    # panel's most-played-first ordering matches the frequency panel's
    # (opening_family_lean also sorts by descending weight). Stable sort
    # preserves insertion order for ties, which on equal-weight buckets
    # means most-recent-game-first (rows iterate in the SQL's end_time
    # DESC order for the live caller; our test fixtures insert in a
    # stable order too).
    by_opening = dict(
        sorted(
            by_opening.items(),
            key=lambda kv: kv[1]["weighted_count"],
            reverse=True,
        )
    )

    # Cross-game sacrifice frequency. None when there were no opponent
    # moves at all (defensive — the existing games-loop never increments
    # the denominator for an opponent's non-moves, so this only triggers
    # for an empty corpus or one where every parseable game had zero
    # opponent plies, both impossible in real data but cheap to guard).
    if weighted_opponent_moves > 0.0:
        weighted_sacrifice_frequency = round(
            weighted_sacrifices / weighted_opponent_moves, 4
        )
    else:
        weighted_sacrifice_frequency = None

    return {
        "game_count": game_count,
        "weighted_game_count": round(weighted_game_count, 4),
        "weighted_parseable_game_count": round(weighted_parseable_game_count, 4),
        "by_opening": by_opening,
        "weighted_sacrifice_frequency": weighted_sacrifice_frequency,
    }


def _normalize_username_for_result(row: Dict[str, Any], pgn: str) -> str:
    """Whose-result-is-this resolver for compute_opening_results.

    `compute_opening_results` takes raw {pgn, end_time, ...} rows (the
    shape `list_opponent_profiles` already aggregates), so it doesn't
    have a pre-resolved `opponent_username` to hand `_analyze_game` the
    way `compute_opponent_style` does. The opponent's username is encoded
    in the PGN's `[White]`/`[Black]` headers, and `_opponent_color` (which
    `_analyze_game` calls) compares the casefolded name against them.

    We surface the name from the row if the caller put it there (the live
    endpoint path through `list_opponent_profiles` does); the name is
    normalized via `_normalize_username` to match the contract
    `_opponent_color` expects (a casefolded name, the same
    `compute_opponent_style` produces via `_normalize_username` before
    calling `_analyze_game`). Without this normalization the comparison
    against the already-casefolded PGN header values would silently
    mismatch on any mixed-case username. Returns "" if the caller didn't
    set it, so `_opponent_color`'s existing ambiguity handling drops the
    game. The live path always sets it, so this is a pure plumbing
    helper, not a heuristic.
    """
    name = row.get("opponent_username")
    if not name:
        return ""
    return _normalize_username(str(name))


def compute_opponent_style(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    sparring_time_control: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute recency+time-control-weighted aggregate style signals.

    Reads the opponent's imported games from `opponent_games`, parses each
    PGN, and aggregates FIVE signals with per-game weighting applied. The
    per-game weight is the PRODUCT of two factors:

      * RECENCY:    exp(-lambda * age_in_years)  (_game_recency_weight)
      * TIME-CONTROL SIMILARITY: _game_tc_weight(game_bucket, sparring_bucket)
        -- 1.0 for same-TC games, down-weighted (0.1-0.7) for cross-TC,
        1.0 (neutral) when either the game's or the sparring session's
        time control is "unknown" (don't penalise what you can't classify).

    So same-TC recent games dominate the aggregates; old cross-TC games act
    as a down-weighted soft prior (NOT purged). When `sparring_time_control`
    is None (or unbucketable), the TC factor collapses to 1.0 and the
    weighting is recency-only -- exactly the pre-TC behaviour, so existing
    callers are unaffected.

    `sparring_time_control` is the OPTIONAL time-control string of the
    current sparring session: a canonical bucket label ("bullet"/"blitz"/
    "rapid"/"classical"), an "M+I" label ("3+2"), or a raw "base+inc"
    seconds string. Bucketed via `_time_control_bucket`. When the
    opponent's per-game `time_class` DB column is missing, the PGN
    `[TimeControl]` header is the fallback bucket source.

    Aggregates that receive the FULL TC weighting: every per-game signal
    computed here (sac frequency, queen-trade, setup, castle, game length,
    opening family) and the effective-sample-size gate. The companion
    `opponent_traps.compute_exploitable_traps` applies the same TC
    similarity (moderate weighting for blunder patterns); the repertoire
    sampler (`opponent_repertoire.pick_repertoire_move`) intentionally
    applies NO TC weighting -- openings transfer across TCs better than
    tactical style does (see that module's docstring).

      1. sacrifice_frequency      — weighted sacs / weighted opponent-moves.
      2. opening_family_lean      — {family: weighted_fraction}.
      3. average_game_length       — weighted mean of total plies per game
                                     (half-moves).
      4. castling_side_distribution — {kingside, queenside, never} each as
                                     a weighted fraction of games; sums
                                     to ~1.0.
      5. queen_trade_timing, as two SEPARATE keys (NOT collapsed):
         - queen_trade_move_number  — weighted mean of the last-queen-
                                      capture PLY over games where both
                                      queens left the board; None if no
                                      game qualified (separate from the
                                      sufficient-floor None).
         - queens_stay_on_rate      — weighted fraction of games where
                                      BOTH queens were still on the board
                                      at game end.

    Returns a dict shaped:
        {
          "sufficient": bool,            # floor check (effective_sample_size
                                          #   >= MIN_STYLE_EFF_SAMPLES).
                                          #   effective_sample_size is now
                                          #   the sum of per-game COMBINED
                                          #   (recency x TC) weights, so a
                                          #   cross-TC-only corpus under a
                                          #   mismatched sparring TC clears
                                          #   the gate only if it has enough
                                          #   same-TC (or unknown-TC) mass.
          "game_count": int,             # raw count, for transparency
          "effective_sample_size": float, # sum of per-game COMBINED weights
                                           #   (recency x TC). The gate metric.
                                           #   Under decay+TC this is <= the
                                           #   recency-only eff sample; the
                                           #   gap quantifies staleness +
                                           #   cross-TC mismatch.
          "recency_decay_lambda": float, # the recency lambda used (1.0)
          "sparring_time_control_bucket": str | None,  # the resolved sparring
                                          #   TC bucket (bullet/blitz/rapid/
                                          #   classical), or None when no
                                          #   sparring TC was supplied.
          "time_control_weighted_by_bucket": dict | None,  # {game_bucket:
                                          #   sum of combined weights} for
                                          #   parseable games. Verifies that
                                          #   same-TC games are being
                                          #   preferred (the dominant bucket
                                          #   should match the sparring one).
          "time_control_unclassified_count": int,  # raw count of games whose
                                          #   TC bucket was "unknown" --
                                          #   flags how much of the corpus is
                                          #   unclassified (each such game
                                          #   got the neutral 1.0 weight).
          "weighted_game_count": float,  # sum of ALL per-game COMBINED
                                         #   weights (incl. unparseable PGNs)
          "weighted_parseable_game_count": float,  # sum of COMBINED weights for
                                                    #   parseable games only.
                                                    #   Denominator for
                                                    #   average_game_length
                                                    #   and queens_stay_on_rate.
          "sacrifice_frequency": float | None,
          "opening_family_lean": dict | None,
          "average_game_length": float | None,
          "castling_side_distribution": dict | None,
          "queen_trade_move_number": float | None,
          "queens_stay_on_rate": float | None,
          "setup_signatures": List[Dict] | None,  # per-game POV-normalized
                                                  #   pawn + piece snapshots
                                                  #   captured in the
                                                  #   [SETUP_SIGNATURE_PLY_MIN,
                                                  #    SETUP_SIGNATURE_PLY_MAX]
                                                  #   window; each snapshot
                                                  #   tagged with its game's
                                                  #   COMBINED recency x TC
                                                  #   "weight" so the reranker
                                                  #   can weight the Jaccard by
                                                  #   recency+TC. None iff below
                                                  #   SETUP_SIGNATURE_MIN_GAMES
                                                  #   OR no snapshots captured.
          "sacrifice_events": int,       # raw total, transparency/debug
          "opponent_moves": int,         # raw total, transparency/debug
        }

    `sufficient=False` is the signal to the caller to fall through to
    default Maia behaviour and not apply a style bias — same contract
    `pick_repertoire_move` exits with via the per-position floor. When
    `sufficient=False`, every per-signal key (sacrifice_frequency,
    opening_family_lean, average_game_length,
    castling_side_distribution, queen_trade_move_number,
    queens_stay_on_rate) is None, mirroring the existing two-signal
    behavior.

    DENOMINATOR CONSISTENCY: all five aggregate signals exclude
    unparseable PGNs from their denominators. An unparseable PGN is a
    data-quality failure, not a game with length 0 / no castling /
    queens-still-on — including it would dilute the signals toward zero
    and conflate "the opponent plays short games" with "we couldn't read
    20% of their PGNs". Each signal uses the denominator natural to its
    own unit:
      * sacrifice_frequency:       weighted_opponent_moves (per-move rate)
      * opening_family_lean:       total_family_weight (per-family fraction)
      * castling_side_distribution: total_castling_weight (per-game fraction)
      * average_game_length:       weighted_parseable_game_count (per-game mean)
      * queens_stay_on_rate:       weighted_parseable_game_count (per-game rate)
      * queen_trade_move_number:   weighted_queen_trade_games (qualifying-games mean)
    The first three already excluded unparseable games by construction
    (their accumulators only increment in the post-continue block); the
    last three were brought into consistency by switching their
    denominators from weighted_game_count to weighted_parseable_game_count
    (or, for queen_trade_move_number, to its own signal-specific
    weighted_queen_trade_games which was already parseable-only). The gap
    between weighted_game_count and weighted_parseable_game_count
    quantifies the data-quality loss for the caller.

    Two subtle None-vs-0.0 distinctions the caller should know:
      * `queen_trade_move_number` returns None when sufficient=True BUT no
        game in the sample had both queens leave the board (i.e. every
        game ended with at least one queen on, or the asymmetric one-on
        case). This is distinct from "all games traded queens at ply
        zero" (which would be 0.0, not None) and from "below floor"
        (also None, but for a different reason). The distinction matters
        for callers that audit whether the signal is "absent because no
        data" vs "absent because sub-floor".
      * The other new signals (average_game_length,
        castling_side_distribution, queens_stay_on_rate) are never None
        when sufficient=True; if every PGN were unparseable (defensive
        edge case) they fall back to 0.0 / {} / 0.0 respectively, the
        same way `sacrifice_frequency` falls back to 0.0 when the
        opponent-move denominator is zero.

    Raises nothing from the database layer; a missing pool surfaces as a
    RuntimeError, matching `opponent_repertoire.py`'s contract.
    """
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    normalized_opponent = _normalize_username(opponent_username)
    # Resolve the sparring session's time-control bucket ONCE. When it is
    # None/unbucketable, _game_tc_weight returns 1.0 for every game and the
    # combined weight collapses to recency-only (existing callers unaffected).
    sparring_bucket = _time_control_bucket(sparring_time_control)
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT pgn, end_time, time_class
                FROM opponent_games
                WHERE requested_by_user_id = %s
                  AND provider = %s
                  AND LOWER(opponent_username) = LOWER(%s)
                ORDER BY end_time DESC, imported_at DESC
                """,
                (requested_by_user_id, provider, opponent_username),
            )
            rows = [dict(row) for row in cur.fetchall()]
    finally:
        database.connection_pool.putconn(conn)

    game_count = len(rows)

    # --- effective sample size pre-pass (cheap, no PGN parsing) --------------
    # Compute the sum of per-game COMBINED (recency x TC) weights BEFORE the
    # expensive PGN-parsing loop. This is the gate metric: under exponential
    # decay (lambda=1.0) the recency factor is always <= 1.0, and the TC
    # similarity factor is <= 1.0 for cross-TC games (1.0 for same-TC /
    # unknown), so effective_sample_size is always <= raw game count AND <=
    # the recency-only eff sample. A stale-but-voluminous opponent (e.g. 500
    # four-year-old games) or a cross-TC-heavy corpus under a mismatched
    # sparring TC both have a much lower effective_sample_size than a recent
    # same-TC opponent with the same raw count. The gate distinguishes
    # "recent and same-TC and enough" from "stale / cross-TC and voluminous"
    # — the spec's explicit requirement, extended to the time-control axis.
    #
    # This pre-pass only computes math.exp() + a dict lookup per row (no PGN
    # parsing), so it is cheap (microseconds per game). The expensive
    # _analyze_game calls only happen after the gate passes.
    import time as _time

    now_unix = _time.time()
    effective_sample_size = 0.0
    # Transparency: how many games could not be bucketed (each got the
    # neutral 1.0 TC weight -- flagged so the caller can see how much of
    # the corpus is unclassified), and the combined-weight mass per bucket
    # (verifies same-TC dominance).
    tc_unclassified_count = 0
    tc_weight_by_bucket: Dict[str, float] = {}
    for row in rows:
        end_time = int(row.get("end_time") or 0)
        game_bucket = _time_control_bucket(
            row.get("time_class"), row.get("pgn") or ""
        )
        if game_bucket == "unknown":
            tc_unclassified_count += 1
        weight = _game_recency_weight(end_time, now_unix) * _game_tc_weight(
            game_bucket, sparring_bucket
        )
        effective_sample_size += weight
        tc_weight_by_bucket[game_bucket] = (
            tc_weight_by_bucket.get(game_bucket, 0.0) + weight
        )

    # --- floor check (effective sample size, not raw count) ------------------
    # The PRIMARY gate is now on effective_sample_size (sum of per-game
    # COMBINED recency x TC weights), not raw game count. This is stricter
    # for stale opponents AND for cross-TC-heavy corpora under a mismatched
    # sparring TC: a 500-game opponent with all 4-year-old games has a
    # recency-only eff sample ~= 9.1 (barely clears), but under a rapid
    # sparring session where all those games are bullet, each game's weight
    # is further scaled by 0.2 (the rapid<->bullet similarity), dropping the
    # combined eff sample to ~1.8 (fails). A recent same-TC opponent with
    # the same raw count has ~= 460 (trivially clears). The gate thus
    # distinguishes "recent + same-TC + enough" from "stale / cross-TC +
    # voluminous" — the spec's explicit requirement, on both axes.
    sufficient = effective_sample_size >= MIN_STYLE_EFF_SAMPLES

    if not sufficient or game_count == 0:
        log.debug(
            "compute_opponent_style: insufficient data for %s/%s — "
            "game_count=%d, effective_sample_size=%.2f (floor=%.1f), "
            "lambda=%.2f, sparring_tc_bucket=%s, unclassified=%d, "
            "falling through to default Maia",
            provider, opponent_username, game_count,
            effective_sample_size, MIN_STYLE_EFF_SAMPLES,
            STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR,
            sparring_bucket, tc_unclassified_count,
        )
        return {
            "sufficient": sufficient,  # False (or False via game_count=0)
            "game_count": game_count,
            "effective_sample_size": round(effective_sample_size, 4),
            "recency_decay_lambda": STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR,
            "sparring_time_control_bucket": (
                sparring_bucket if sparring_bucket != "unknown" else None
            ),
            "time_control_weighted_by_bucket": None,
            "time_control_unclassified_count": tc_unclassified_count,
            "weighted_game_count": 0.0,
            "weighted_parseable_game_count": 0.0,
            "sacrifice_frequency": None,
            "opening_family_lean": None,
            "average_game_length": None,
            "castling_side_distribution": None,
            "queen_trade_move_number": None,
            "queens_stay_on_rate": None,
            "setup_signatures": None,
            "sacrifice_events": 0,
            "opponent_moves": 0,
        }

    log.debug(
        "compute_opponent_style: sufficient for %s/%s — "
        "game_count=%d, effective_sample_size=%.2f (floor=%.1f), "
        "lambda=%.2f, sparring_tc_bucket=%s, unclassified=%d, "
        "recency+TC weighting ACTIVE",
        provider, opponent_username, game_count,
        effective_sample_size, MIN_STYLE_EFF_SAMPLES,
        STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR,
        sparring_bucket, tc_unclassified_count,
    )

    # --- aggregate ------------------------------------------------------------
    # Per-game COMBINED weights
    #   w_g = _game_tc_weight(game_bucket, sparring_bucket)
    #           * exp(-lambda * age_years),   end_time=0 -> recency 1.0.
    # now_unix was already computed in the pre-pass above; reusing it here
    # keeps the gate and the aggregate on the same time anchor. The TC
    # factor is the SAME one the pre-pass applied, so the gate and the
    # aggregate stay consistent (a corpus that clears the gate has the
    # combined weight mass the aggregates then distribute).
    # weighted_game_count: sum of ALL per-game COMBINED weights, including
    # games whose PGNs we couldn't parse. This is the "how much data do we
    # have on file" transparency field — it always >=
    # weighted_parseable_game_count.
    weighted_game_count = 0.0
    # weighted_parseable_game_count: sum of weights for games whose PGNs
    # we successfully parsed. This is the denominator for every per-game
    # aggregate signal (average_game_length, queens_stay_on_rate). It
    # excludes unparseable games so they don't dilute the signals toward
    # zero — see the denominator-consistency docstring below. The three
    # per-move / per-family / per-castling signals (sacrifice_frequency,
    # opening_family_lean, castling_side_distribution) use their own
    # signal-specific accumulators (which also exclude unparseable games
    # by construction), so all five signals share the same "exclude
    # unparseable" denominator philosophy while each using the denominator
    # natural to its own unit (per-opponent-move, per-family, per-game).
    weighted_parseable_game_count = 0.0
    weighted_sacrifices = 0.0
    weighted_opponent_moves = 0.0
    family_weight: Dict[str, float] = {}
    raw_sacrifices = 0
    raw_opponent_moves = 0

    # New-signal accumulators.
    weighted_plies = 0.0
    castling_side_weight: Dict[str, float] = {
        "kingside": 0.0,
        "queenside": 0.0,
        "never": 0.0,
    }
    # queen-trade-timing (a): only games where BOTH queens left the board
    # contribute. We sum (weight * last_queen_capture_ply) and the weight
    # sum in parallel, then divide.
    weighted_queen_trade_plies = 0.0
    weighted_queen_trade_games = 0.0
    # queen-trade-timing (b): the symmetric "both queens on at end" rate.
    weighted_queens_on_games = 0.0

    # setup-structure snapshots: aggregated across parseable games. Each
    # snapshot is the dict from _pov_snapshot_squares + `snapshot_ply`. We
    # collect them in insertion order so end_time DESC ROW ORDER from the
    # SQL means snapshots are roughly most-recent-first; if we blow past
    # SNAPSHOTS_MAX_PER_OPPONENT we trim the tail (oldest snapshots in
    # this list ~ oldest games, since the SQL orders end_time DESC).
    aggregated_setup_snapshots: List[Dict[str, Any]] = []

    for row in rows:
        end_time = int(row.get("end_time") or 0)
        pgn = row.get("pgn") or ""
        # COMBINED per-game weight: recency x TC-similarity. The game's TC
        # bucket is resolved from the DB `time_class` column (primary) with
        # the PGN `[TimeControl]` header as fallback. _game_tc_weight
        # returns 1.0 when either bucket is "unknown" (don't penalise what
        # you can't classify), so an unbucketable sparring session or an
        # unbucketable game collapses to recency-only weighting. The
        # pre-pass already computed this same combined weight for the gate;
        # recomputing here is cheaper than carrying a per-row weight list
        # and keeps the loop body self-documenting.
        game_bucket = _time_control_bucket(row.get("time_class"), pgn)
        weight = _game_recency_weight(end_time, now_unix) * _game_tc_weight(
            game_bucket, sparring_bucket
        )

        analyzed = _analyze_game(pgn, normalized_opponent)
        if analyzed is None:
            # PGN unparseable or opponent color unresolved. The game
            # still counts toward game_count (we have it on file) and
            # toward weighted_game_count (we DID see it, recently enough
            # to matter), but contributes nothing to weighted_parseable_
            # game_count or any signal accumulator. This is the
            # denominator-consistency contract: unparseable games are
            # excluded from every signal's denominator so they don't
            # dilute the signals toward zero. The gap between
            # weighted_game_count and weighted_parseable_game_count
            # quantifies the data-quality loss for the caller.
            weighted_game_count += weight
            continue

        raw_sacrifices += analyzed["sacrifices"]
        raw_opponent_moves += analyzed["opponent_moves"]
        weighted_sacrifices += weight * analyzed["sacrifices"]
        weighted_opponent_moves += weight * analyzed["opponent_moves"]

        family = analyzed["family"]
        family_weight[family] = family_weight.get(family, 0.0) + weight

        weighted_plies += weight * analyzed["plies"]
        castling_side_weight[analyzed["opp_castled"]] += weight

        # setup-signature snapshots: each snapshot is tagged with the
        # game's COMBINED recency x TC weight so the reranker can weight
        # the Jaccard similarity by recency+TC (recent same-TC setups
        # dominate, old cross-TC setups act as a soft prior — the same
        # philosophy as the other aggregate signals). This EXTENDS the
        # existing "weight" field rather than adding a second field, so
        # the reranker reads the single snap["weight"] it already consumes
        # (opponent_style_reranker._setup_similarity) and automatically
        # benefits from the combined weighting with NO reranker change.
        # Tag each snapshot with the game's opening family and the
        # profiled player's color in this game, so the reranker can
        # filter by family + color before computing setup_mult. Without
        # this filter, a Scandinavian position would match against
        # Italian/Scotch snapshots (the dominant majority), drowning the
        # 7 Scandinavian games' signal in 500+ non-Scandinavian ones.
        game_family = analyzed["family"]
        game_color = analyzed["opponent_color"]
        for snap in analyzed["setup_snapshots"]:
            snap["family"] = game_family
            snap["player_color"] = game_color
            snap["weight"] = weight
        aggregated_setup_snapshots.extend(analyzed["setup_snapshots"])

        if analyzed["queens_on_at_end"]:
            weighted_queens_on_games += weight
        elif analyzed["queens_off_at_end"]:
            lqcp = analyzed["last_queen_capture_ply"]
            if lqcp is not None:
                weighted_queen_trade_plies += weight * lqcp
                weighted_queen_trade_games += weight

        weighted_game_count += weight
        weighted_parseable_game_count += weight

    # --- sacrifice frequency (weighted rate) --------------------------------
    # Weighted sacs / weighted opponent-moves. If a player has many games
    # but very few opponent moves in each (short aborts, time forfeits
    # before move 10), the denominator can still be tiny — guard it.
    if weighted_opponent_moves > 0.0:
        sacrifice_frequency = weighted_sacrifices / weighted_opponent_moves
    else:
        # Sufficiency was met on raw game count, but every parseable game
        # had zero opponent moves (impossible for a real game, but possible
        # if all PGNs were malformed and `analyzed` always returned None).
        # Report 0.0 rather than None so the caller sees "sufficient but
        # empty"; the recency weighting does not invent moves that aren't
        # there.
        sacrifice_frequency = 0.0

    # --- opening family lean (weighted distribution) -------------------------
    # Each game contributes its weight to exactly one family bin.
    # Apart from floating-point drift, family_weight sums to weighted_game_count.
    # We normalize by the total so the distribution sums to ~1.0 and is
    # directly comparable to "this share of the opponent's recent games
    # are Sicilians".
    total_family_weight = sum(family_weight.values())
    if total_family_weight > 0.0:
        opening_family_lean = {
            family: round(w / total_family_weight, 4)
            for family, w in family_weight.items()
        }
        # Sort by descending weight for caller convenience + readability.
        opening_family_lean = dict(
            sorted(opening_family_lean.items(), key=lambda kv: kv[1], reverse=True)
        )
    else:
        opening_family_lean = {}

    # --- average game length (weighted mean of plies) ------------------------
    # Half-moves (plies) is the unit python-chess's `mainline_moves()`
    # yields; we use it consistently with the queen-trade-move-number ply
    # so the two stats share a scale (a queen trade at "ply 28" is on the
    # same timeline as an average game length of "40 plies").
    #
    # DENOMINATOR CONSISTENCY: the denominator is weighted_parseable_game_count
    # (sum of weights for games we could parse), NOT weighted_game_count
    # (which includes unparseable PGNs). An unparseable PGN is a data-
    # quality failure, not a game with known length 0 — including it in
    # the denominator would drag the mean toward zero and conflate "the
    # opponent plays short games" with "we couldn't read 20% of their
    # PGNs". The three other aggregate signals (sacrifice_frequency,
    # opening_family_lean, castling_side_distribution) also exclude
    # unparseable games from their denominators (by construction — their
    # accumulators only increment in the post-continue block), so this
    # change brings average_game_length into consistency with them.
    # Data-quality transparency is served by the gap between
    # weighted_game_count and weighted_parseable_game_count, which the
    # caller can inspect.
    if weighted_parseable_game_count > 0.0:
        average_game_length = weighted_plies / weighted_parseable_game_count
    else:
        # All games were unparseable (sufficient=True via raw count,
        # but zero parseable PGNs). Fall back to 0.0 rather than None,
        # matching sacrifice_frequency's 0.0 fallback when the
        # opponent-move denominator is zero.
        average_game_length = 0.0

    # --- castling side distribution (3-way weighted fractions) ---------------
    # We always surface all three bins (kingside / queenside / never),
    # unlike opening_family_lean which drops unobserved families. Rationale:
    # the castling domain is fixed at exactly 3 mutually-exclusive outcomes
    # for any game, so a caller benefitting from "queenside": 0.0 (vs the
    # key being absent) is the more honest representation of "we have N
    # games and saw zero queenside castles among them". A 0.0 entry is
    # evidence; a missing key could be "not computed".
    total_castling_weight = sum(castling_side_weight.values())
    if total_castling_weight > 0.0:
        castling_side_distribution = {
            side: round(w / total_castling_weight, 4)
            for side, w in castling_side_weight.items()
        }
        # Sort by descending weight for caller convenience + readability
        # (matches the opening_family_lean sort).
        castling_side_distribution = dict(
            sorted(
                castling_side_distribution.items(),
                key=lambda kv: kv[1],
                reverse=True,
            )
        )
    else:
        castling_side_distribution = {}

    # --- queen-trade timing (a): weighted mean of last-queen-capture ply ------
    # Only games where BOTH queens left the board contribute (see
    # `_analyze_game` for the eligibility flag). If no game qualified,
    # return None rather than 0.0 — 0.0 would imply "queens traded at
    # move zero", which is never true; None correctly signals "no
    # qualifying games in this opponent's sample".
    if weighted_queen_trade_games > 0.0:
        queen_trade_move_number = (
            weighted_queen_trade_plies / weighted_queen_trade_games
        )
    else:
        queen_trade_move_number = None

    # --- queen-trade timing (b): weighted fraction of "queens on at end" ------
    # Symmetric-by-construction complement of (a)'s eligibility set, but
    # reported as a standalone rate (NOT "1 - queens_stay_on_rate" — the
    # asymmetric middle case is excluded from BOTH signals, so the two
    # don't sum to 1.0 in general).
    #
    # DENOMINATOR CONSISTENCY: uses weighted_parseable_game_count, NOT
    # weighted_game_count — same philosophy as average_game_length above.
    # An unparseable PGN can't tell us whether queens were on the board
    # at game end, so including it in the denominator (as the previous
    # version did, dividing by weighted_game_count) would falsely claim
    # "queens stayed on in 0% of games" when the truth is "we couldn't
    # tell for N% of games". The gap between weighted_game_count and
    # weighted_parseable_game_count quantifies the data-quality loss.
    if weighted_parseable_game_count > 0.0:
        queens_stay_on_rate = weighted_queens_on_games / weighted_parseable_game_count
    else:
        # All games unparseable — defensive fallback, same as
        # average_game_length and sacrifice_frequency above.
        queens_stay_on_rate = 0.0

    # --- setup-structure signatures ------------------------------------------
    # Aggregated across parseable games. Defensive cap: if an opponent has
    # many games, trim to the most-recent (rows came back end_time DESC so
    # `aggregated_setup_snapshots` is roughly most-recent-first -- a
    # snapshot from a given game is appended after the previous game's
    # snapshots in the row order, and end_time DESC at the SQL preserves
    # that recency alpha). The cap is a transport-size bound, not a signal
    # filter; ~250 snapshots is well past noise floor for Jaccard max.
    if len(aggregated_setup_snapshots) > SNAPSHOTS_MAX_PER_OPPONENT:
        aggregated_setup_snapshots = aggregated_setup_snapshots[
            :SNAPSHOTS_MAX_PER_OPPONENT
        ]
    # None iff below the setup-signature floor OR no snapshots were
    # captured (defensive -- impossible for any game >= PLY_MIN, but a
    # whole sample of aborts is possible). Mirrors the existing
    # opening_family_lean None-vs-empty contract.
    setup_signatures: Optional[List[Dict[str, Any]]] = (
        aggregated_setup_snapshots
        if (
            game_count >= SETUP_SIGNATURE_MIN_GAMES
            and aggregated_setup_snapshots
        )
        else None
    )

    # Round the per-bucket combined-weight masses for transparency. The
    # dominant bucket should match `sparring_bucket` when same-TC games are
    # being preferred (the spec's verification requirement). The "unknown"
    # bucket's mass corresponds to games that got the neutral 1.0 TC weight
    # (flagged via time_control_unclassified_count as a raw count too).
    tc_weighted_by_bucket = {
        bucket: round(w, 4)
        for bucket, w in sorted(
            tc_weight_by_bucket.items(), key=lambda kv: kv[1], reverse=True
        )
    }

    return {
        "sufficient": sufficient,
        "game_count": game_count,
        "effective_sample_size": round(effective_sample_size, 4),
        "recency_decay_lambda": STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR,
        "sparring_time_control_bucket": (
            sparring_bucket if sparring_bucket != "unknown" else None
        ),
        "time_control_weighted_by_bucket": tc_weighted_by_bucket or None,
        "time_control_unclassified_count": tc_unclassified_count,
        "weighted_game_count": round(weighted_game_count, 4),
        "weighted_parseable_game_count": round(weighted_parseable_game_count, 4),
        "sacrifice_frequency": round(sacrifice_frequency, 4),
        "opening_family_lean": opening_family_lean or None,
        "average_game_length": round(average_game_length, 4),
        "castling_side_distribution": castling_side_distribution or None,
        "queen_trade_move_number": (
            round(queen_trade_move_number, 4)
            if queen_trade_move_number is not None
            else None
        ),
        "queens_stay_on_rate": round(queens_stay_on_rate, 4),
        "setup_signatures": setup_signatures,
        "sacrifice_events": raw_sacrifices,
        "opponent_moves": raw_opponent_moves,
    }