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
# Minimum number of opponent games before this layer commits to a style
# profile. Below it `compute_opponent_style` reports `sufficient=False`
# and the caller falls through to default Maia behaviour — exactly the same
# contract `pick_repertoire_move` uses for the per-position case.
#
# Why 3 and not 5 (the per-position floor in opponent_repertoire.py):
#   * A whole-game aggregate is much more sample-efficient than a
#     per-position sample. Each game contributes many opponent moves
#     (sacrifice denominator) and one family label (opening denominator),
#     so the variance of the rates shrinks much faster per game added
#     than per-position move counts. Two games give ~60 opponent moves
#     and 2 family labels — already past "anecdote" territory for the
#     sacrifice rate, but still very thin for the family distribution.
#   * The intuition the spec flagged: aggregating across a player's whole
#     history needs fewer games than sampling one noisy position, so the
#     floor can be lower.
#   * "Rule of three": >=3 events is the smallest count where a rate
#     starts to mean something rather than being one-or-two isolated
#     occurrences. With 3 games worth of opponent moves we are well past
#     this for the sacrifice rate; for the family distribution 3 games
#     is right at the edge (3 bins at most), which is why the family
#     lean is reported but the caller should treat a 3-game family lean
#     as barely-sufficient (still better than nothing, which is the
#     alternative the caller falls through to).
#
# Tune up if noisy style signals leak into re-ranking; tune down if too
# many established-but-few-games opponents fall through to default Maia.
MIN_STYLE_GAMES = 3

# --- recency decay ---------------------------------------------------------
#
# Per-game exponential decay rate, per year, applied to per-game weights
# when computing the weighted average of each style signal. Same value as
# RECENCY_DECAY_LAMBDA_PER_YEAR in opponent_repertoire.py (half-life
# ~= 1.39 yr) so the two layers age on the same timescale and do not
# disagree about whether "old habits still count". DECoupled as a separate
# constant here (not imported) so the two layers can be tuned independently
# later — cross-service constant imports of magic tuning numbers are an
# awkward coupling, and the two layers may legitimately want different
# lifetimes (openings evolve on the order of years; player style arguably
# even slower). For now they match.
STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR = 0.5

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
         extracts just the family. This is the path all 200 production
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

    mainline = list(game.mainline_moves())
    if not mainline:
        return None

    board = game.board()
    opponent_moves = 0
    sacrifices = 0
    opp_castled = "never"
    queen_capture_plies: List[int] = []

    for idx, move in enumerate(mainline):
        if board.turn == opponent_color:
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

    white_queens = len(board.pieces(chess.QUEEN, chess.WHITE))
    black_queens = len(board.pieces(chess.QUEEN, chess.BLACK))
    queens_on_at_end = white_queens > 0 and black_queens > 0
    queens_off_at_end = white_queens == 0 and black_queens == 0

    return {
        "opponent_moves": opponent_moves,
        "sacrifices": sacrifices,
        "family": _opening_family(game),
        "plies": len(mainline),
        "opp_castled": opp_castled,
        "last_queen_capture_ply": (
            max(queen_capture_plies) if queen_capture_plies else None
        ),
        "queens_on_at_end": queens_on_at_end,
        "queens_off_at_end": queens_off_at_end,
    }


def compute_opponent_style(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> Dict[str, Any]:
    """Compute recency-weighted aggregate style signals for one opponent.

    Reads the opponent's imported games from `opponent_games`, parses each
    PGN, and aggregates FIVE signals with per-game recency weighting applied:

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
          "sufficient": bool,            # floor check (>= MIN_STYLE_GAMES)
          "game_count": int,             # raw count, for transparency
          "weighted_game_count": float,  # sum of ALL per-game recency weights
                                         #   (incl. unparseable PGNs)
          "weighted_parseable_game_count": float,  # sum of weights for
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
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT pgn, end_time
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

    # --- floor check (raw count, never weighted) -----------------------------
    # Same contract as opponent_repertoire: evaluate sufficiency on raw
    # sample count, not the decayed weight, so old-but-voluminous opponents
    # still qualify (an opponent who hasn't played in 4 years but has 80
    # games on file is still a known-style opponent).
    sufficient = game_count >= MIN_STYLE_GAMES

    if not sufficient or game_count == 0:
        return {
            "sufficient": sufficient,  # False (or False via game_count=0)
            "game_count": game_count,
            "weighted_game_count": 0.0,
            "weighted_parseable_game_count": 0.0,
            "sacrifice_frequency": None,
            "opening_family_lean": None,
            "average_game_length": None,
            "castling_side_distribution": None,
            "queen_trade_move_number": None,
            "queens_stay_on_rate": None,
            "sacrifice_events": 0,
            "opponent_moves": 0,
        }

    # --- aggregate ------------------------------------------------------------
    # Per-game weights w_g = exp(-lambda * age_years), end_time=0 -> 1.0.
    # We use Python's time.time() for the "now" anchor; the alternative
    # (DB-side NOW()) would round-trip another query and pin the aggregate
    # to the DB clock, which is noisier than the OS clock for a one-shot
    # per-opponent computation that already parses every PGN in Python.
    import time as _time

    now_unix = _time.time()
    # weighted_game_count: sum of ALL per-game weights, including games
    # whose PGNs we couldn't parse. This is the "how much data do we have
    # on file" transparency field — it always >= weighted_parseable_game_count.
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

    for row in rows:
        end_time = int(row.get("end_time") or 0)
        pgn = row.get("pgn") or ""
        weight = _game_recency_weight(end_time, now_unix)

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

    return {
        "sufficient": sufficient,
        "game_count": game_count,
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
        "sacrifice_events": raw_sacrifices,
        "opponent_moves": raw_opponent_moves,
    }