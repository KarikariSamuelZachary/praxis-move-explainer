"""
Ablation diagnostic (TEST-ONLY) for the held-out replay accuracy measurement.

Reuses the cached positions from heldout_replay_accuracy.py and, for each
held-out position, computes the actual move's rank under three reranker
configurations (all using the EXISTING rerank_candidates with different
already-supported inputs — no function is modified):

  * raw         : Maia candidate order (no reranker).
  * style_only  : rerank_candidates(..., near_book_weights=None) — sac/qt/
                  setup/castle/length/trap stack, near-book OFF.
  * full        : rerank_candidates(..., near_book_weights=<cached>) — the
                  production stack.

This isolates the marginal effect of the near-book layer (full vs
style_only) and the pre-existing style stack (style_only vs raw), and
reports per-signal firing frequency.
"""

import json
import math
import os
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    pass

from core import database  # noqa: E402
from services.opponent_repertoire import pick_near_repertoire_moves  # noqa: E402
from services.opponent_style import compute_opponent_style  # noqa: E402
from services.opponent_style_reranker import rerank_candidates  # noqa: E402
from services.opponent_traps import compute_exploitable_traps  # noqa: E402

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
OPPONENT = "iaminspiredbroo"

BOOK_ORDER = ["in_book", "near_book", "out_of_book"]


def ply_bucket(ply: int) -> str:
    if ply <= 10:
        return "opening"
    if ply <= 30:
        return "middlegame"
    return "endgame"


class _StubPool:
    def __init__(self, conn):
        self.conn = conn

    def getconn(self):
        return self.conn

    def putconn(self, conn):  # noqa: ARG002
        return

    def closeall(self):
        self.conn.close()


def install_build_set_isolation(build_ids: List[str]):
    import psycopg2
    dsn = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE opponent_games AS "
            "SELECT * FROM public.opponent_games WHERE id::text = ANY(%s)",
            (build_ids,),
        )
        cur.execute(
            "CREATE TEMP TABLE opponent_repertoire_moves AS "
            "SELECT * FROM public.opponent_repertoire_moves "
            "WHERE opponent_game_id::text = ANY(%s)",
            (build_ids,),
        )
        cur.execute(
            "CREATE TEMP TABLE opponent_game_blunders AS "
            "SELECT * FROM public.opponent_game_blunders "
            "WHERE game_id::text = ANY(%s)",
            (build_ids,),
        )
    database.connection_pool = _StubPool(conn)
    return conn


def build_ids_from_db() -> List[str]:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    dsn = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id::text AS id FROM opponent_games
                WHERE requested_by_user_id = %s AND provider = %s
                  AND LOWER(opponent_username) = LOWER(%s)
                ORDER BY end_time ASC, imported_at ASC, id ASC
                """,
                (UID, PROVIDER, OPPONENT),
            )
            rows = [dict(r)["id"] for r in cur.fetchall()]
    finally:
        conn.close()
    n = len(rows)
    return rows[: math.floor(n * 0.8)]


def raw_order(candidates):
    return [c.get("move", "") for c in candidates]


def reranked_order(candidates, rerank):
    if rerank.get("applied_bias"):
        rows = sorted(rerank["bias_breakdown"]["weights"],
                      key=lambda r: (-r["weight"], r["rank"]))
        return [r["move"] for r in rows]
    return [c.get("move", "") for c in candidates]


def rank_of(order, actual):
    for i, m in enumerate(order, start=1):
        if m == actual:
            return i
    return None


def topk(ranks, k):
    n = len(ranks)
    return sum(1 for r in ranks if r is not None and r <= k) / n


def top1(ranks):
    n = len(ranks)
    return sum(1 for r in ranks if r == 1) / n


def main() -> None:
    positions_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/heldout_positions.json"
    with open(positions_path) as fh:
        positions = json.load(fh)

    build_ids = build_ids_from_db()
    conn = install_build_set_isolation(build_ids)

    for tc_label, tc in (("blitz", "blitz"), ("neutral", None)):
        style = compute_opponent_style(
            requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc,
        )
        traps = compute_exploitable_traps(
            conn, requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc,
        )

        # per book-status accumulators for raw / style_only / full
        data: Dict[str, Dict[str, List[Optional[int]]]] = {
            key: {book: [] for book in BOOK_ORDER} for key in ("raw", "style", "full")
        }
        signal_counter = Counter()
        nb_fire = Counter()

        for p in positions:
            candidates = p["candidates"]
            if not candidates:
                continue
            board = chess.Board(p["fen"])
            book = p["book_status"]
            actual = p["actual_uci"]

            r_raw = rank_of(raw_order(candidates), actual)

            r_style = rerank_candidates(
                candidates=candidates, style=style, board=board,
                exploitable_trap_keys=traps, near_book_weights=None,
            )
            r_full = rerank_candidates(
                candidates=candidates, style=style, board=board,
                exploitable_trap_keys=traps,
                near_book_weights=p["near_book_weights"],
            )

            data["raw"][book].append(r_raw)
            data["style"][book].append(rank_of(reranked_order(candidates, r_style), actual))
            data["full"][book].append(rank_of(reranked_order(candidates, r_full), actual))

            if r_full.get("applied_bias"):
                for sig in r_full["bias_breakdown"]["signals_applied"]:
                    signal_counter[sig] += 1
            if r_full.get("near_book_active"):
                nb_fire[book] += 1

        print(f"\n=== TC = {tc_label} ===")
        for book in BOOK_ORDER:
            n = len(data["raw"][book])
            if n == 0:
                continue
            print(f"  {book:<12} n={n:<5} "
                  f"top1  raw={top1(data['raw'][book])*100:5.1f}%  "
                  f"style={top1(data['style'][book])*100:5.1f}%  "
                  f"full={top1(data['full'][book])*100:5.1f}%   | "
                  f"top3  raw={topk(data['raw'][book],3)*100:5.1f}%  "
                  f"style={topk(data['style'][book],3)*100:5.1f}%  "
                  f"full={topk(data['full'][book],3)*100:5.1f}%")
        all_raw = [r for book in BOOK_ORDER for r in data["raw"][book]]
        all_style = [r for book in BOOK_ORDER for r in data["style"][book]]
        all_full = [r for book in BOOK_ORDER for r in data["full"][book]]
        print(f"  {'OVERALL':<12} n={len(all_raw):<5} "
              f"top1  raw={top1(all_raw)*100:5.1f}%  "
              f"style={top1(all_style)*100:5.1f}%  full={top1(all_full)*100:5.1f}%   | "
              f"top3  raw={topk(all_raw,3)*100:5.1f}%  "
              f"style={topk(all_style,3)*100:5.1f}%  full={topk(all_full,3)*100:5.1f}%")
        print(f"  signals_applied (positions where each signal actually deviated): "
              f"{dict(signal_counter)}")
        print(f"  near_book_active positions by book: {dict(nb_fire)}")


if __name__ == "__main__":
    main()
