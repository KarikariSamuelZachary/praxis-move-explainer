#!/usr/bin/env python
"""Label check: N games, old batch mode vs deterministic new mode.

Runs both modes through the real GameAnalyzer on the same full games and
reports the old->new confusion matrix, how many labels change by a lot
(severity crossings), and ten mistake->good / inaccuracy->best cases with
FEN, move, both evals and the engine's best move at 1M nodes.

N defaults to REVIEW_NODES (150000). The old-mode pass also reports its
depth p50: below 15 means it was CPU-starved and the result is invalid.

Usage:
  REVIEW_NODES=150000 python scripts/label_check.py --games 20 \
      --sf-path /tmp/opencode/sf19/stockfish
"""
import argparse
import json
import os
import statistics
import sys
from pathlib import Path

import chess

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.game_analyzer import GameAnalyzer  # noqa: E402
from determinism_sweep import NodeEngine  # noqa: E402
from llms.mock_explainer import MockExplainer  # noqa: E402
from nodes_selection import (  # noqa: E402
    SEVERITY,
    SPECIAL,
    TimedNodeEngine,
    TimedStockfishEngine,
    label_diff,
    load_games,
    quantiles,
)

LABELS = [
    "book",
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
]
MIN_OLD_DEPTH_P50 = 15.0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sf-path", default=os.getenv("STOCKFISH_PATH", "stockfish"))
    parser.add_argument("--pgn", default=str(REPO_ROOT / "data" / "gate_set_A.pgn"))
    parser.add_argument(
        "--nodes",
        type=int,
        default=int(os.getenv("REVIEW_NODES", "150000")),
    )
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--backstop", type=float, default=30.0)
    parser.add_argument("--json", default=None)
    return parser.parse_args()


def analyze_full_rows(pgns, engine):
    analyzer = GameAnalyzer(
        engine=engine, explainer=MockExplainer(), multipv=2
    )
    per_game = []
    for pgn in pgns:
        per_game.append(
            analyzer.analyze_full_game(pgn, include_explanations=False)[1:]
        )
    return per_game


def main():
    args = parse_args()
    os.nice(19)
    pgns = load_games(args.pgn, 100000, args.games, 0)
    print(f"label check: {len(pgns)} games, new mode N={args.nodes}, old mode depth18/0.5s")

    new_engine = TimedNodeEngine(
        args.sf_path, nodes=args.nodes, fresh=True, backstop=args.backstop
    )
    try:
        new_mode = analyze_full_rows(pgns, new_engine)
    finally:
        new_engine.close()

    old_engine = TimedStockfishEngine(
        stockfish_path=args.sf_path, depth=18, analysis_time=0.5
    )
    old_engine.start()
    try:
        old_mode = analyze_full_rows(pgns, old_engine)
    finally:
        old_engine.close()

    old_depth = quantiles(old_engine.depths)
    old_idle = (old_depth.get("p50") or 0) >= MIN_OLD_DEPTH_P50
    print(
        f"old mode depth {old_depth} -> "
        f"{'idle and valid' if old_idle else 'CPU-starved, INVALID'}"
    )

    matrix = {left: {right: 0 for right in LABELS} for left in LABELS}
    for old_rows, new_rows in zip(old_mode, new_mode):
        for old_row, new_row in zip(old_rows, new_rows):
            matrix[old_row["classification"]][new_row["classification"]] += 1
    header = "old\\new " + " ".join(f"{label[:4]:>5}" for label in LABELS)
    print(header)
    for left in LABELS:
        print(
            f"{left[:7]:<8}"
            + " ".join(f"{matrix[left][right]:>5}" for right in LABELS)
        )

    diff = label_diff(
        [[{"label": r["classification"], "mate": r.get("eval_mate")} for r in g] for g in old_mode],
        [[{"label": r["classification"], "mate": r.get("eval_mate")} for r in g] for g in new_mode],
    )
    moves = diff["moves"] or 1
    print(
        f"changed={diff['changed']}/{diff['moves']} "
        f"({diff['changed'] / moves:.1%}) | "
        f"adjacent={diff['adjacent']} | "
        f"changed_a_lot={diff['severity_crossings']} "
        f"({diff['severity_crossings'] / moves:.1%}) | special={diff['special']}"
    )

    wanted = []
    for game_index, (old_rows, new_rows) in enumerate(zip(old_mode, new_mode)):
        for ply_index, (old_row, new_row) in enumerate(zip(old_rows, new_rows)):
            pair = (old_row["classification"], new_row["classification"])
            if pair in (("mistake", "good"), ("inaccuracy", "best")):
                wanted.append((game_index, ply_index, old_row, new_row))
                if len(wanted) >= 10:
                    break
        if len(wanted) >= 10:
            break

    reference = NodeEngine(
        args.sf_path, nodes=1_000_000, fresh=True, backstop=30.0
    )
    try:
        for game_index, ply_index, old_row, new_row in wanted:
            before = chess.Board(old_row["fen_before"])
            evaluation = reference.evaluate(before, multipv=2)
            if evaluation.mate is not None:
                best = f"mate {evaluation.mate}"
            else:
                best = f"{evaluation.best_move_san} ({evaluation.score_cp:.0f} cp)"
            print(
                f"  game {game_index} ply {ply_index} {old_row['san']} "
                f"{old_row['classification']} -> {new_row['classification']}: "
                f"fen={old_row['fen_before']} "
                f"old_eval={old_row['eval_cp']}/{old_row['eval_mate']} "
                f"new_eval={new_row['eval_cp']}/{new_row['eval_mate']} "
                f"1M_best={best}"
            )
    finally:
        reference.close()

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "games": len(pgns),
                    "nodes": args.nodes,
                    "old_mode_depth": old_depth,
                    "old_mode_idle_valid": old_idle,
                    "matrix": matrix,
                    "diff": diff,
                    "upgrades": [
                        {
                            "game": game_index,
                            "ply": ply_index,
                            "san": old_row["san"],
                            "fen_before": old_row["fen_before"],
                            "old": old_row["classification"],
                            "new": new_row["classification"],
                            "old_eval_cp": old_row["eval_cp"],
                            "old_eval_mate": old_row["eval_mate"],
                            "new_eval_cp": new_row["eval_cp"],
                            "new_eval_mate": new_row["eval_mate"],
                        }
                        for game_index, ply_index, old_row, new_row in wanted
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
