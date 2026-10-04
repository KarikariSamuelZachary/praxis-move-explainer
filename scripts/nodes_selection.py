#!/usr/bin/env python
"""Choose the review nodes budget (N) and measure the old-mode noise floor.

Part 1 (default): run fresh-token, fixed-nodes GameAnalyzer labels at each
budget and compare against a high-node fresh reference. Reports per-budget
label agreement, mate recall and per-position wall time. Results are
identical at any N; only quality and latency trade off.

Part 2 (--noise-floor): run the REAL batch config (depth 18, 0.5s) twice,
normal and reversed game order, and report label changes -- the old-vs-old
floor the gate must be judged against.

Usage:
  python scripts/nodes_selection.py --pgn data/gate_set_A.pgn \
      --budgets 60000 100000 150000 300000 --reference-nodes 400000 \
      --limit-positions 200 --low-priority
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import chess
import chess.pgn

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.game_analyzer import GameAnalyzer  # noqa: E402
from llms.mock_explainer import MockExplainer  # noqa: E402
from engines.stockfish_engine import StockfishEngine  # noqa: E402
from determinism_sweep import NodeEngine, SEVERITY, SPECIAL  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sf-path", default=os.getenv("STOCKFISH_PATH", "stockfish"))
    parser.add_argument("--pgn", default=str(REPO_ROOT / "data" / "gate_set_A.pgn"))
    parser.add_argument(
        "--budgets", nargs="*", type=int, default=[60_000, 100_000, 150_000, 300_000]
    )
    parser.add_argument("--reference-nodes", type=int, default=400_000)
    parser.add_argument("--limit-positions", type=int, default=200)
    parser.add_argument(
        "--limit-games",
        type=int,
        default=10,
        help="Games sampled evenly across the file; plies are capped per game.",
    )
    parser.add_argument("--backstop", type=float, default=30.0)
    parser.add_argument("--noise-floor", action="store_true")
    parser.add_argument("--low-priority", action="store_true")
    parser.add_argument("--json", default=None)
    return parser.parse_args()


class TimedNodeEngine(NodeEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.durations = []

    def evaluate(self, board, depth_limit=None, pov=None, time_limit=None, multipv=1):
        started = time.monotonic()
        try:
            return super().evaluate(
                board,
                depth_limit=depth_limit,
                pov=pov,
                time_limit=time_limit,
                multipv=multipv,
            )
        finally:
            self.durations.append(time.monotonic() - started)


def _truncate_game(game, max_plies):
    """Copy headers and the first max_plies moves into a new PGN game."""
    truncated = chess.pgn.Game()
    truncated.headers = game.headers.copy()
    board = game.board()
    node = truncated
    for index, move in enumerate(game.mainline_moves()):
        if index >= max_plies:
            break
        board.push(move)
        node = node.add_main_variation(move)
    return truncated


def load_games(path, limit_positions, limit_games):
    """Sample games evenly and cap plies per game, so the sample spans
    opponents instead of exhausting the first two long games."""
    games = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        while True:
            game = chess.pgn.read_game(fh)
            if game is None:
                break
            games.append(game)
    if not games:
        return []
    if limit_games and len(games) > limit_games:
        step = len(games) / limit_games
        games = [games[int(i * step)] for i in range(limit_games)]
    per_game = max(10, limit_positions // len(games))
    return [_truncate_game(game, per_game) for game in games]


def run_budget(games, sf_path, nodes, backstop):
    engine = TimedNodeEngine(sf_path, nodes=nodes, fresh=True, backstop=backstop)
    analyzer = GameAnalyzer(engine=engine, explainer=MockExplainer(), multipv=2)
    per_game = []
    try:
        for game in games:
            rows = analyzer.analyze_full_game(str(game), include_explanations=False)[1:]
            per_game.append(
                [
                    {"label": row["classification"], "mate": row.get("eval_mate")}
                    for row in rows
                ]
            )
    finally:
        engine.close()
    return per_game, engine.durations


def quantiles(values):
    if not values:
        return {}
    ordered = sorted(values)

    def pick(q):
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    return {"p50": round(pick(0.50), 3), "p95": round(pick(0.95), 3)}


def compare_to_reference(reference, candidate):
    total = matched = mates_total = mates_found = 0
    for ref_game, cand_game in zip(reference, candidate):
        for ref_row, cand_row in zip(ref_game, cand_game):
            total += 1
            if ref_row["label"] == cand_row["label"]:
                matched += 1
            if ref_row["mate"] is not None:
                mates_total += 1
                if cand_row["mate"] is not None:
                    mates_found += 1
    return {
        "moves": total,
        "agreement": matched / total if total else 0.0,
        "mate_recall": mates_found / mates_total if mates_total else None,
        "mate_positions": mates_total,
    }


def label_diff(reference, candidate):
    changed = adjacent = crossings = special = 0
    total = 0
    for ref_game, cand_game in zip(reference, candidate):
        for ref_row, cand_row in zip(ref_game, cand_game):
            total += 1
            left, right = ref_row["label"], cand_row["label"]
            if left == right:
                continue
            changed += 1
            if left in SPECIAL or right in SPECIAL:
                special += 1
                continue
            delta = abs(SEVERITY[left] - SEVERITY[right])
            if delta == 1:
                adjacent += 1
            elif delta >= 2:
                crossings += 1
    return {
        "moves": total,
        "changed": changed,
        "adjacent": adjacent,
        "severity_crossings": crossings,
        "special": special,
    }


def run_nodes_sweep(args, games):
    reference, _ = run_budget(
        games, args.sf_path, args.reference_nodes, args.backstop
    )
    print(f"reference: {args.reference_nodes} nodes")
    results = {}
    for budget in args.budgets:
        candidate, durations = run_budget(games, args.sf_path, budget, args.backstop)
        comparison = compare_to_reference(reference, candidate)
        results[budget] = {
            "agreement": comparison["agreement"],
            "mate_recall": comparison["mate_recall"],
            "mate_positions": comparison["mate_positions"],
            "moves": comparison["moves"],
            "wall": quantiles(durations),
        }
        recall = (
            f"{comparison['mate_recall']:.3f}"
            if comparison["mate_recall"] is not None
            else "n/a"
        )
        print(
            f"  {budget:>7}: agreement={comparison['agreement']:.4f} "
            f"mate_recall={recall} "
            f"wall p50={results[budget]['wall'].get('p50')}s "
            f"p95={results[budget]['wall'].get('p95')}s"
        )
    return results


def run_noise_floor(args, games):
    def batch_run(ordered):
        engine = StockfishEngine(
            stockfish_path=args.sf_path, depth=18, analysis_time=0.5
        )
        engine.start()
        analyzer = GameAnalyzer(engine=engine, explainer=MockExplainer(), multipv=2)
        per_game = {}
        try:
            for index, game in ordered:
                rows = analyzer.analyze_full_game(
                    str(game), include_explanations=False
                )[1:]
                per_game[index] = [
                    {"label": row["classification"], "mate": row.get("eval_mate")}
                    for row in rows
                ]
        finally:
            engine.close()
        return per_game

    indexed = list(enumerate(games))
    normal = batch_run(indexed)
    reversed_run = batch_run(list(reversed(indexed)))

    aligned_reference = [normal[i] for i in range(len(games))]
    aligned_candidate = [reversed_run[i] for i in range(len(games))]
    diff = label_diff(aligned_reference, aligned_candidate)
    print(
        "old-vs-old noise floor (depth 18, 0.5s, reversed order): "
        f"{diff['changed']}/{diff['moves']} changed "
        f"(adjacent {diff['adjacent']}, crossings {diff['severity_crossings']}, "
        f"special {diff['special']})"
    )
    return diff


def main():
    args = parse_args()
    if args.low_priority:
        os.nice(19)
        print("  (running at nice 19)")

    games = load_games(args.pgn, args.limit_positions, args.limit_games)
    print(f"pgn={args.pgn} games={len(games)}")
    results = {}
    if not args.noise_floor:
        results["nodes"] = run_nodes_sweep(args, games)
    else:
        results["noise_floor"] = run_noise_floor(args, games)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
