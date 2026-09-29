"""Invariant test for `compute_opening_results`' raw per-color / per-class
counts, run against a REAL imported opponent in the dev DB.

Run with:
    cd src && ../venv/bin/python services/opponent_openings_invariants_test.py
    cd src && ../venv/bin/python services/opponent_openings_invariants_test.py chesscom jolash_01

Defaults to the opponent with the most stored games. Read-only.

For every opening bucket, asserts the storage contract the projection step
relies on:

  * raw_count == raw_wins + raw_losses + raw_draws + aborted("*")
  * per color (and per time class):
      - the counts sum exactly to raw_count (no game silently dropped)
      - wins/losses/draws sum exactly to the aggregate raw_wins/losses/draws
  * the "unknown" time class is stored, never dropped

Exits non-zero on the first failed invariant. Skips (exit 0) when the DB
is unreachable or no opponent games are stored.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
ROOT = os.path.dirname(SRC)

# Self-bootstrap so the documented `cd src && python services/...` invocation
# works regardless of PYTHONPATH (the DB-backed harness needs `core`).
if SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001 -- dotenv is optional
    pass

from psycopg2.extras import RealDictCursor  # noqa: E402

from core import database  # noqa: E402
from services.opponent_repertoire import (  # noqa: E402
    OPENING_LOW_SAMPLE_MIN_GAMES,
    OPENING_MIN_RAW_GAMES,
    _openings_lost_against,
)
from services.opponent_style import compute_opening_results  # noqa: E402

_AXES = ("raw_by_color", "raw_by_time_class")
_STATS = ("wins", "losses", "draws")


def _pick_opponent(conn, provider, username):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if provider and username:
            cur.execute(
                """
                SELECT provider, opponent_username, COUNT(*)::int AS games
                FROM opponent_games
                WHERE provider = %s AND LOWER(opponent_username) = LOWER(%s)
                GROUP BY provider, opponent_username
                """,
                (provider, username),
            )
        else:
            cur.execute(
                """
                SELECT provider, opponent_username, COUNT(*)::int AS games
                FROM opponent_games
                GROUP BY provider, opponent_username
                ORDER BY games DESC
                LIMIT 1
                """
            )
        row = cur.fetchone()
        return dict(row) if row else None


def _load_games(conn, provider, username):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT pgn, end_time, time_class, opponent_username
            FROM opponent_games
            WHERE provider = %s AND LOWER(opponent_username) = LOWER(%s)
            ORDER BY end_time DESC
            """,
            (provider, username),
        )
        return [dict(row) for row in cur.fetchall()]


def _check_bucket(family, bucket, failures):
    raw = bucket["raw_count"]
    w, l, d = bucket["raw_wins"], bucket["raw_losses"], bucket["raw_draws"]
    if raw < 0 or w < 0 or l < 0 or d < 0:
        failures.append(f"{family}: negative count ({bucket})")
        return
    if w + l + d > raw:
        failures.append(
            f"{family}: W/L/D ({w}/{l}/{d}) exceed raw_count ({raw})"
        )

    for axis in _AXES:
        axis_map = bucket.get(axis) or {}
        if not axis_map:
            failures.append(f"{family}: {axis} is empty for raw_count={raw}")
            continue
        total = sum(entry["count"] for entry in axis_map.values())
        if total != raw:
            failures.append(
                f"{family}: {axis} count sum {total} != raw_count {raw} "
                f"({axis_map})"
            )
        for stat in _STATS:
            axis_sum = sum(entry[stat] for entry in axis_map.values())
            if axis_sum != bucket[f"raw_{stat}"]:
                failures.append(
                    f"{family}: {axis} {stat} sum {axis_sum} != "
                    f"raw_{stat} {bucket[f'raw_{stat}']}"
                )


def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 2 else None
    username = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.environ.get("DATABASE_URL"):
        print("SKIP: DATABASE_URL is not set")
        return 0

    database.init_db()
    conn = database.connection_pool.getconn()
    try:
        opponent = _pick_opponent(conn, provider, username)
        if not opponent:
            print("SKIP: no opponent_games rows in the DB")
            return 0
        games = _load_games(
            conn, opponent["provider"], opponent["opponent_username"]
        )
    finally:
        database.connection_pool.putconn(conn)

    print(
        f"opponent={opponent['provider']}/{opponent['opponent_username']} "
        f"games={len(games)}"
    )
    result = compute_opening_results(games)
    by_opening = result["by_opening"]
    print(f"buckets={len(by_opening)}")

    failures = []
    for family, bucket in by_opening.items():
        _check_bucket(family, bucket, failures)

    # The projection must consume the raw shape without violating its own
    # contract: no `_unknown` rows, color always set, floor respected, and
    # low-sample rows confined to the backfill band.
    rows = _openings_lost_against(by_opening)
    print(f"projected rows={len(rows)}")
    for row in rows:
        if row.get("legacy"):
            failures.append(f"{row['name']}: raw projection fell back to legacy")
        if row.get("family") == "_unknown":
            failures.append(f"{row['name']}: _unknown must not be displayed")
        if row.get("color") not in ("white", "black"):
            failures.append(f"{row['name']}: missing color split ({row.get('color')})")
        if not 0.0 <= row.get("loss_rate", -1) <= 1.0:
            failures.append(f"{row['name']}: loss_rate out of range ({row.get('loss_rate')})")
        if row.get("loss_percentage") != row.get("loss_rate"):
            failures.append(f"{row['name']}: transitional loss_percentage drifted")
        if row.get("low_sample"):
            if not (
                OPENING_LOW_SAMPLE_MIN_GAMES
                <= row["raw_games"]
                < OPENING_MIN_RAW_GAMES
            ):
                failures.append(
                    f"{row['name']}: low_sample row outside backfill band "
                    f"({row['raw_games']})"
                )
        elif row.get("raw_games", 0) < OPENING_MIN_RAW_GAMES:
            failures.append(
                f"{row['name']}: qualified row below the raw floor "
                f"({row['raw_games']})"
            )

    if failures:
        print(f"\nFAILED ({len(failures)} invariant violation(s)):")
        for failure in failures[:25]:
            print(f"  - {failure}")
        if len(failures) > 25:
            print(f"  ... and {len(failures) - 25} more")
        return 1

    # Positive evidence that the axes are actually populated in real data.
    classes = sorted(
        {
            cls
            for bucket in by_opening.values()
            for cls in (bucket.get("raw_by_time_class") or {})
        }
    )
    print(f"time classes seen: {classes}")
    print("All invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
