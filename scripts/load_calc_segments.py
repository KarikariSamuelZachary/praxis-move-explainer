"""Load clean forced segments from the calc-probe corpus into Postgres.

Source: scripts/calc_probe/corpus.sqlite (table clean_forced_segments),
read in a read-only transaction. Target: public.calc_segments
(see src/core/migrations.py).

Idempotent: every insert is ON CONFLICT (source_puzzle_id,
start_solver_index) DO NOTHING. Re-running loads only new rows.

Safety:
  * --dry-run is the default; nothing is written without --apply.
  * --apply against a non-local Postgres host additionally requires
    --confirm-host <hostname> exactly matching the target host.

Usage:
    python scripts/load_calc_segments.py --dry-run   # default
    python scripts/load_calc_segments.py --apply --db-url ... --confirm-host ...
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "calc_probe"))

import chess

from puzzle_line import band_of, family_of

BATCH = 500
RUN_ID = "corpus-sf19-1"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", "db"}

REASONS = ("bad_fen", "first_move_illegal", "illegal_move_in_line",
           "ply_count_mismatch", "duplicate_in_source")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Load calc segments to Postgres")
    ap.add_argument("--corpus", default=None,
                    help="source SQLite file (default: scripts/calc_probe/corpus.sqlite)")
    ap.add_argument("--run-id", default=RUN_ID)
    ap.add_argument("--db-url", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--dry-run", dest="apply", action="store_false",
                    default=False)
    ap.add_argument("--apply", dest="apply", action="store_true")
    ap.add_argument("--confirm-host", default=None)
    ap.add_argument("--batch", type=int, default=BATCH)
    return ap.parse_args(argv)


def target_host(db_url: str) -> str:
    return urllib.parse.urlparse(db_url).hostname or ""


def validate(row: dict, seen: set) -> str | None:
    """Return a rejection reason, or None if the row is loadable."""
    key = (row["puzzle_id"], row["start"])
    if key in seen:
        return "duplicate_in_source"
    seen.add(key)
    try:
        board = chess.Board(row["fen"])
    except ValueError:
        return "bad_fen"
    toks = (row["line"] or "").split()
    n = row["n"]
    if n < 2 or len(toks) != 2 * n - 1:
        return "ply_count_mismatch"
    for i, uci in enumerate(toks):
        try:
            mv = chess.Move.from_uci(uci)
        except ValueError:
            return "first_move_illegal" if i == 0 else "illegal_move_in_line"
        if mv not in board.legal_moves:
            return "first_move_illegal" if i == 0 else "illegal_move_in_line"
        board.push(mv)
    return None


def read_source(path: str, run_id: str) -> list[dict]:
    con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            "SELECT puzzle_id, segment_fen, line_uci, start_solver_index,"
            " solver_move_count, rating, themes, trivially_forced,"
            " engine_verified_replies, min_cp_margin, mate_involved, checks,"
            " captures, quiet, promotions, verified_level, engine_version"
            " FROM clean_forced_segments WHERE run_id=? ORDER BY puzzle_id,"
            " start_solver_index", (run_id,)).fetchall()
    finally:
        con.close()
    names = ("puzzle_id", "fen", "line", "start", "n", "rating", "themes",
             "trivial", "verified", "min_cp", "mate", "checks", "captures",
             "quiet", "promos", "vlevel", "engine_version")
    return [dict(zip(names, r)) for r in rows]


def to_row(r: dict) -> tuple:
    themes = (r["themes"] or "").split()
    return (
        r["puzzle_id"], r["fen"], r["line"], r["n"], r["start"],
        band_of(r["rating"]), family_of(themes), r["rating"], themes,
        bool(r["trivial"]), r["verified"], r["min_cp"], bool(r["mate"]),
        r["checks"], r["captures"], r["quiet"], r["promos"], r["vlevel"],
        r["engine_version"],
    )


INSERT_SQL = """
INSERT INTO calc_segments (source_puzzle_id, segment_fen, line_uci,
    solver_move_count, start_solver_index, band, family, rating, themes,
    trivially_forced, engine_verified_replies, min_cp_margin, mate_involved,
    checks, captures, quiet, promotions, verified_level, engine_version)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (source_puzzle_id, start_solver_index) DO NOTHING
