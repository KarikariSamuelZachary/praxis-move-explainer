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
import csv
import json
import multiprocessing
import os
import random
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
    parser.add_argument(
        "--reference-floor",
        type=int,
        default=0,
        help="Second reference (e.g. 1000000) to measure reference stability.",
    )
    parser.add_argument("--limit-positions", type=int, default=200)
    parser.add_argument(
        "--limit-games",
        type=int,
        default=10,
        help="Games sampled evenly across the file; 0 = full set.",
    )
    parser.add_argument(
        "--plies-per-game",
        type=int,
        default=0,
        help="Cap plies per game; 0 = derive from limit-positions.",
    )
    parser.add_argument("--backstop", type=float, default=30.0)
    parser.add_argument("--noise-floor", action="store_true")
    parser.add_argument(
        "--order",
        choices=["normal", "reversed", "random"],
        default="reversed",
        help="Second-run order for the noise floor.",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--mate-csv",
        default=str(REPO_ROOT / "praxis_subset.csv"),
        help="Puzzle CSV used for the mate-rich recall sample.",
    )
    parser.add_argument("--mate-limit", type=int, default=100)
    parser.add_argument(
        "--mate-recall",
        action="store_true",
        help="Run the mate-rich recall sample instead of the label sweep.",
    )
    parser.add_argument(
        "--warm-breakout",
        action="store_true",
        help="Report warm-TT-only mates and warm/fresh label changes per N.",
    )
    parser.add_argument("--low-priority", action="store_true")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Parallel workers; shards are contiguous so order is preserved.",
    )
    parser.add_argument(
        "--reference-cache",
        default=None,
        help="JSON file to save/reuse the reference per-game rows, so a long "
        "1M reference can be computed once and candidates run separately.",
    )
    parser.add_argument(
        "--old-mode",
        action="store_true",
        help="Also run the shipped depth+time batch config against the reference.",
    )
    parser.add_argument("--old-depth", type=int, default=18)
    parser.add_argument("--old-time", type=float, default=0.5)
    parser.add_argument("--json", default=None)
    return parser.parse_args()


