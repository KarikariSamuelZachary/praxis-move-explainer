"""
Trap clustering for opponent preparation.

Groups an opponent's blunders (from ``opponent_game_blunders``) into
recurring "traps" — positions the opponent has blundered in across 2+
DIFFERENT games.  Used by the "Traps He's Fallen For" section of the
Opponent Preparation page.

This is a read/aggregation over existing data, NOT a new job.  It is a
pure function callable at request time — no status tables, no side
effects.  Returns an empty list when zero traps qualify (the expected,
common case for opponents with sparse blunder data).

The grouping is done in Python (not SQL GROUP BY) so the dedupe-by-
game_id and 2+-distinct-games qualifying logic is genuinely testable
without a real database.  The WHERE clause is served by the existing
``idx_opponent_game_blunders_lookup`` index on
``(requested_by_user_id, provider, opponent_username, position_key)``.

This module ALSO exports ``compute_exploitable_traps`` -- a thinner,
reranker-specific sibling of ``compute_opponent_traps`` that applies
the ``TRAP_MIN_HITS`` / ``TRAP_MIN_GAMES`` exploitability gates and
returns the bare ``set`` of qualifying ``position_key`` strings (not
the full display-shaped trap objects the UI consumes).  The reranker
receives this set as ``exploitable_trap_keys`` and uses it to decide
per-move whether to enter trap-mode (drill a known weakness) or stay in
mirror-mode (today's style-bias behaviour).  See decision (6) in
``opponent_style_reranker.py``'s module docstring for the full branch
spec.
"""
import logging
import math
import time
from typing import Any, Dict, List, Optional, Set

from psycopg2.extras import RealDictCursor

from services.opponent_style import (
    STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR,
    _game_tc_weight,
    _time_control_bucket,
)

log = logging.getLogger(__name__)

# Seconds per year (365.25 days, leap-year averaged). Used by the
# recency-weight helper that applies moderate decay to the per-position
# blunder hit count (see compute_exploitable_traps below).
_SECONDS_PER_YEAR = 365.25 * 86400.0


def _game_recency_weight(end_time: int, now_unix: float) -> float:
    """exp(-lambda * age_years) for one game, with end_time=0 -> neutral 1.0.

    Mirrors the helper in opponent_style.py so the trap layer ages blunders
    on the SAME timescale as the style-aggregate signals (lambda=1.0,
    half-life ~8.3 months). This is the "moderate decay" the spec asks for
    on trap/blunder patterns: recent blunders count fully toward the
    TRAP_MIN_HITS gate, while old blunders need more volume to clear it.
    """
    if not end_time or end_time <= 0:
        return 1.0
    age_seconds = max(0.0, now_unix - float(end_time))
    age_years = age_seconds / _SECONDS_PER_YEAR
    return math.exp(-STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR * age_years)


