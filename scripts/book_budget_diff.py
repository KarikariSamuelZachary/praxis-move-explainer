#!/usr/bin/env python
"""Frozen-set label diff for a per-position budget rule (kept as a harness).

NOTE: the book-interior cheap budget this was written for was REMOVED from
the production path after measurement: it saved ~0.4s per game on prod
(SF19 @ 1M nps; 2.9 interior book plies/game on the frozen gate set) against
a 3s bar, so the extra fingerprint dimension was not worth it. This script is
kept so a future budget experiment can reuse the methodology: it runs the
REAL review path (GameAnalyzer + StockfishEngine + the opening book) on a
frozen gate sample in two configurations:

    baseline   full budget for every position
    candidate  the experimental reduced budget (env knobs; no-ops today)

and reports the per-ply label diff with the same severity classification used
by nodes_selection.py / determinism_sweep.py. Deterministic node-limited evals
are reproducible, so any deterministic label change is a real regression. Old
time-mode results are compared against a baseline-vs-baseline noise floor.

Only evals for book plies more than two before the exit use the cheap budget;
the last two book plies keep the full budget because their evals and the raw
EP loss feed the first out-of-book ply's Miss/Blunder checks. Expect zero
label changes in deterministic mode.

Usage:
  python scripts/book_budget_diff.py --pgn data/gate_set_A.pgn \
      --limit-positions 200 --limit-games 10 --mode both \
      --nodes 150000 --book-nodes 20000 --json /tmp/book_diff.json
"""
import argparse
import json
import os
import sys
import time
from io import StringIO
from pathlib import Path

import chess
import chess.pgn

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:  # noqa: BLE001 -- dotenv is optional
    pass

from core import database  # noqa: E402
from core.game_analyzer import GameAnalyzer  # noqa: E402
from engines.stockfish_engine import StockfishEngine  # noqa: E402
from llms.mock_explainer import MockExplainer  # noqa: E402
from services.opening_book import (  # noqa: E402
    count_book_rows,
    invalidate_cache,
    is_book_move,
)
from nodes_selection import label_diff, load_games, quantiles  # noqa: E402

ENV_KEYS = (
    "REVIEW_DETERMINISTIC",
    "REVIEW_NODES",
    "REVIEW_BOOK_NODES",
    "REVIEW_BOOK_SECONDS",
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pgn", default=str(REPO_ROOT / "data" / "gate_set_A.pgn")
    )
    parser.add_argument("--sf-path", default=os.getenv("STOCKFISH_PATH", "stockfish"))
    parser.add_argument("--limit-positions", type=int, default=200)
    parser.add_argument("--limit-games", type=int, default=10)
    parser.add_argument("--plies-per-game", type=int, default=0)
    parser.add_argument("--mode", choices=("deterministic", "old", "both"), default="both")
    parser.add_argument("--nodes", type=int, default=150_000)
    parser.add_argument("--book-nodes", type=int, default=20_000)
    parser.add_argument("--analysis-time", type=float, default=0.5)
    parser.add_argument("--book-seconds", type=float, default=0.1)
    parser.add_argument("--backstop", type=float, default=30.0)
    parser.add_argument("--low-priority", action="store_true")
    parser.add_argument("--json", default=None)
    return parser.parse_args()


def _env_with(overrides):
    """Set env overrides, return a restore callable (no context manager leak)."""
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    for key, value in overrides.items():
        if value is not None:
            os.environ[key] = str(value)
    def restore():
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return restore


