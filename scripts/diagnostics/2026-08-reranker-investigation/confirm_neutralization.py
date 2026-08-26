"""
Confirm neutralization via transparency output (TEST-ONLY).

Reuses the held-out cache + build-set isolation and reports, over all held-out
positions, how often each signal actually deviates (appears in
`bias_breakdown.signals_applied`) under the current (neutralized) stack.
Expected: 'castle' and 'game_length' count == 0; the others still fire.
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
        signal_counter = Counter()
        n_bias = 0
        for p in positions:
            candidates = p["candidates"]
            if not candidates:
                continue
            board = chess.Board(p["fen"])
            r = rerank_candidates(
                candidates=candidates, style=style, board=board,
                exploitable_trap_keys=traps,
                near_book_weights=p["near_book_weights"])
            if r.get("applied_bias"):
                n_bias += 1
                for sig in r["bias_breakdown"]["signals_applied"]:
                    signal_counter[sig] += 1
        print(f"TC={tc_label}: positions with applied_bias={n_bias}/{len(positions)}")
        for sig in sorted(signal_counter):
            print(f"  {sig:<16} fires on {signal_counter[sig]:5d} positions "
                  f"({signal_counter[sig]/len(positions)*100:.1f}%)")
        print()


if __name__ == "__main__":
    main()
