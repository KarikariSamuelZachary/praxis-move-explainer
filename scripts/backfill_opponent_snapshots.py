"""
One-off idempotent backfill: recompute every stored opponent profile
snapshot from its source `opponent_games` rows.

Why: snapshots built before the raw per-color / per-time-class opening
counts existed only carry the legacy weighted fields. This rewrites them
through the current `build_opponent_profile_snapshot`, so the Weak Openings
projection gets raw counts without waiting for the next user import.

Properties:
  * Parse-only — no Stockfish, no provider network calls, no writes to
    `opponent_games`. Only `opponent_profile_snapshots` is upserted.
  * Idempotent — the upsert is keyed on (user, provider, opponent); run it
    as many times as you like.
  * Avatar/verified metadata is carried over from the existing snapshot
    (the import flow owns provider metadata fetches). Opponents that never
    got a snapshot are inserted, mirroring the profile-list endpoint's
    lazy backfill.

Usage:
    venv/bin/python scripts/backfill_opponent_snapshots.py [--dry-run]
        [--user CLERK_ID] [--provider lichess|chesscom] [--opponent NAME]
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001 -- dotenv is optional
    pass

from core import database  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402
from services.opponent_repertoire import (  # noqa: E402
    build_opponent_profile_snapshot,
    upsert_opponent_profile_snapshot,
)


def _load_targets(conn, *, user, provider, opponent):
    """Existing snapshots (canonical casing + metadata carry-over) plus
    game groups that never got a snapshot (case-insensitive anti-join — the
    same backfill the profile-list endpoint performs on read)."""
    params = {"user": user, "provider": provider, "opponent": opponent}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT requested_by_user_id, provider, opponent_username,
                   avatar_url, verified, TRUE AS has_snapshot
            FROM opponent_profile_snapshots
            WHERE (%(user)s::text IS NULL OR requested_by_user_id = %(user)s)
              AND (%(provider)s::text IS NULL OR provider = %(provider)s)
              AND (
                  %(opponent)s::text IS NULL
                  OR LOWER(opponent_username) = LOWER(%(opponent)s)
              )
            ORDER BY requested_by_user_id, provider, opponent_username
            """,
            params,
        )
        targets = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT g.requested_by_user_id, g.provider,
                   MIN(g.opponent_username) AS opponent_username,
                   NULL::text AS avatar_url, FALSE AS verified,
                   FALSE AS has_snapshot
            FROM opponent_games g
            WHERE (%(user)s::text IS NULL OR g.requested_by_user_id = %(user)s)
              AND (%(provider)s::text IS NULL OR g.provider = %(provider)s)
              AND (
                  %(opponent)s::text IS NULL
                  OR LOWER(g.opponent_username) = LOWER(%(opponent)s)
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM opponent_profile_snapshots s
                  WHERE s.requested_by_user_id = g.requested_by_user_id
                    AND s.provider = g.provider
                    AND LOWER(s.opponent_username) = LOWER(g.opponent_username)
              )
            GROUP BY g.requested_by_user_id, g.provider, LOWER(g.opponent_username)
            ORDER BY 1, 2, 3
            """,
            params,
        )
        targets.extend(dict(row) for row in cur.fetchall())
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute stored opponent profile snapshots (parse-only, "
            "idempotent)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the targets without writing anything",
    )
    parser.add_argument("--user", help="limit to one Clerk user id")
    parser.add_argument(
        "--provider", choices=("lichess", "chesscom"), help="limit to one provider"
    )
    parser.add_argument(
        "--opponent", help="limit to one opponent username (case-insensitive)"
    )
    args = parser.parse_args()

    database.init_db()
    conn = database.connection_pool.getconn()
    scanned = written = inserted = failed = 0
    started_all = time.perf_counter()
    try:
        targets = _load_targets(
            conn,
            user=args.user,
            provider=args.provider,
            opponent=args.opponent,
        )
        print(f"targets={len(targets)} dry_run={args.dry_run}")
        for target in targets:
            scanned += 1
            label = (
                f"{target['provider']}/{target['opponent_username']} "
                f"(user={target['requested_by_user_id']})"
            )
            action = "update" if target["has_snapshot"] else "insert"
            if args.dry_run:
                print(f"  [dry-run] {action}: {label}")
                continue
            opponent_started = time.perf_counter()
            try:
                snapshot = build_opponent_profile_snapshot(
                    conn,
                    requested_by_user_id=target["requested_by_user_id"],
                    provider=target["provider"],
                    opponent_username=target["opponent_username"],
                    avatar_url=target["avatar_url"],
                    verified=bool(target["verified"]),
                )
                upsert_opponent_profile_snapshot(conn, snapshot)
                conn.commit()
                written += 1
                if action == "insert":
                    inserted += 1
                print(
                    f"  [ok] {action}: {label} games={snapshot['game_count']} "
                    f"duration_ms={(time.perf_counter() - opponent_started) * 1000:.0f}"
                )
            except Exception as exc:  # noqa: BLE001 -- keep going per opponent
                conn.rollback()
                failed += 1
                print(f"  [FAIL] {action}: {label} -> {exc!r}")
    finally:
        database.connection_pool.putconn(conn)

    print(
        f"\nsummary: scanned={scanned} written={written} "
        f"(inserted={inserted}) failed={failed} "
        f"duration_ms={(time.perf_counter() - started_all) * 1000:.0f}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
