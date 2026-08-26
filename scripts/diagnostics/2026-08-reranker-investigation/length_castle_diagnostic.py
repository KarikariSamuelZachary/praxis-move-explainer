"""
game_length and castle signal diagnostic (TEST-ONLY).

Pure measurement. No production code or weight changes.

For each held-out position (reusing the heldout_replay_accuracy.py cache and
build-set TEMP-TABLE isolation), isolates EACH signal separately:

  game_length : base * length_multiplier  (suppress/boost forcing moves)
  castle      : base * castle_multiplier   (boost/suppress castle-side moves)

Reports, per signal (never combined):
  1. multiplier distribution (max AND min, since both signals are
     bidirectional), flip-top-1 rate, fraction with any candidate deviating.
  2. isolated net top-1 delta: helped / hurt / neutral (same convention as
     the setup_signature check: raw rank==1 -> signal rank>1 = hurt).
  3. split by book status (in_book / near_book / out_of_book).
  4. directional-correctness spot-checks (wrong-promotion cases dumped for
     qualitative read).
"""

import json
import math
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

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


def quantiles(vals, qs=(0.5, 0.75, 0.9, 0.95, 0.99, 1.0)):
    if not vals:
        return {q: None for q in qs}
    s = sorted(vals)
    out = {}
    for q in qs:
        idx = min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))
        out[q] = s[idx]
    return out