# --- exploitability floor (trap-mode gates) ---------------------------------
#
# A position_key is "exploitable" for a given opponent iff BOTH gates pass:
#
#   (1) TRAP_MIN_HITS = 2: the opponent has blundered from this exact
#       position_key in games whose RECENCY-WEIGHTED sum >= 2.0 (deduped
#       by game_id, matching compute_opponent_traps' dedupe convention).
#       Each distinct game's blunder contributes its per-game recency
#       weight (exp(-lambda * age_years), lambda=1.0, half-life ~8.3
#       months — same decay as the style-aggregate signals) to the sum.
#       Two RECENT blunders give 2.0 >= 2.0 (clears). Two 1-year-old
#       blunders give 2 * 0.368 = 0.74 < 2.0 (doesn't clear — old
#       blunders need more volume to be "exploitable"). This is the
#       "moderate decay" the spec asks for: recent blunder patterns
#       count fully, old ones need more recurrence to clear the same
#       floor. The floor VALUE (2) is kept; only the unit changed from
#       raw count to weighted sum.
#       2 is the smallest count where a recurrence starts to mean
#       something rather than being one isolated occurrence (the same
#       "rule of three" intuition MIN_STYLE_GAMES uses, tightened by 1
#       because a repeated BLUNDER is a stronger signal than a repeated
#       opening choice).
#
#   (2) TRAP_MIN_GAMES = 5: the opponent has >= 5 total games in the
#       imported dataset (COUNT over opponent_games, NOT over
#       opponent_game_blunders -- a position can be reached without a
#       blunder occurring).  Reuses the same floor spirit as
#       MIN_STYLE_GAMES (=3, in opponent_style.py) and
#       MIN_REPERTOIRE_SAMPLES (=1, in opponent_repertoire.py), kept as
#       its OWN named constant here (not imported) so this layer can be
#       tuned independently -- matching how
#       STYLE_RECENCY_DECAY_LAMBDA_PER_YEAR is a separate constant from
#       the repertoire sampler's RECENCY_DECAY_LAMBDA_PER_YEAR (the two
#       now diverge: style=1.0, repertoire=0.5).  5 (not 3) because a *repeated blunder pattern*
#       is a stronger claim than a *style aggregate*: the style layer
#       profiling "this player sacs a lot" can afford to fire at 3 games
#       since each game contributes many opponent moves (variance shrinks
#       fast), but a *specific position* the opponent blunders in needs
#       a larger game base before the absence of blunders in other games
#       at that position is meaningful evidence the position is safe.
#       Tune up if trap-mode fires on noise for sparse opponents; tune
#       down if established-but-few-games opponents never enter trap-mode.
#
# Both gates must pass.  A 2-game opponent with 2 identical blunders does
# NOT count (fails gate 2).  A 50-game opponent with exactly 1 hit on a
# position_key does NOT count (fails gate 1).  See Tests 18-20 in
# opponent_style_reranker_test.py for the per-gate isolation cases.
TRAP_MIN_HITS = 2
TRAP_MIN_GAMES = 5


