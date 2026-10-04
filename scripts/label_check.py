#!/usr/bin/env python
"""Small label check: N games, old batch mode vs deterministic new mode.

Runs both modes through the real GameAnalyzer on the same games and reports
how many labels change, split into adjacent flips and severity crossings
("changed by a lot"). N defaults to REVIEW_NODES (150000 for the local
sandbox run).

Usage:
  REVIEW_NODES=150000 python scripts/label_check.py --games 20 \
      --sf-path /tmp/opencode/sf19/stockfish
"""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nodes_selection import (  # noqa: E402
    SPECIAL,
    SEVERITY,
    label_diff,
    load_games,
    run_budget,
    run_old_mode,
)


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
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--backstop", type=float, default=30.0)
    return parser.parse_args()


def main():
    args = parse_args()
    os.nice(19)
    pgns = load_games(args.pgn, 100000, args.games, 0)
    print(f"label check: {len(pgns)} games, new mode N={args.nodes}, old mode depth18/0.5s")

    new_mode, _, _ = run_budget(
        pgns,
        args.sf_path,
        args.nodes,
        args.backstop,
        jobs=args.jobs,
        low_priority=True,
    )
    old_mode, _, _ = run_old_mode(
        pgns, args.sf_path, 18, 0.5, low_priority=True
    )

    diff = label_diff(old_mode, new_mode)
    moves = diff["moves"] or 1
    print(
        f"changed={diff['changed']}/{diff['moves']} "
        f"({diff['changed'] / moves:.1%}) | "
        f"adjacent={diff['adjacent']} | "
        f"changed_a_lot={diff['severity_crossings']} "
        f"({diff['severity_crossings'] / moves:.1%}) | special={diff['special']}"
    )
    for game_index, (old_rows, new_rows) in enumerate(zip(old_mode, new_mode)):
        for ply_index, (old_row, new_row) in enumerate(zip(old_rows, new_rows)):
            left, right = old_row["label"], new_row["label"]
            if left == right or left in SPECIAL or right in SPECIAL:
                continue
            if abs(SEVERITY[left] - SEVERITY[right]) >= 2:
                print(
                    f"  game {game_index} ply {ply_index}: "
                    f"{left} -> {right}"
                )


if __name__ == "__main__":
    main()
