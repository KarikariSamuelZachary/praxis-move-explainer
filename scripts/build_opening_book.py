"""
Build the opening book used by Game Review's "book" classification.

Replaces the old hardcoded "first 10 plies are book" rule with a real
theory/frequency lookup (see services/opening_book.py). Two sources:

  * eco — the CC0 `lichess-org/chess-openings` dataset (downloaded from
    GitHub unless --eco-dir is given). Every move of every named theory
    line is recorded; presence = theory.
  * pgn — an optional masters/high-rated PGN dump. Moves are counted over
    the first --max-ply plies of games where both Elo headers are present
    and >= --min-elo; only moves with count >= --min-count are kept.

Both writes are idempotent: each source's rows are deleted and rewritten,
so re-running never duplicates and always reflects the current input.

Usage:
    venv/bin/python scripts/build_opening_book.py [--source eco|pgn|all]
        [--eco-dir PATH] [--pgn PATH] [--min-count N] [--max-ply N]
        [--min-elo N] [--dry-run]
"""
import argparse
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001 -- dotenv is optional
    pass

import chess.pgn  # noqa: E402
from core import database  # noqa: E402
from services.opening_book import (  # noqa: E402
    collect_moves_from_games,
    collect_moves_from_lines,
    invalidate_cache,
    iter_eco_rows,
    replace_book_rows,
)

ECO_BASE_URL = (
    "https://raw.githubusercontent.com/lichess-org/chess-openings/master"
)
ECO_FILES = ("a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv")


def _load_eco_lines(args) -> list:
    lines = []
    for filename in ECO_FILES:
        if args.eco_dir:
            path = os.path.join(args.eco_dir, filename)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        else:
            url = f"{ECO_BASE_URL}/{filename}"
            print(f"  downloading {url}")
            with urllib.request.urlopen(url, timeout=60) as response:
                text = response.read().decode("utf-8")
        rows = list(iter_eco_rows(text))
        print(f"  {filename}: {len(rows)} theory lines")
        lines.extend(pgn for _eco, _name, pgn in rows)
    return lines


def _iter_games(handle):
    while True:
        game = chess.pgn.read_game(handle)
        if game is None:
            break
        yield game


def _build_eco(args, conn) -> int:
    print("Building ECO/theory book...")
    lines = _load_eco_lines(args)
    book = collect_moves_from_lines(lines)
    rows = [
        (key, uci, san, count)
        for (key, uci), (count, san) in book.items()
        if count >= 1
    ]
    positions = len({key for key, _uci, _san, _count in rows})
    print(f"  {len(rows)} moves across {positions} positions")
    if args.dry_run:
        return len(rows)
    written = replace_book_rows(conn, source="eco", rows=rows)
    print(f"  wrote {written} rows (source=eco)")
    return written


def _build_pgn(args, conn) -> int:
    if not args.pgn:
        raise SystemExit("--source pgn/all requires --pgn PATH")
    print(f"Building frequency book from {args.pgn}...")
    with open(args.pgn, encoding="utf-8", errors="replace") as handle:
        book = collect_moves_from_games(
            _iter_games(handle),
            max_ply=args.max_ply,
            min_elo=args.min_elo,
        )
    rows = [
        (key, uci, san, count)
        for (key, uci), (count, san) in book.items()
        if count >= args.min_count
    ]
    positions = len({key for key, _uci, _san, _count in rows})
    print(
        f"  {len(rows)} moves across {positions} positions "
        f"(min-count={args.min_count}, max-ply={args.max_ply}, min-elo={args.min_elo})"
    )
    if args.dry_run:
        return len(rows)
    written = replace_book_rows(conn, source="pgn", rows=rows)
    print(f"  wrote {written} rows (source=pgn)")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("eco", "pgn", "all"),
        default="eco",
        help="Which book source(s) to build (default: eco).",
    )
    parser.add_argument(
        "--eco-dir",
        default=None,
        help="Local directory with chess-openings a.tsv..e.tsv "
        "(default: download from GitHub).",
    )
    parser.add_argument(
        "--pgn",
        default=None,
        help="Masters/high-rated PGN file for the frequency book.",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=5,
        help="Minimum occurrences for PGN moves (default: 5).",
    )
    parser.add_argument(
        "--max-ply",
        type=int,
        default=30,
        help="Only the first N plies of each PGN game are counted (default: 30).",
    )
    parser.add_argument(
        "--min-elo",
        type=int,
        default=2200,
        help="Skip games where either Elo header is below this (default: 2200).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without writing to the database.",
    )
    args = parser.parse_args()

    database.init_db()
    conn = database.connection_pool.getconn()
    try:
        total = 0
        if args.source in ("eco", "all"):
            total += _build_eco(args, conn)
        if args.source in ("pgn", "all"):
            total += _build_pgn(args, conn)
    finally:
        database.connection_pool.putconn(conn)

    if args.dry_run:
        print(f"Dry run complete: {total} rows would be written.")
    else:
        # This process's cache is irrelevant (it exits), but make the
        # intent explicit for any future in-process callers.
        invalidate_cache()
        print(
            f"Done: {total} rows. A running backend picks the new book up "
            f"within 10 minutes (book cache TTL) without a restart."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