def compute_opponent_traps(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> List[Dict[str, Any]]:
    """Cluster an opponent's blunders into recurring-position traps.

    A position qualifies as a trap ONLY if the opponent blundered there
    in 2+ DIFFERENT games.  The same game blundering twice at the same
    position does NOT count as 2 — we dedupe by ``game_id`` within each
    position group before checking the threshold.

    Returns a list of trap dicts sorted by ``game_count`` descending,
    each containing:

      * ``position_key``      — first 4 FEN fields (same convention as
                                 the repertoire sampler's
                                 ``_position_key(board)``).
      * ``fen``               — one representative full FEN from the
                                 group (the first row encountered).
      * ``moves``             — sorted distinct ``move_san`` values
                                 played at this position.
      * ``classification``    — worst of ``blunder``/``mistake`` in the
                                 group (``blunder`` wins).
      * ``game_count``        — number of DISTINCT games in the group.
      * ``move_number_min``   — earliest move_number in the group.
      * ``move_number_max``   — latest move_number in the group.
      * ``tier``              — always ``"position"`` (the only tier
                                 implemented; opening-family fallback
                                 is intentionally not built).

    Returns ``[]`` when zero groups qualify.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                position_key,
                fen,
                move_san,
                classification,
                game_id,
                move_number
            FROM opponent_game_blunders
            WHERE requested_by_user_id = %s
              AND provider = %s
              AND LOWER(opponent_username) = LOWER(%s)
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        rows = [dict(row) for row in cur.fetchall()]

    # --- Group by position_key ---
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = row["position_key"]
        groups.setdefault(key, []).append(row)

    traps: List[Dict[str, Any]] = []
    for position_key, group_rows in groups.items():
        # Dedupe by game_id — the same game blundering twice at the same
        # position counts as ONE game, not two.
        game_ids = {r["game_id"] for r in group_rows}
        if len(game_ids) < 2:
            continue

        classifications = {r["classification"] for r in group_rows}
        worst = "blunder" if "blunder" in classifications else "mistake"

        moves = sorted({r["move_san"] for r in group_rows})
        move_numbers = [r["move_number"] for r in group_rows]

        traps.append(
            {
                "position_key": position_key,
                "fen": group_rows[0]["fen"],
                "moves": moves,
                "classification": worst,
                "game_count": len(game_ids),
                "move_number_min": min(move_numbers),
                "move_number_max": max(move_numbers),
                "tier": "position",
            }
        )

    traps.sort(key=lambda t: t["game_count"], reverse=True)
    return traps


def compute_exploitable_traps(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    sparring_time_control: Optional[str] = None,
) -> Set[str]:
    """Return the bare set of exploitable ``position_key`` strings for
    this opponent, applying BOTH exploitability gates (see
    ``TRAP_MIN_HITS`` / ``TRAP_MIN_GAMES`` above).

    This is the reranker-specific sibling of ``compute_opponent_traps``:
    it returns the thin ``set`` of qualifying ``position_key`` strings
    (NOT the full display-shaped trap dicts the UI consumes), so the
    reranker's per-move ``_is_trap_triggering`` check is a single
    ``resulting_key in exploitable_trap_keys`` lookup.  Computed ONCE
    per sparring session by the caller and passed into
    ``rerank_candidates(exploitable_trap_keys=...)`` -- not recomputed
    per move.

    ``sparring_time_control`` is the OPTIONAL time-control string of the
    current sparring session (a bucket label, "M+I" label, or raw
    "base+inc").  When supplied, each blunder's per-game weight is the
    PRODUCT of the recency decay AND the time-control similarity
    (``_game_tc_weight``), so blunders from the SAME time control as the
    sparring session count fully toward ``TRAP_MIN_HITS`` while cross-TC
    blunders are down-weighted (moderate weighting -- the off-diagonal
    similarity values are 0.1-0.7, not zero, so a cross-TC blunder still
    contributes, just needs more volume to clear the gate).  When it is
    None/unbucketable, the TC factor collapses to 1.0 and the weighting
    is recency-only (existing callers unaffected).

    Gates (both must pass; a failure on either returns a key-free set):

      (1) ``TRAP_MIN_GAMES`` (opponent-level, RAW count): the opponent
          has >= 5 total games in ``opponent_games``.  Below this the
          whole opponent is treated as "no exploitable traps" regardless
          of how concentrated any single position's blunders are -- a
          3-game opponent with 3 identical blunders is anecdote, not a
          statistically real pattern.  Checked first so the (cheaper)
          per-position gate only runs when the opponent-level gate has
          already passed.  This gate is on RAW game count (not
          recency-weighted) because it is a volume check ("does the
          opponent have enough games on file for the absence of blunders
          elsewhere to be meaningful"), not a recency check.

      (2) ``TRAP_MIN_HITS`` (per-position_key, RECENCY+TC-WEIGHTED): each
          candidate ``position_key`` must have blunders in distinct
          games whose RECENCY+TC-WEIGHTED sum >= 2.0 (TRAP_MIN_HITS as a
          float).  Each distinct game's blunder contributes its
          per-game combined weight (recency exp(-lambda * age_years)
          times _game_tc_weight(game_bucket, sparring_bucket),
          lambda=1.0 -- same decay as the style-aggregate signals) to
          the sum.  Two recent same-TC blunders give 2.0 >= 2.0
          (clears); old or cross-TC blunders need more volume to clear.
          Grouping/deduping is done in Python (not SQL GROUP BY) so the
          dedupe-by-game_id logic is testable without a real database,
          matching ``compute_opponent_traps``'s shape.  This is the
          "moderate decay" the spec asks for on trap/blunder patterns.

    Returns an empty ``set`` when the opponent has no blunder rows, or
    when no ``position_key`` clears both gates.  ``None`` is never
    returned -- the reranker's contract is "a set (possibly empty) or
    omit the argument entirely"; an empty set and an omitted argument
    both reproduce mirror-only behaviour (``trap_mode_active=False``).
    """
    # Resolve the sparring session's time-control bucket ONCE. When it is
    # None/unbucketable, _game_tc_weight returns 1.0 for every game and
    # the combined weight collapses to recency-only (existing callers
    # unaffected -- the moderate TC weighting only bites when a bucketable
    # sparring TC is supplied).
    sparring_bucket = _time_control_bucket(sparring_time_control)

    # --- Gate 1: opponent-level total game count (RAW) ----------------------
    # Checked first so the per-position gate only runs when this passes.
    # COUNT(DISTINCT id) over opponent_games, NOT over opponent_game_blunders
    # -- a position can be reached without a blunder occurring, so the
    # blunder table undercounts the opponent's total game base.
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT COUNT(DISTINCT id) AS total_games
            FROM opponent_games
            WHERE requested_by_user_id = %s
              AND provider = %s
              AND LOWER(opponent_username) = LOWER(%s)
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        count_rows = [dict(r) for r in cur.fetchall()]
    total_games = (
        int(count_rows[0]["total_games"])
        if count_rows and count_rows[0].get("total_games") is not None
        else 0
    )
    if total_games < TRAP_MIN_GAMES:
        # Opponent-level gate fails -> no position_key can be exploitable
        # regardless of blunder concentration.  Return early so the
        # per-position query below doesn't run.
        return set()

    # --- Gate 2: per-position_key hit count (recency+TC-weighted) -----------
    # Join opponent_game_blunders with opponent_games to get end_time AND
    # time_class for each blunder row, so we can compute per-game COMBINED
    # (recency x TC) weights. The WHERE clause on opponent_game_blunders'
    # own columns keeps the blunder-table index
    # (idx_opponent_game_blunders_lookup) usable.
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT b.position_key, b.game_id, g.end_time, g.time_class
            FROM opponent_game_blunders b
            JOIN opponent_games g ON g.id = b.game_id
            WHERE b.requested_by_user_id = %s
              AND b.provider = %s
              AND LOWER(b.opponent_username) = LOWER(%s)
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        rows = [dict(r) for r in cur.fetchall()]

    # Group by position_key, dedupe by game_id (same convention as
    # compute_opponent_traps: the same game blundering twice at the same
    # position counts as ONE game, not two). For each distinct game, keep
    # the MAX combined weight (all blunder rows from the same game share
    # the same end_time/time_class so the weight is the same; max is
    # defensive against any row-level oddness).
    now_unix = time.time()
    key_to_game_weights: Dict[str, Dict[Any, float]] = {}
    for row in rows:
        key = row.get("position_key")
        if not key:
            continue
        game_id = row.get("game_id")
        end_time = int(row.get("end_time") or 0)
        game_bucket = _time_control_bucket(row.get("time_class"))
        weight = _game_recency_weight(end_time, now_unix) * _game_tc_weight(
            game_bucket, sparring_bucket
        )
        game_weights = key_to_game_weights.setdefault(key, {})
        # If the same game_id appears multiple times (multiple blunders in
        # one game at the same position), keep the weight (they all share
        # the same end_time/time_class so the weight is the same; max is
        # defensive).
        if game_id not in game_weights or weight > game_weights[game_id]:
            game_weights[game_id] = weight

    # A position_key is exploitable iff the SUM of per-game COMBINED
    # (recency x TC) weights across distinct games clears TRAP_MIN_HITS
    # (as a float). Two recent same-TC blunders give 2.0 >= 2.0 (clears);
    # old or cross-TC blunders need more volume. Below this a single-
    # occurrence "blunder" is noise, not a pattern -- see the
    # TRAP_MIN_HITS constant comment above.
    exploitable: Set[str] = {
        key
        for key, game_weights in key_to_game_weights.items()
        if sum(game_weights.values()) >= float(TRAP_MIN_HITS)
    }
    return exploitable