def run_pass(
    pgns, sf_path, *, deterministic, nodes, book_nodes, analysis_time, book_seconds
):
    overrides = (
        {
            "REVIEW_DETERMINISTIC": "1",
            "REVIEW_NODES": nodes,
            "REVIEW_BOOK_NODES": book_nodes,
        }
        if deterministic
        else {
            "REVIEW_BOOK_SECONDS": book_seconds,
        }
    )
    restore = _env_with(overrides)
    engine = StockfishEngine(
        stockfish_path=sf_path, depth=18, analysis_time=analysis_time
    )
    engine.start()
    analyzer = GameAnalyzer(
        engine=engine,
        explainer=MockExplainer(),
        book_lookup=is_book_move,
        multipv=2,
        deterministic=deterministic,
    )
    per_game = []
    durations = []
    try:
        for pgn in pgns:
            started = time.monotonic()
            rows = analyzer.analyze_full_game(pgn, include_explanations=False)
            durations.append(time.monotonic() - started)
            per_game.append(
                [
                    {
                        "label": row["classification"],
                        "eval": row.get("eval_cp"),
                        "mate": row.get("eval_mate"),
                        "san": row["san"],
                    }
                    for row in rows[1:]
                ]
            )
    finally:
        engine.close()
        restore()
    return per_game, durations


def book_regions(pgns):
    """Per game: (flags, last_book_index, interior_indices)."""
    regions = []
    for pgn in pgns:
        game = chess.pgn.read_game(StringIO(pgn))
        board = game.board()
        flags = []
        in_book = True
        for move in game.mainline_moves():
            flag = bool(is_book_move(board, move)) if in_book else False
            if not flag:
                in_book = False
            flags.append(flag)
            board.push(move)
        last = max((i for i, flag in enumerate(flags) if flag), default=-1)
        interior = [i for i, flag in enumerate(flags) if flag and i <= last - 2]
        regions.append((flags, last, interior))
    return regions


def flip_details(baseline, candidate, regions, limit=12):
    details = []
    for game_index, (left, right) in enumerate(zip(baseline, candidate)):
        flags, last, interior = regions[game_index]
        for ply_index, (old, new) in enumerate(zip(left, right)):
            if old["label"] == new["label"]:
                continue
            details.append(
                {
                    "game": game_index,
                    "ply": ply_index,
                    "san": new["san"],
                    "old": old["label"],
                    "new": new["label"],
                    "old_eval": old["eval"],
                    "new_eval": new["eval"],
                    "book": bool(flags[ply_index]) if ply_index < len(flags) else False,
                    "interior": ply_index in interior,
                    "exit_ply": ply_index == last + 1,
                }
            )
            if len(details) >= limit:
                return details
    return details


def summarize(name, baseline, candidate, regions):
    diff = label_diff(baseline, candidate)
    interior_evals = 0
    max_eval_delta = 0.0
    for game_index, (left, right) in enumerate(zip(baseline, candidate)):
        _flags, _last, interior = regions[game_index]
        for ply_index in interior:
            old_eval = left[ply_index]["eval"]
            new_eval = right[ply_index]["eval"]
            if isinstance(old_eval, (int, float)) and isinstance(new_eval, (int, float)):
                interior_evals += 1
                max_eval_delta = max(max_eval_delta, abs(old_eval - new_eval))
    print(
        f"  {name}: labels changed {diff['changed']}/{diff['moves']} "
        f"(adjacent {diff['adjacent']}, crossings {diff['severity_crossings']}, "
        f"special {diff['special']}) | interior eval deltas n={interior_evals} "
        f"max={max_eval_delta:.1f}cp"
    )
    flips = flip_details(baseline, candidate, regions)
    for flip in flips:
        print(
            f"    game {flip['game']} ply {flip['ply']} {flip['san']}: "
            f"{flip['old']} -> {flip['new']} "
            f"(eval {flip['old_eval']} -> {flip['new_eval']}, "
            f"book={flip['book']}, interior={flip['interior']}, "
            f"exit={flip['exit_ply']})"
        )
    if diff["changed"] > len(flips):
        print(f"    ... {diff['changed'] - len(flips)} more flips not shown")
    return {**diff, "interior_eval_deltas": interior_evals, "max_interior_eval_delta": max_eval_delta}


