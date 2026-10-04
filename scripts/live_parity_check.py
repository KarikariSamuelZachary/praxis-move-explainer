#!/usr/bin/env python
"""Parity check: live sandbox labels vs batch review labels, same mode.

For each of N games, replays every mainline move through POST /api/review/live
with a COLD cache (eval and result caches cleared before every call) and
compares the label to the batch review label for the same move. Any mismatch
means the sandbox and batch paths disagree.

Run with the deterministic mode on:
  cd src
  REVIEW_DETERMINISTIC=1 REVIEW_NODES=150000 \
  STOCKFISH_PATH=/tmp/opencode/sf19/stockfish \
  ../venv/bin/python ../scripts/live_parity_check.py --games 5
"""
import argparse
import os
import sys
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
from core.game_analyzer import GameAnalyzer  # noqa: E402
from engines.stockfish_engine import (  # noqa: E402
    get_review_engine_name,
    get_review_stockfish,
)
from llms.mock_explainer import MockExplainer  # noqa: E402
from services.opening_book import is_book_move  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pgn", default=str(REPO_ROOT / "data" / "gate_set_A.pgn")
    )
    parser.add_argument("--games", type=int, default=5)
    parser.add_argument("--max-plies", type=int, default=0, help="0 = full games")
    return parser.parse_args()


def batch_labels(pgn: str, max_plies: int):
    engine = get_review_stockfish(depth=int(os.getenv("REVIEW_DEPTH", "18")))
    analyzer = GameAnalyzer(
        engine=engine,
        explainer=MockExplainer(),
        book_lookup=is_book_move,
        multipv=REVIEW_MULTIPV,
        deterministic=True,
    )
    rows = analyzer.analyze_full_game(pgn, include_explanations=False)[1:]
    if max_plies:
        rows = rows[:max_plies]
    return rows


def main():
    args = parse_args()
    if not review_module.review_deterministic_enabled():
        raise SystemExit("REVIEW_DETERMINISTIC must be on for this check")

    games = []
    with open(args.pgn, encoding="utf-8", errors="replace") as fh:
        while len(games) < args.games:
            game = chess.pgn.read_game(fh)
            if game is None:
                break
            games.append(game)
    print(f"parity check: {len(games)} games from {args.pgn}")

    # Start the singleton first: the engine name is part of the mode, and a
    # pre-start "unknown" would make every explore look stale.
    get_review_stockfish(depth=int(os.getenv("REVIEW_DEPTH", "18")))
    mode = current_mode_string(
        engine_name=get_review_engine_name(),
        multipv=REVIEW_MULTIPV,
        nodes=review_nodes(),
    )
    client = TestClient(app_module.app)
    headers = {
        "X-Internal-Secret": os.environ["INTERNAL_SECRET"],
        "X-Clerk-User-Id": "live-parity-check",
    }

    total = mismatches = errors = 0
    for game_index, game in enumerate(games):
        board = game.board()
        san_path = []
        rows = batch_labels(str(game), args.max_plies)
        ratings = GameAnalyzer._ratings_from_headers(game)
        for ply_index, node in enumerate(game.mainline()):
            if args.max_plies and ply_index >= args.max_plies:
                break
            move = node.move
            move_san = board.san(move)
            mover_rating = ratings["white" if board.turn == chess.WHITE else "black"]
            board.push(move)

            review_module._SANDBOX_EVAL_CACHE.clear()
            review_module._SANDBOX_RESULT_CACHE.clear()
            # The check itself fires hundreds of requests; clear the
            # in-process limiter so it measures parity, not throttling.
            rate_limit._memory_counters.clear()
            response = client.post(
                "/api/review/live",
                json={
                    "moves": san_path,
                    "move": move_san,
                    "player_rating": mover_rating,
                    "expected_mode": mode,
                },
                headers=headers,
            )
            total += 1
            if response.status_code != 200:
                errors += 1
                print(
                    f"  game {game_index} ply {ply_index}: "
                    f"HTTP {response.status_code} {response.text[:120]}"
                )
                san_path.append(move_san)
                continue
            live_label = response.json()["classification"]
            batch_label = rows[ply_index]["classification"]
            if live_label != batch_label:
                mismatches += 1
                if mismatches <= 25:
                    print(
                        f"  MISMATCH game {game_index} ply {ply_index} "
                        f"{move_san}: batch={batch_label} live={live_label}"
                    )
            san_path.append(move_san)
        print(f"  game {game_index}: {len(san_path)} plies checked")

    print(
        f"checked {total} moves | mismatches={mismatches} | errors={errors} | "
        f"mode={mode}"
    )
    if mismatches or errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
