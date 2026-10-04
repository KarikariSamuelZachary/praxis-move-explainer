#!/usr/bin/env python
"""First-touch label time for new sandbox positions.

Times 20 fresh middle-game positions through POST /api/review/live with a
cold cache per call (eval, result and limiter stores cleared) and reports
the median and slowest request. Same mode the sandbox ships with.

Run with the deterministic mode on:
  cd src
  REVIEW_DETERMINISTIC=1 REVIEW_NODES=150000 \
  STOCKFISH_PATH=/tmp/opencode/sf19/stockfish \
  ../venv/bin/python ../scripts/first_touch_timing.py --positions 20
"""
import argparse
import os
import statistics
import sys
import time
from pathlib import Path

import chess
import chess.pgn

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import main as app_module  # noqa: E402
import routers.review as review_module  # noqa: E402
from core import rate_limit  # noqa: E402
from core.analysis_mode import (  # noqa: E402
    REVIEW_MULTIPV,
    current_mode_string,
    review_nodes,
)
from engines.stockfish_engine import (  # noqa: E402
    get_review_engine_name,
    get_review_stockfish,
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pgn", default=str(REPO_ROOT / "data" / "gate_set_A.pgn")
    )
    parser.add_argument("--positions", type=int, default=20)
    parser.add_argument("--at-ply", type=int, default=20)
    return parser.parse_args()


def fresh_positions(path, count, at_ply):
    """(path sans, move san) pairs for positions never evaluated here."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        while len(out) < count:
            game = chess.pgn.read_game(fh)
            if game is None:
                break
            board = game.board()
            sans = []
            for index, move in enumerate(game.mainline_moves()):
                if index == at_ply:
                    move_san = board.san(move)
                    out.append((list(sans), move_san))
                    break
                sans.append(board.san(move))
                board.push(move)
    return out


def main():
    args = parse_args()
    positions = fresh_positions(args.pgn, args.positions, args.at_ply)
    if not positions:
        raise SystemExit("no positions sampled")

    get_review_stockfish(depth=int(os.getenv("REVIEW_DEPTH", "18")))
    mode = current_mode_string(
        engine_name=get_review_engine_name(),
        multipv=REVIEW_MULTIPV,
        nodes=review_nodes(),
    )
    client = TestClient(app_module.app)
    headers = {
        "X-Internal-Secret": os.environ["INTERNAL_SECRET"],
        "X-Clerk-User-Id": "first-touch-timing",
    }

    timings = []
    for path, move in positions:
        review_module._SANDBOX_EVAL_CACHE.clear()
        review_module._SANDBOX_RESULT_CACHE.clear()
        rate_limit._memory_counters.clear()
        started = time.monotonic()
        response = client.post(
            "/api/review/live",
            json={
                "moves": path,
                "move": move,
                "expected_mode": mode,
            },
            headers=headers,
        )
        timings.append(time.monotonic() - started)
        assert response.status_code == 200, response.text[:200]

    ordered = sorted(timings)
    print(f"first-touch label time over {len(timings)} new positions:")
    print(f"  median={statistics.median(timings):.2f}s "
          f"slowest={max(timings):.2f}s "
          f"p90={ordered[int(0.90 * (len(ordered) - 1))]:.2f}s")
    print(f"  mode={mode}")


if __name__ == "__main__":
    main()
