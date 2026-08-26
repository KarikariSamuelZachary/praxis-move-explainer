"""
Follow-up: net effect of setup bias on top-1 (help vs hurt), TEST-ONLY.

For each held-out position, compare the actual move's rank in (a) raw Maia
order and (b) setup-only order (base_weight * setup_multiplier). Count:
  helped  : raw rank > 1 -> setup-only rank == 1
  hurt    : raw rank == 1 -> setup-only rank > 1
  neutral : otherwise
"""

import json
import math
import os
import sys

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
from services.opponent_style import compute_opponent_style  # noqa: E402
from services.opponent_style_reranker import rerank_candidates  # noqa: E402
from services.opponent_traps import compute_exploitable_traps  # noqa: E402

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
OPPONENT = "iaminspiredbroo"


class _StubPool:
    def __init__(self, conn):
        self.conn = conn

    def getconn(self):
        return self.conn

    def putconn(self, conn):  # noqa: ARG002
        return

    def closeall(self):
        self.conn.close()


def build_ids_from_db():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    dsn = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id::text AS id FROM opponent_games "
                "WHERE requested_by_user_id = %s AND provider = %s "
                "AND LOWER(opponent_username) = LOWER(%s) "
                "ORDER BY end_time ASC, imported_at ASC, id ASC",
                (UID, PROVIDER, OPPONENT))
            rows = [dict(r)["id"] for r in cur.fetchall()]
    finally:
        conn.close()
    return rows[: math.floor(len(rows) * 0.8)]


def install_build_set_isolation(build_ids):
    import psycopg2
    dsn = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE opponent_games AS "
            "SELECT * FROM public.opponent_games WHERE id::text = ANY(%s)",
            (build_ids,))
        cur.execute(
            "CREATE TEMP TABLE opponent_repertoire_moves AS "
            "SELECT * FROM public.opponent_repertoire_moves "
            "WHERE opponent_game_id::text = ANY(%s)",
            (build_ids,))
        cur.execute(
            "CREATE TEMP TABLE opponent_game_blunders AS "
            "SELECT * FROM public.opponent_game_blunders "
            "WHERE game_id::text = ANY(%s)",
            (build_ids,))
    database.connection_pool = _StubPool(conn)
    return conn


def main():
    with open("/tmp/opencode/heldout_positions.json") as fh:
        positions = json.load(fh)
    build_ids = build_ids_from_db()
    conn = install_build_set_isolation(build_ids)
    style = compute_opponent_style(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control="blitz")
    traps = compute_exploitable_traps(
        conn, requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control="blitz")

    helped = hurt = neutral = 0
    raw_top1 = 0
    setup_top1 = 0
    n = 0
    for p in positions:
        candidates = p["candidates"]
        if not candidates:
            continue
        board = chess.Board(p["fen"])
        actual = p["actual_uci"]
        rerank = rerank_candidates(
            candidates=candidates, style=style, board=board,
            exploitable_trap_keys=traps, near_book_weights=p["near_book_weights"])
        rows = rerank["bias_breakdown"]["weights"] if rerank.get("applied_bias") else None
        if not rows:
            continue
        moves = [r["move"] for r in rows]
        if actual not in moves:
            continue
        n += 1
        raw_rank = moves.index(actual) + 1
        setup_weights = [r["base_weight"] * (r["setup_multiplier"] or 1.0) for r in rows]
        order = sorted(range(len(moves)), key=lambda i: -setup_weights[i])
        setup_rank = order.index(moves.index(actual)) + 1
        if raw_rank == 1:
            raw_top1 += 1
        if setup_rank == 1:
            setup_top1 += 1
        if raw_rank > 1 and setup_rank == 1:
            helped += 1
        elif raw_rank == 1 and setup_rank > 1:
            hurt += 1
        else:
            neutral += 1

    print(f"positions with actual in candidates: {n}")
    print(f"raw Maia top-1:      {raw_top1 / n * 100:.2f}%")
    print(f"setup-only top-1:    {setup_top1 / n * 100:.2f}%")
    print(f"  net setup delta:    {(setup_top1 - raw_top1) / n * 100:+.2f}pp")
    print(f"helped (rank>1 -> 1): {helped}")
    print(f"hurt   (rank=1 -> >1): {hurt}")
    print(f"neutral:               {neutral}")


if __name__ == "__main__":
    main()
