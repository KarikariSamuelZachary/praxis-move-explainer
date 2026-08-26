"""
Isolated net delta of the REBUILT narrow castle signal (TEST-ONLY).

game_length was removed; castle is rebuilt to fire only on the literal
O-O/O-O-O move. This measures castle ALONE (base * castle_multiplier) vs
raw Maia order over the held-out positions: helped / hurt / neutral, split
by book status, using the same methodology as the original diagnostics.
"""

import json
import math
import os
import sys
from collections import Counter

import chess
import psycopg2

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
BOOK_ORDER = ["in_book", "near_book", "out_of_book"]


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

    for tc_label, tc in (("blitz", "blitz"), ("neutral", None)):
        style = compute_opponent_style(
            requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc)
        traps = compute_exploitable_traps(
            conn, requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc)

        recs = []
        for p in positions:
            candidates = p["candidates"]
            if not candidates:
                continue
            board = chess.Board(p["fen"])
            actual = p["actual_uci"]
            r = rerank_candidates(
                candidates=candidates, style=style, board=board,
                exploitable_trap_keys=traps,
                near_book_weights=p["near_book_weights"])
            rows = r["bias_breakdown"]["weights"] if r.get("applied_bias") else None
            if not rows:
                continue
            moves = [row["move"] for row in rows]
            if actual not in moves:
                continue
            base_ws = [row["base_weight"] for row in rows]
            castle_mults = [row["castle_multiplier"] for row in rows]
            castle_inds = [row["castle_indicator"] for row in rows]
            cw = [b * m for b, m in zip(base_ws, castle_mults)]
            corder = sorted(range(len(moves)), key=lambda i: -cw[i])
            crank = corder.index(moves.index(actual)) + 1
            raw_rank = moves.index(actual) + 1
            recs.append({
                "book": p["book_status"],
                "raw_rank": raw_rank,
                "rank": crank,
                "fire": any(i != 0 for i in castle_inds),
                "max_mult": max(castle_mults),
            })

        n = len(recs)
        helped = hurt = neutral = 0
        raw_top1 = sig_top1 = 0
        for r in recs:
            if r["raw_rank"] == 1:
                raw_top1 += 1
            if r["rank"] == 1:
                sig_top1 += 1
            if r["raw_rank"] > 1 and r["rank"] == 1:
                helped += 1
            elif r["raw_rank"] == 1 and r["rank"] > 1:
                hurt += 1
            else:
                neutral += 1
        fire = sum(1 for r in recs if r["fire"])
        mx = [r["max_mult"] for r in recs if r["fire"]]

        print(f"\n=== {tc_label}: REBUILT castle isolated (n={n}) ===")
        print(f"  fires (any castle candidate non-zero): {fire} ({fire/n*100:.1f}%)")
        if mx:
            mx.sort()
            print(f"  max castle_mult when fired: p50={mx[len(mx)//2]:.3f} "
                  f"p90={mx[min(len(mx)-1, math.ceil(0.9*len(mx))-1)]:.3f} "
                  f"max={mx[-1]:.3f}")
        print(f"  raw top-1:    {raw_top1/n*100:.2f}%")
        print(f"  castle top-1: {sig_top1/n*100:.2f}%")
        print(f"  net castle delta: {(sig_top1-raw_top1)/n*100:+.2f}pp")
        print(f"  helped={helped}  hurt={hurt}  neutral={neutral}")
        for b in BOOK_ORDER:
            sub = [r for r in recs if r["book"] == b]
            if not sub:
                continue
            m = len(sub)
            h = sum(1 for r in sub if r["raw_rank"] > 1 and r["rank"] == 1)
            hu = sum(1 for r in sub if r["raw_rank"] == 1 and r["rank"] > 1)
            r1 = sum(1 for r in sub if r["raw_rank"] == 1) / m
            s1 = sum(1 for r in sub if r["rank"] == 1) / m
            print(f"    {b:<12} n={m:<5} raw={r1*100:.1f}% castle={s1*100:.1f}% "
                  f"delta={(s1-r1)*100:+.2f}pp  helped={h} hurt={hu}")


if __name__ == "__main__":
    main()