"""


def main(argv=None) -> int:
    args = parse_args(argv)
    corpus = args.corpus or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "calc_probe",
        "corpus.sqlite")
    rows = read_source(corpus, args.run_id)
    print(f"source: {corpus} run={args.run_id} segments={len(rows)}")

    seen: set = set()
    valid: list[dict] = []
    rejected: dict[str, int] = {}
    for r in rows:
        reason = validate(r, seen)
        if reason is None:
            valid.append(r)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1

    inserted = skipped = 0
    if args.apply:
        if not args.db_url:
            raise SystemExit("--apply needs --db-url (or DATABASE_URL)")
        host = target_host(args.db_url)
        if host not in LOCAL_HOSTS and args.confirm_host != host:
            raise SystemExit(
                f"refusing --apply to non-local host {host!r} without "
                f"--confirm-host {host!r}")
        print(f"APPLYING to host {host!r} in batches of {args.batch}")
        import psycopg2
        from psycopg2 import InterfaceError, OperationalError

        def _connect():
            # keepalives so a long batch of per-row round-trips does not
            # look idle to middleboxes (Azure dropped one such connection).
            return psycopg2.connect(args.db_url, keepalives=1,
                                    keepalives_idle=30,
                                    keepalives_interval=10,
                                    keepalives_count=3)

        con = _connect()
        try:
            for i in range(0, len(valid), args.batch):
                batch = valid[i:i + args.batch]
                for attempt in (0, 1):
                    try:
                        with con:  # explicit transaction per batch
                            with con.cursor() as cur:
                                for r in batch:
                                    cur.execute(INSERT_SQL, to_row(r))
                                    inserted += cur.rowcount
                                    skipped += 1 - cur.rowcount
                        break
                    except (InterfaceError, OperationalError) as e:
                        # One reconnect + retry: the batch transaction
                        # rolled back with the dead connection, and the
                        # loader is idempotent, so no partial state.
                        print(f"  batch {i // args.batch + 1}: connection"
                              f" lost ({e}); reconnecting", flush=True)
                        try:
                            con.close()
                        except Exception:
                            pass
                        if attempt == 1:
                            raise
                        con = _connect()
                print(f"  batch {i // args.batch + 1}: "
                      f"inserted={inserted} skipped={skipped}", flush=True)
        finally:
            con.close()
    else:
        print("dry-run: no writes (pass --apply to load)")

    print(f"valid={len(valid)} rejected={sum(rejected.values())} "
          f"inserted={inserted} skipped={skipped}")
    for reason in REASONS:
        if rejected.get(reason):
            print(f"  rejected {reason}: {rejected[reason]}")
    # Non-trivial rows by band x solver_move_count.
    matrix: dict = {}
    trivial = 0
    for r in valid:
        if r["trivial"]:
            trivial += 1
            continue
        key = (band_of(r["rating"]), r["n"])
        matrix[key] = matrix.get(key, 0) + 1
    bands = sorted({b for b, _ in matrix}, key=lambda b: (b is None, b))
    ns = sorted({n for _, n in matrix})
    print("non-trivial rows by band x solver_move_count:")
    print("band | " + " | ".join(f"N={n}" for n in ns))
    for b in bands:
        print(f"{b} | " + " | ".join(str(matrix.get((b, n), 0)) for n in ns))
    print(f"trivially_forced rows (loaded, flagged): {trivial}")
    # Estimated table size: measured avg field bytes + ~100B PG row overhead.
    payload = sum(len((r["fen"] or "")) + len((r["line"] or ""))
                  + len((r["themes"] or "")) + 8 * 8 for r in valid)
    est = (payload / max(len(valid), 1) + 100) * len(valid)
    print(f"estimated calc_segments size: ~{est / 1e6:.1f} MB "
          f"for {len(valid)} rows (payload + 100B/row overhead)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
