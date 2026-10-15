"""Delete one dev user's calc data (attempts, ratings, Woodpecker rows).

Refuses to run unless the database host is localhost: this is a footgun
guard, not a migration — it exists so backend/frontend iteration on the
calculation trainer doesn't require manual SQL psql sessions.

Usage:
    venv/bin/python scripts/calc_dev_reset.py --user <clerk-id> [--db-url ...]
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Reset one dev user's calc data")
    ap.add_argument("--user", required=True, help="users.clerk_id to wipe")
    ap.add_argument("--db-url", default=os.getenv("DATABASE_URL"))
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.db_url:
        raise SystemExit("--db-url (or DATABASE_URL) is required")
    host = urllib.parse.urlparse(args.db_url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"refusing: host {host!r} is not localhost")
    import psycopg2
    con = psycopg2.connect(args.db_url)
    try:
        with con:
            with con.cursor() as cur:
                # Order matters only for tables without CASCADE help;
                # user_id FKs cascade attempts/ratings/entries anyway.
                for table in ("calc_why", "calc_woodpecker_attempts",
                              "calc_woodpecker_entries", "calc_attempts",
                              "calc_ratings"):
                    if table == "calc_why":
                        cur.execute(
                            "DELETE FROM calc_why WHERE attempt_id IN"
                            " (SELECT id FROM calc_attempts WHERE user_id"
                            " = %s) OR review_attempt_id IN (SELECT id FROM"
                            " calc_woodpecker_attempts WHERE user_id = %s)",
                            (args.user, args.user))
                    else:
                        cur.execute(f"DELETE FROM {table} WHERE user_id = %s",
                                    (args.user,))
                    print(f"{table}: {cur.rowcount} rows deleted")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