def main():
    args = parse_args()
    if args.low_priority:
        os.nice(19)
        print("  (running at nice 19)")

    database.init_db()
    invalidate_cache()
    rows = count_book_rows()
    if rows == 0:
        raise SystemExit(
            "opening book is empty; run scripts/build_opening_book.py first"
        )
    print(f"opening book rows: {rows}")

    pgns = load_games(
        args.pgn, args.limit_positions, args.limit_games, args.plies_per_game
    )
    regions = book_regions(pgns)
    book_total = sum(sum(flags) for flags, _last, _interior in regions)
    interior_total = sum(len(interior) for _flags, _last, interior in regions)
    print(
        f"sample: {len(pgns)} games, {sum(len(f) for f, _l, _i in regions)} plies, "
        f"book plies {book_total}, interior-cheap plies {interior_total}"
    )
    if interior_total == 0:
        raise SystemExit("sample has no interior book plies; nothing to diff")

    results = {
        "sample": {
            "games": len(pgns),
            "plies": sum(len(f) for f, _l, _i in regions),
            "book_plies": book_total,
            "interior_plies": interior_total,
        }
    }

    if args.mode in ("deterministic", "both"):
        full, full_wall = run_pass(
            pgns,
            args.sf_path,
            deterministic=True,
            nodes=args.nodes,
            book_nodes=args.nodes,  # equal budgets = baseline
            analysis_time=args.analysis_time,
            book_seconds=args.book_seconds,
        )
        cheap, cheap_wall = run_pass(
            pgns,
            args.sf_path,
            deterministic=True,
            nodes=args.nodes,
            book_nodes=args.book_nodes,
            analysis_time=args.analysis_time,
            book_seconds=args.book_seconds,
        )
        print(
            f"  deterministic N={args.nodes} vs book-N={args.book_nodes}: "
            f"wall p50 {quantiles(full_wall).get('p50')}s -> "
            f"{quantiles(cheap_wall).get('p50')}s"
        )
        results["deterministic"] = {
            "nodes": args.nodes,
            "book_nodes": args.book_nodes,
            "baseline_wall": quantiles(full_wall),
            "cheap_wall": quantiles(cheap_wall),
            **summarize(
                f"deterministic N={args.nodes}/book={args.book_nodes}",
                full,
                cheap,
                regions,
            ),
        }

    if args.mode in ("old", "both"):
        base1, base1_wall = run_pass(
            pgns,
            args.sf_path,
            deterministic=False,
            nodes=args.nodes,
            book_nodes=args.nodes,
            analysis_time=args.analysis_time,
            book_seconds=args.analysis_time,  # equal = baseline
        )
        base2, _base2_wall = run_pass(
            pgns,
            args.sf_path,
            deterministic=False,
            nodes=args.nodes,
            book_nodes=args.nodes,
            analysis_time=args.analysis_time,
            book_seconds=args.analysis_time,
        )
        cheap, cheap_wall = run_pass(
            pgns,
            args.sf_path,
            deterministic=False,
            nodes=args.nodes,
            book_nodes=args.nodes,
            analysis_time=args.analysis_time,
            book_seconds=args.book_seconds,
        )
        print("  old-mode noise floor (0.5s vs 0.5s):")
        noise = summarize("noise floor", base1, base2, regions)
        print(
            f"  old mode {args.analysis_time}s vs book {args.book_seconds}s: "
            f"wall p50 {quantiles(base1_wall).get('p50')}s -> "
            f"{quantiles(cheap_wall).get('p50')}s"
        )
        results["old_mode"] = {
            "analysis_time": args.analysis_time,
            "book_seconds": args.book_seconds,
            "noise_floor": noise,
            "baseline_wall": quantiles(base1_wall),
            "cheap_wall": quantiles(cheap_wall),
            **summarize("old mode", base1, cheap, regions),
        }

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")

    det = results.get("deterministic")
    if det and (det["changed"] or det["severity_crossings"]):
        print("deterministic diff is NOT clean; do not ship the cheap budget")
        return 1
    if det:
        print("deterministic diff is clean (zero label changes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