class TimedNodeEngine(NodeEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.durations = []
        self.depths = []

    def evaluate(self, board, depth_limit=None, pov=None, time_limit=None, multipv=1):
        started = time.monotonic()
        try:
            evaluation = super().evaluate(
                board,
                depth_limit=depth_limit,
                pov=pov,
                time_limit=time_limit,
                multipv=multipv,
            )
        finally:
            self.durations.append(time.monotonic() - started)
        self.depths.append(getattr(evaluation, "depth", None))
        return evaluation


class TimedStockfishEngine(StockfishEngine):
    """Old batch config (depth 18 + 0.5s) with per-eval time/depth capture."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.durations = []
        self.depths = []

    def evaluate(
        self,
        board,
        depth_limit=None,
        pov=None,
        time_limit=None,
        multipv=1,
        nodes=None,
        fresh_token=False,
    ):
        started = time.monotonic()
        try:
            evaluation = super().evaluate(
                board,
                depth_limit=depth_limit,
                pov=pov,
                time_limit=time_limit,
                multipv=multipv,
                nodes=nodes,
                fresh_token=fresh_token,
            )
        finally:
            self.durations.append(time.monotonic() - started)
        self.depths.append(getattr(evaluation, "depth", None))
        return evaluation


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


def load_games(path, limit_positions, limit_games, plies_per_game=0):
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
    if plies_per_game > 0:
        per_game = plies_per_game
    else:
        per_game = max(10, limit_positions // len(games))
    return [str(_truncate_game(game, per_game)) for game in games]


def _budget_shard(payload):
    pgns, sf_path, nodes, backstop, low_priority, fresh = payload
    if low_priority:
        os.nice(19)
    engine = TimedNodeEngine(sf_path, nodes=nodes, fresh=fresh, backstop=backstop)
    analyzer = GameAnalyzer(engine=engine, explainer=MockExplainer(), multipv=2)
    per_game = []
    try:
        for pgn in pgns:
            rows = analyzer.analyze_full_game(pgn, include_explanations=False)[1:]
            per_game.append(
                [
                    {"label": row["classification"], "mate": row.get("eval_mate")}
                    for row in rows
                ]
            )
    finally:
        engine.close()
    return per_game, engine.durations, engine.depths


def run_budget(
    pgns, sf_path, nodes, backstop, jobs=1, low_priority=False, fresh=True
):
    if jobs <= 1:
        return _budget_shard((pgns, sf_path, nodes, backstop, low_priority, fresh))

    shard_size = (len(pgns) + jobs - 1) // jobs
    shards = [
        pgns[index : index + shard_size] for index in range(0, len(pgns), shard_size)
    ]
    payloads = [
        (shard, sf_path, nodes, backstop, low_priority, fresh) for shard in shards
    ]
    with multiprocessing.Pool(min(jobs, len(shards))) as pool:
        results = pool.map(_budget_shard, payloads)

    per_game = [game for result, _, _ in results for game in result]
    durations = [
        duration for _, shard_durations, _ in results for duration in shard_durations
    ]
    depths = [depth for _, _, shard_depths in results for depth in shard_depths]
    return per_game, durations, depths


def quantiles(values):
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return {}

    def pick(q):
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    return {
        "p50": round(pick(0.50), 3),
        "p90": round(pick(0.90), 3),
        "p95": round(pick(0.95), 3),
    }


def cluster_bootstrap_ci(reference, candidate, iters=2000, seed=7):
    """Game-level bootstrap 95% CI for the severity-crossing rate."""
    per_game = []
    for ref_game, cand_game in zip(reference, candidate):
        crossings = moves = 0
        for ref_row, cand_row in zip(ref_game, cand_game):
            moves += 1
            left, right = ref_row["label"], cand_row["label"]
            if left == right or left in SPECIAL or right in SPECIAL:
                continue
            if abs(SEVERITY[left] - SEVERITY[right]) >= 2:
                crossings += 1
        per_game.append((crossings, moves))
    if not per_game:
        return {"low": 0.0, "high": 0.0}
    rng = random.Random(seed)
    rates = []
    for _ in range(iters):
        crossings = total = 0
        for _ in range(len(per_game)):
            game_crossings, game_moves = per_game[rng.randrange(len(per_game))]
            crossings += game_crossings
            total += game_moves
        rates.append(crossings / total if total else 0.0)
    rates.sort()
    return {
        "low": rates[int(0.025 * iters)],
        "high": rates[min(iters - 1, int(0.975 * iters))],
    }


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
        "severity": label_diff(reference, candidate),
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


def run_old_mode(pgns, sf_path, depth, analysis_time, low_priority=False):
    """The shipped batch config: depth 18 + 0.5s, no fresh token."""
    if low_priority:
        os.nice(19)
    engine = TimedStockfishEngine(
        stockfish_path=sf_path, depth=depth, analysis_time=analysis_time
    )
    engine.start()
    analyzer = GameAnalyzer(engine=engine, explainer=MockExplainer(), multipv=2)
    per_game = []
    try:
        for pgn in pgns:
            rows = analyzer.analyze_full_game(pgn, include_explanations=False)[1:]
            per_game.append(
                [
                    {"label": row["classification"], "mate": row.get("eval_mate")}
                    for row in rows
                ]
            )
    finally:
        engine.close()
    return per_game, engine.durations, engine.depths


def run_nodes_sweep(args, pgns):
    reference = None
    if args.reference_cache and Path(args.reference_cache).exists():
        cached = json.loads(Path(args.reference_cache).read_text(encoding="utf-8"))
        if cached.get("nodes") == args.reference_nodes:
            reference = cached["per_game"]
    if reference is not None:
        print(f"reference: {args.reference_nodes} nodes (cache {args.reference_cache})")
    else:
        reference, _, _ = run_budget(
            pgns,
            args.sf_path,
            args.reference_nodes,
            args.backstop,
            jobs=args.jobs,
            low_priority=args.low_priority,
        )
        if args.reference_cache:
            Path(args.reference_cache).write_text(
                json.dumps({"nodes": args.reference_nodes, "per_game": reference}),
                encoding="utf-8",
            )
            print(f"wrote reference cache {args.reference_cache}")
        print(f"reference: {args.reference_nodes} nodes")
    results = {"reference": args.reference_nodes, "jobs": args.jobs}
    mate_reference = reference
    long_mates = []
    if args.reference_floor:
        floor_reference, _, _ = run_budget(
            pgns,
            args.sf_path,
            args.reference_floor,
            args.backstop,
            jobs=args.jobs,
            low_priority=args.low_priority,
        )
        mate_reference = floor_reference
        floor = label_diff(reference, floor_reference)
        results["reference_floor"] = {
            "nodes": args.reference_floor,
            **floor,
            "long_mates": len(long_mates),
        }
        print(
            f"  reference floor {args.reference_nodes} vs {args.reference_floor}: "
            f"changed={floor['changed']}/{floor['moves']} "
            f"adjacent={floor['adjacent']} "
            f"crossings={floor['severity_crossings']} special={floor['special']}"
        )

    for game_index, game_rows in enumerate(mate_reference):
        for ply_index, row in enumerate(game_rows):
            mate = row["mate"]
            if mate is not None and abs(mate) >= 6:
                long_mates.append((game_index, ply_index))
    if long_mates:
        print(f"  long mates (>=6) in reference: {len(long_mates)}")

    for budget in args.budgets:
        candidate, durations, depths = run_budget(
            pgns,
            args.sf_path,
            budget,
            args.backstop,
            jobs=args.jobs,
            low_priority=args.low_priority,
        )
        comparison = compare_to_reference(reference, candidate)
        severity = comparison["severity"]
        moves = severity["moves"] or 1
        crossing_ci = cluster_bootstrap_ci(reference, candidate, seed=args.seed)
        long_mate_changes = 0
        long_mate_kept = 0
        for game_index, ply_index in long_mates:
            if mate_reference[game_index][ply_index]["label"] != candidate[game_index][ply_index]["label"]:
                long_mate_changes += 1
            if candidate[game_index][ply_index]["mate"] is not None:
                long_mate_kept += 1
        results[budget] = {
            "agreement": comparison["agreement"],
            "mate_recall": comparison["mate_recall"],
            "mate_positions": comparison["mate_positions"],
            "moves": comparison["moves"],
            "adjacent": severity["adjacent"],
            "severity_crossings": severity["severity_crossings"],
            "crossing_rate": severity["severity_crossings"] / moves,
            "crossing_ci": crossing_ci,
            "special": severity["special"],
            "long_mate_label_changes": long_mate_changes,
            "long_mate_kept": long_mate_kept,
            "wall": quantiles(durations),
            "depth": quantiles(depths),
        }
        recall = (
            f"{comparison['mate_recall']:.3f}"
            if comparison["mate_recall"] is not None
            else "n/a"
        )
        long_mate_note = (
            f" long_mates={len(long_mates)}"
            f"(label_changes={long_mate_changes},kept={long_mate_kept})"
            if long_mates
            else ""
        )
        print(
            f"  {budget:>7}: agreement={comparison['agreement']:.4f} "
            f"crossing_rate={results[budget]['crossing_rate']:.4f} "
            f"ci=[{crossing_ci['low']:.4f},{crossing_ci['high']:.4f}] "
            f"adjacent={severity['adjacent']} "
            f"crossings={severity['severity_crossings']} "
            f"special={severity['special']} "
            f"depth={results[budget]['depth']} "
            f"mate_recall={recall}{long_mate_note} "
            f"wall p50={results[budget]['wall'].get('p50')}s "
            f"p95={results[budget]['wall'].get('p95')}s"
        )

    if args.old_mode:
        old_mode, old_durations, old_depths = run_old_mode(
            pgns,
            args.sf_path,
            args.old_depth,
            args.old_time,
            low_priority=args.low_priority,
        )
        comparison = compare_to_reference(reference, old_mode)
        severity = comparison["severity"]
        moves = severity["moves"] or 1
        crossing_ci = cluster_bootstrap_ci(reference, old_mode, seed=args.seed)
        results["old_mode"] = {
            "depth_limit": args.old_depth,
            "analysis_time": args.old_time,
            "agreement": comparison["agreement"],
            "severity_crossings": severity["severity_crossings"],
            "crossing_rate": severity["severity_crossings"] / moves,
            "crossing_ci": crossing_ci,
            "adjacent": severity["adjacent"],
            "special": severity["special"],
            "depth": quantiles(old_depths),
            "wall": quantiles(old_durations),
        }
        print(
            f"  old mode (depth {args.old_depth}, {args.old_time}s): "
            f"agreement={comparison['agreement']:.4f} "
            f"crossing_rate={results['old_mode']['crossing_rate']:.4f} "
            f"ci=[{crossing_ci['low']:.4f},{crossing_ci['high']:.4f}] "
            f"crossings={severity['severity_crossings']} "
            f"adjacent={severity['adjacent']} special={severity['special']} "
            f"depth={results['old_mode']['depth']} "
            f"wall p50={results['old_mode']['wall'].get('p50')}s "
            f"p95={results['old_mode']['wall'].get('p95')}s"
        )
    return results


def load_mate_positions(csv_path, limit):
    """Lichess puzzle FENs are the position BEFORE the opponent's blunder and
    `moves[0]` is that blunder, so replay it to get the solver's position."""
    positions = []
    with open(csv_path, encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            themes = row.get("themes", "")
            mate_in = None
            for candidate in ("mateIn2", "mateIn3", "mateIn4"):
                if candidate in themes:
                    mate_in = int(candidate[-1])
                    break
            if mate_in is None:
                continue
            moves = row.get("moves", "").split()
            if not moves:
                continue
            try:
                board = chess.Board(row.get("fen", ""))
                board.push_uci(moves[0])
            except ValueError:
                continue
            positions.append((row.get("id"), board.fen(), mate_in))
    if limit and len(positions) > limit:
        step = len(positions) / limit
        positions = [positions[int(i * step)] for i in range(limit)]
    return positions


def run_mate_recall(args):
    positions = load_mate_positions(args.mate_csv, args.mate_limit)
    if not positions:
        raise SystemExit(f"no mateIn2/3/4 positions found in {args.mate_csv}")
    print(f"mate-rich sample: {len(positions)} positions from {args.mate_csv}")
    results = {}
    for budget in list(args.budgets) + [args.reference_nodes]:
        engine = NodeEngine(
            args.sf_path, nodes=budget, fresh=True, backstop=args.backstop
        )
        found = 0
        exact = 0
        try:
            for _, fen, expected in positions:
                board = chess.Board(fen)
                evaluation = engine.evaluate(board, multipv=2)
                if evaluation.mate is not None:
                    found += 1
                    if evaluation.mate > 0 and evaluation.mate <= expected:
                        exact += 1
        finally:
            engine.close()
        recall = found / len(positions)
        results[budget] = {"recall": recall, "exact_or_faster": exact}
        print(
            f"  {budget:>7}: mate recall {found}/{len(positions)} = {recall:.3f} "
            f"(distance <= expected: {exact})"
        )
    return results


def run_noise_floor(args, pgns):
    def batch_run(ordered):
        engine = StockfishEngine(
            stockfish_path=args.sf_path, depth=18, analysis_time=0.5
        )
        engine.start()
        analyzer = GameAnalyzer(engine=engine, explainer=MockExplainer(), multipv=2)
        per_game = {}
        try:
            for index, pgn in ordered:
                rows = analyzer.analyze_full_game(
                    pgn, include_explanations=False
                )[1:]
                per_game[index] = [
                    {"label": row["classification"], "mate": row.get("eval_mate")}
                    for row in rows
                ]
        finally:
            engine.close()
        return per_game

    indexed = list(enumerate(games))
    if args.order == "reversed":
        second_order = list(reversed(indexed))
    elif args.order == "random":
        second_order = indexed[:]
        random.Random(args.seed).shuffle(second_order)
    else:
        second_order = indexed[:]

    first = batch_run(indexed)
    second = batch_run(second_order)
    aligned_first = [first[i] for i in range(len(pgns))]
    aligned_second = [second[i] for i in range(len(pgns))]
    diff = label_diff(aligned_first, aligned_second)
    print(
        f"old-vs-old noise floor (depth 18, 0.5s, {args.order} order): "
        f"{diff['changed']}/{diff['moves']} changed "
        f"(adjacent {diff['adjacent']}, crossings {diff['severity_crossings']}, "
        f"special {diff['special']})"
    )
    return diff


def run_warm_breakout(args, pgns):
    """Fresh vs warm-TT at each N: where does a warm table find mate the
    fresh run misses, and do those labels change?"""
    print(f"warm breakout: {len(pgns)} games, budgets={args.budgets}")
    results = {}
    for budget in args.budgets:
        fresh, _, _ = run_budget(
            pgns,
            args.sf_path,
            budget,
            args.backstop,
            jobs=args.jobs,
            low_priority=args.low_priority,
            fresh=True,
        )
        warm, _, _ = run_budget(
            pgns,
            args.sf_path,
            budget,
            args.backstop,
            jobs=args.jobs,
            low_priority=args.low_priority,
            fresh=False,
        )
        warm_only = []
        fresh_only = 0
        label_changes = 0
        total = 0
        for game_index, rows in enumerate(fresh):
            for ply_index, fresh_row in enumerate(rows):
                total += 1
                warm_row = warm[game_index][ply_index]
                if fresh_row["label"] != warm_row["label"]:
                    label_changes += 1
                if warm_row["mate"] is not None and fresh_row["mate"] is None:
                    warm_only.append(
                        {
                            "game": game_index,
                            "ply": ply_index,
                            "warm_mate": warm_row["mate"],
                            "fresh_label": fresh_row["label"],
                            "warm_label": warm_row["label"],
                        }
                    )
                if fresh_row["mate"] is not None and warm_row["mate"] is None:
                    fresh_only += 1
        results[budget] = {
            "moves": total,
            "label_changes": label_changes,
            "warm_only_mates": len(warm_only),
            "fresh_only_mates": fresh_only,
            "positions": warm_only,
        }
        print(
            f"  {budget:>7}: label_changes={label_changes}/{total} "
            f"warm_only_mates={len(warm_only)} fresh_only_mates={fresh_only}"
        )
        for item in warm_only[:8]:
            print(
                f"    warm-only mate: game={item['game']} ply={item['ply']} "
                f"mate={item['warm_mate']} fresh={item['fresh_label']} "
                f"warm={item['warm_label']}"
            )
    return results


def main():
    args = parse_args()
    if args.low_priority:
        os.nice(19)
        print("  (running at nice 19)")

    results = {}
    if args.mate_recall:
        results["mate_recall"] = run_mate_recall(args)
    elif args.warm_breakout:
        pgns = load_games(
            args.pgn,
            args.limit_positions,
            args.limit_games,
            args.plies_per_game,
        )
        print(f"pgn={args.pgn} games={len(pgns)}")
        results["warm_breakout"] = run_warm_breakout(args, pgns)
    else:
        pgns = load_games(
            args.pgn,
            args.limit_positions,
            args.limit_games,
            args.plies_per_game,
        )
        print(f"pgn={args.pgn} games={len(pgns)}")
        if not args.noise_floor:
            results["nodes"] = run_nodes_sweep(args, pgns)
        else:
            results["noise_floor"] = run_noise_floor(args, pgns)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
