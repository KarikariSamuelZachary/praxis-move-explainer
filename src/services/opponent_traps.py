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
"""
import logging
from typing import Any, Dict, List

from psycopg2.extras import RealDictCursor

log = logging.getLogger(__name__)


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