def main():
    positions_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/heldout_positions.json"
    with open(positions_path) as fh:
        positions = json.load(fh)

    build_ids = build_ids_from_db()
    conn = install_build_set_isolation(build_ids)
    style = compute_opponent_style(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control="blitz")
    traps = compute_exploitable_traps(
        conn, requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control="blitz")

    print(f"opponent style: avg_game_length={style['average_game_length']}")
    print(f"  castling_side_distribution={style.get('castling_side_distribution')}")
    print(f"  length_centered (surfaced per position below), castle pref below")

    # per-signal accumulators
    acc = {sig: {"recs": [], "wrong": []} for sig in ("castle", "length")}

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
        base_ws = [r["base_weight"] for r in rows]
        castle_mults = [r["castle_multiplier"] for r in rows]
        castle_inds = [r["castle_indicator"] for r in rows]
        length_mults = [r["length_multiplier"] for r in rows]
        length_inds = [r["length_indicator"] for r in rows]

        if actual not in moves:
            continue

        raw_rank = moves.index(actual) + 1
        book = p["book_status"]

        # ---- castle isolated ----
        cw = [b * m for b, m in zip(base_ws, castle_mults)]
        corder = sorted(range(len(moves)), key=lambda i: -cw[i])
        ctop = corder[0]
        crank = corder.index(moves.index(actual)) + 1
        c_max = max(castle_mults)
        c_min = min(castle_mults)
        c_fired = any(i != 0 for i in castle_inds)
        acc["castle"]["recs"].append({
            "book": book, "max": c_max, "min": c_min, "fired": c_fired,
            "flip": moves[ctop] != moves[0],
            "raw_rank": raw_rank, "rank": crank,
        })
        if moves[ctop] != moves[0] and moves[ctop] != actual:
            acc["castle"]["wrong"].append({
                "fen": p["fen"], "ply": p["ply"], "book": book,
                "actual": actual, "promoted": moves[ctop],
                "moves": moves, "base_ws": base_ws,
                "castle_mults": castle_mults, "castle_inds": castle_inds,
            })

        # ---- length isolated ----
        lw = [b * m for b, m in zip(base_ws, length_mults)]
        lorder = sorted(range(len(moves)), key=lambda i: -lw[i])
        ltop = lorder[0]
        lrank = lorder.index(moves.index(actual)) + 1
        l_max = max(length_mults)
        l_min = min(length_mults)
        l_fired = any(i for i in length_inds)
        acc["length"]["recs"].append({
            "book": book, "max": l_max, "min": l_min, "fired": l_fired,
            "flip": moves[ltop] != moves[0],
            "raw_rank": raw_rank, "rank": lrank,
        })
        if moves[ltop] != moves[0] and moves[ltop] != actual:
            acc["length"]["wrong"].append({
                "fen": p["fen"], "ply": p["ply"], "book": book,
                "actual": actual, "promoted": moves[ltop],
                "moves": moves, "base_ws": base_ws,
                "length_mults": length_mults, "length_inds": length_inds,
            })

    # ---- report per signal ----
    for sig, label in (("castle", "CASTLE-side preference"),
                       ("length", "GAME-LENGTH (forcing) calibration")):
        recs = acc[sig]["recs"]
        n = len(recs)
        print("\n" + "=" * 78)
        print(f"{label}  —  n={n} positions (actual in candidates)")
        print("=" * 78)

        maxes = [r["max"] for r in recs]
        mins = [r["min"] for r in recs]
        qmax = quantiles(maxes)
        qmin = quantiles(mins)
        print(f"  max multiplier q:  p50={qmax[0.5]:.3f} p75={qmax[0.75]:.3f} "
              f"p90={qmax[0.9]:.3f} p95={qmax[0.95]:.3f} p99={qmax[0.99]:.3f} "
              f"max={qmax[1.0]:.3f}")
        print(f"  min multiplier q:  p50={qmin[0.5]:.3f} p75={qmin[0.75]:.3f} "
              f"p90={qmin[0.9]:.3f} p95={qmin[0.95]:.3f} p99={qmin[0.99]:.3f} "
              f"min={qmin[1.0]:.3f}")
        fired = sum(1 for r in recs if r["fired"])
        flip = sum(1 for r in recs if r["flip"])
        gt = {t: sum(1 for r in recs if r["max"] > t) / n for t in (1.05, 1.10, 1.20)}
        lt = sum(1 for r in recs if r["min"] < 0.99) / n
        print(f"  fired (any candidate deviates): {fired} ({fired/n*100:.1f}%)")
        print(f"  flips top-1 vs raw Maia:       {flip} ({flip/n*100:.1f}%)")
        print(f"  any candidate mult >1.05/1.10/1.20: "
              f"{gt[1.05]*100:.1f}% / {gt[1.10]*100:.1f}% / {gt[1.20]*100:.1f}%")
        print(f"  any candidate mult <0.99 (suppressed): {lt*100:.1f}%")

        # net effect
        helped = hurt = neutral = 0
        raw_top1 = 0
        sig_top1 = 0
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
        print(f"  raw Maia top-1:    {raw_top1/n*100:.2f}%")
        print(f"  {sig}-only top-1:  {sig_top1/n*100:.2f}%")
        print(f"  net {sig} delta:   {(sig_top1 - raw_top1)/n*100:+.2f}pp")
        print(f"  helped={helped}  hurt={hurt}  neutral={neutral}")

        # book split
        print("  --- by book status (net top-1 delta) ---")
        for b in BOOK_ORDER:
            sub = [r for r in recs if r["book"] == b]
            if not sub:
                print(f"    {b:<12} n=0")
                continue
            m = len(sub)
            h = sum(1 for r in sub if r["raw_rank"] > 1 and r["rank"] == 1)
            hu = sum(1 for r in sub if r["raw_rank"] == 1 and r["rank"] > 1)
            r1 = sum(1 for r in sub if r["raw_rank"] == 1) / m
            s1 = sum(1 for r in sub if r["rank"] == 1) / m
            print(f"    {b:<12} n={m:<5} raw={r1*100:.1f}% {sig}={s1*100:.1f}% "
                  f"delta={(s1-r1)*100:+.2f}pp  helped={h} hurt={hu}")

    # ---- spot-check dumps ----
    for sig, label in (("castle", "CASTLE"), ("length", "GAME-LENGTH")):
        wrong = acc[sig]["wrong"]
        print(f"\n--- {label}: wrong-promotion spot-checks "
              f"(signal flipped top-1 AND promoted != actual) — {len(wrong)} total ---")
        for w in wrong[:15]:
            print(f"  fen={w['fen']}  ply={w['ply']}  book={w['book']}")
            print(f"    actual={w['actual']}  promoted={w['promoted']}")
            for i, m in enumerate(w["moves"]):
                if sig == "castle":
                    extra = f"ind={w['castle_inds'][i]:+d} mult={w['castle_mults'][i]:.3f}"
                else:
                    extra = f"forcing={int(w['length_inds'][i])} mult={w['length_mults'][i]:.3f}"
                print(f"      cand#{i+1} {m:6s} base={w['base_ws'][i]:.4f} {extra}")

    print("\nDone.")


if __name__ == "__main__":
    main()
