"""
Candidate-injection prototype (TEST-ONLY).

Hypothesis test: does injecting retrieved moves (from pick_repertoire_move /
pick_near_repertoire_moves) as INDEPENDENT candidates -- scored on their own
terms instead of starting from a penalized Maia rank -- recover top-1 accuracy
that the multiplicative reranker (opponent_style_reranker.rerank_candidates)
cannot touch?

This is a prototype + measurement script. It is NOT wired into train.py or any
production path, and it IMPORTS (never modifies) production functions:

  * Maia candidates are read from a cached positions JSON produced by
    scripts/heldout_replay_accuracy.py (fen / ply / actual_uci / candidates /
    near_book_weights), so no Maia inference is re-run here.
  * Retrieval reuses pick_repertoire_move / pick_near_repertoire_moves verbatim
    (same recency-weighted lookup, same sample floors).
  * The comparison reranker reuses rerank_candidates verbatim.
  * Build-set isolation + chronological 80/20 split are identical to
    heldout_replay_accuracy.py (session-local temp tables shadowing the three
    opponent tables), so the numbers are apples-to-apples with that script.

Scoring (crude and explicit -- this tests the MECHANISM, not a tuned model):

    score_i = base_i + injection_weight * retrieved_weight_i

  base_i            = Maia policy (softmax prob) when the candidate is one of
                      Maia's original top-N; fallback 0.5^(rank-1) if policy
                      is absent; 0.0 for injected candidates (they have no
                      Maia rank to start from -- that is the point).
  retrieved_weight_i= the recency-weighted frequency of the move in the
                      retrieved set, normalized by the max weight in that
                      position so it lives in [0, 1] and is comparable to a
                      policy. 0.0 for moves not in the retrieved set.
  injection_weight  = a single scalar, swept over a grid (it is free: pure
                      arithmetic over cached candidates, no Maia).

The combined candidate set is ranked by score and the top-1 is taken.
"""

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import chess
import psycopg2
from psycopg2.extras import RealDictCursor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    pass

from core import database  # noqa: E402
from services.opponent_repertoire import (  # noqa: E402
    _player_rating,
    _player_username,
    pick_near_repertoire_moves,
    pick_repertoire_move,
)
from services.opponent_style import (  # noqa: E402
    _normalize_username,
    compute_opponent_style,
)
from services.opponent_style_reranker import rerank_candidates  # noqa: E402
from services.opponent_traps import compute_exploitable_traps  # noqa: E402

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
OPPONENT = "iaminspiredbroo"
BUILD_FRACTION = 0.8
CONFIDENCE_FLOOR = 30

# Injection-weight sweep. 0.0 == raw Maia (sanity check). Values are on the
# policy scale because retrieved_weight is normalized to [0,1].
SWEEP = [0.0, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
PRIMARY_WEIGHT = 1.0

BOOK_ORDER = ["in_book", "near_book", "out_of_book"]
PLY_ORDER = ["opening", "middlegame", "endgame"]


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


def fetch_games() -> List[Dict[str, Any]]:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id::text AS id, pgn, end_time, time_class, imported_at,
                       white_player, black_player
                FROM opponent_games
                WHERE requested_by_user_id = %s
                  AND provider = %s
                  AND LOWER(opponent_username) = LOWER(%s)
                ORDER BY end_time ASC, imported_at ASC, id ASC
                """,
                (UID, PROVIDER, OPPONENT),
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def _new_isolation_conn(build_ids: List[str]):
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
    return conn


def install_build_set_isolation(build_ids: List[str]):
    conn = _new_isolation_conn(build_ids)
    database.connection_pool = _StubPool(conn)
    return conn


def heal_db_connection(conn, build_ids: List[str]):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn
    except psycopg2.OperationalError:
        conn.close()
        new_conn = _new_isolation_conn(build_ids)
        database.connection_pool = _StubPool(new_conn)
        return new_conn


def build_set_rating(games: List[Dict[str, Any]]) -> int:
    normalized = _normalize_username(OPPONENT)
    ratings: List[int] = []
    for g in games:
        for player in (g.get("white_player") or {}, g.get("black_player") or {}):
            if _player_username(player or {}) == normalized:
                r = _player_rating(player or {})
                if r is not None:
                    ratings.append(r)
    if not ratings:
        return 1500
    return round(sum(ratings) / len(ratings))


def retrieve_moves(board: chess.Board) -> Tuple[str, Dict[str, float]]:
    """Reuse the production retrieval lookups verbatim.

    Returns (book_status, retrieved) where retrieved is a {move_uci: raw
    recency-weighted frequency} map of the moves the retrieval layer would
    surface for this position (empty for out_of_book).
    """
    rep = pick_repertoire_move(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, board=board,
    )
    if rep is not None:
        w = float(rep.get("weighted_frequency") or rep.get("frequency") or 0.0)
        return "in_book", {rep["move_uci"]: w} if rep.get("move_uci") else {}
    nb = pick_near_repertoire_moves(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, board=board,
    )
    if nb:
        return "near_book", {m: float(w) for m, w in nb.items() if float(w) > 0.0}
    return "out_of_book", {}


def injection_order(
    candidates: List[Dict[str, Any]],
    retrieved: Dict[str, float],
    injection_weight: float,
) -> Tuple[List[str], List[str]]:
    """Return (order, injected_moves) from the crude injection scoring."""
    maia_moves = [c["move"] for c in candidates]
    maia_set = set(maia_moves)

    max_w = max(retrieved.values()) if retrieved else 0.0

    scored: List[Tuple[str, float, str]] = []
    for idx, c in enumerate(candidates):
        base = c.get("policy")
        if base is None:
            base = 0.5 ** idx
        else:
            base = float(base)
        w = retrieved.get(c["move"], 0.0)
        nw = (w / max_w) if max_w > 0.0 else 0.0
        scored.append((c["move"], base + injection_weight * nw, "maia"))

    injected: List[str] = []
    for m, w in retrieved.items():
        if m in maia_set:
            continue
        nw = (w / max_w) if max_w > 0.0 else 0.0
        injected.append(m)
        scored.append((m, injection_weight * nw, "injected"))

    scored.sort(key=lambda t: -t[1])
    return [m for m, _, _ in scored], injected


def rank_of(order: List[str], actual: str) -> Optional[int]:
    for i, m in enumerate(order, start=1):
        if m == actual:
            return i
    return None


def summarize(rs: List[Optional[int]]) -> Dict[str, Any]:
    n = len(rs)
    if n == 0:
        return {"n": 0, "top1": None, "top3": None, "top5": None,
                "median_rank": None, "miss": None}
    ranked = sorted(r for r in rs if r is not None)
    m = len(ranked)
    med = (float(ranked[m // 2]) if m % 2 else
           (ranked[m // 2 - 1] + ranked[m // 2]) / 2.0) if m else None

    def cum(k: int) -> float:
        return sum(1 for r in rs if r is not None and r <= k) / n

    return {
        "n": n,
        "top1": sum(1 for r in rs if r == 1) / n,
        "top3": cum(3),
        "top5": cum(5),
        "median_rank": med,
        "miss": sum(1 for r in rs if r is None) / n,
    }


def pct(x: Optional[float]) -> str:
    return "   --" if x is None else f"{x * 100:6.1f}%"


def main() -> None:
    global OPPONENT
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions-json", required=True,
                    help="Cached positions JSON from heldout_replay_accuracy.py.")
    ap.add_argument("--opponent", default=OPPONENT,
                    help="Opponent username (default %(default)s).")
    ap.add_argument("--max-games", type=int, default=None,
                    help="Cap held-out games for a smoke run.")
    args = ap.parse_args()
    OPPONENT = args.opponent

    random.seed(1234)  # deterministic pick_repertoire_move sampling

    t0 = time.time()
    games = fetch_games()
    n = len(games)
    n_build = math.floor(n * BUILD_FRACTION)
    build = games[:n_build]
    heldout = games[n_build:]
    build_ids = [g["id"] for g in build]
    heldout_ids = set(g["id"] for g in heldout)
    overlap = set(build_ids) & heldout_ids

    with open(args.positions_json) as fh:
        positions = json.load(fh)

    conn = install_build_set_isolation(build_ids)
    rating = build_set_rating(build)

    # --- re-derive retrieval for every cached position (cheap DB, no Maia) ---
    n_done = 0
    for p in positions:
        conn = heal_db_connection(conn, build_ids)
        board = chess.Board(p["fen"])
        status, retrieved = retrieve_moves(board)
        p["retrieved"] = retrieved
        p["status"] = status
        n_done += 1
        if n_done % 1000 == 0:
            print(f"  ... retrieved {n_done}/{len(positions)} positions, "
                  f"{time.time() - t0:.0f}s", flush=True)

    print("\n" + "=" * 80)
    print("CANDIDATE-INJECTION PROTOTYPE — retrieved moves as first-class candidates")
    print("=" * 80)
    print(f"opponent: {OPPONENT} ({PROVIDER})   user: {UID}")
    print(f"split: {int(BUILD_FRACTION*100)}/{int((1-BUILD_FRACTION)*100)} "
          f"-> build={len(build)} held-out={len(heldout)} (total={n})")
    print(f"build/held-out game-id overlap: {len(overlap)}")
    print(f"opponent rating (build set only): {rating}")
    print(f"cached positions: {len(positions)}")
    print(f"injection_weight sweep: {SWEEP}   (primary={PRIMARY_WEIGHT})")

    for tc_label, tc in (("blitz", "blitz"), ("neutral", None)):
        style = compute_opponent_style(
            requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc,
        )
        traps = compute_exploitable_traps(
            conn, requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc,
        )

        # --- collect per-position metrics once, then report ---
        recs: List[Dict[str, Any]] = []
        for p in positions:
            candidates = p["candidates"]
            if not candidates:
                continue
            actual = p["actual_uci"]
            maia_moves = [c["move"] for c in candidates]
            maia_set = set(maia_moves)

            board = chess.Board(p["fen"])
            rerank = rerank_candidates(
                candidates=candidates, style=style, board=board,
                exploitable_trap_keys=traps,
                near_book_weights=(p.get("near_book_weights")
                                   if p["status"] == "near_book" else None),
            )
            rerank_order = (
                [r["move"] for r in rerank["bias_breakdown"]["weights"]]
                if rerank.get("applied_bias") and rerank.get("bias_breakdown")
                else maia_moves
            )

            retrieved = p["retrieved"]
            inj_orders: Dict[float, List[str]] = {}
            inj_injected: Dict[float, List[str]] = {}
            for w in SWEEP:
                order, injected = injection_order(candidates, retrieved, w)
                inj_orders[w] = order
                inj_injected[w] = injected

            recs.append({
                "book": p["status"],
                "ply": p["ply"],
                "actual": actual,
                "raw_rank": rank_of(maia_moves, actual),
                "rerank_rank": rank_of(rerank_order, actual),
                "maia_top1_correct": maia_moves[0] == actual,
                "actual_in_maia_top5": actual in maia_set,
                "actual_injected": actual not in maia_set and actual in retrieved,
                "inj_orders": inj_orders,
                "inj_injected": inj_injected,
            })

        def bucket_ranks(key):
            out = {}
            for book in BOOK_ORDER:
                for pb in PLY_ORDER:
                    out[(book, pb)] = summarize(
                        [r[key] for r in recs
                         if r["book"] == book and ply_bucket(r["ply"]) == pb]
                    )
            out["_overall"] = summarize([r[key] for r in recs])
            return out

        raw_tab = bucket_ranks("raw_rank")
        rer_tab = bucket_ranks("rerank_rank")

        def inj_rank_primary(r):
            order = r["inj_orders"][PRIMARY_WEIGHT]
            return rank_of([order[0]], r["actual"])

        print(f"\n{'=' * 80}\nTC = {tc_label}")
        print("\n  Top-1 accuracy by book/ply bucket "
              "(raw Maia / current reranker / injection@primary):")
        hdr = "    book \\ ply     " + "".join(f"{pb:>28}" for pb in PLY_ORDER)
        print(hdr)
        for book in BOOK_ORDER:
            cells = []
            for pb in PLY_ORDER:
                a = raw_tab[(book, pb)]
                b = rer_tab[(book, pb)]
                inj = summarize([inj_rank_primary(r) for r in recs
                                 if r["book"] == book and ply_bucket(r["ply"]) == pb])
                if a["n"] == 0:
                    cells.append("".ljust(28))
                    continue
                flag = "*" if 0 < a["n"] < CONFIDENCE_FLOOR else " "
                cells.append(f"{pct(a['top1'])} {pct(b['top1'])} {pct(inj['top1'])}{flag}")
            print(f"    {book:<14}   " + "   ".join(cells))
        a = raw_tab["_overall"]
        b = rer_tab["_overall"]
        inj_ov = summarize([inj_rank_primary(r) for r in recs])
        print(f"    {'OVERALL':<14}   " +
              f"{pct(a['top1'])} {pct(b['top1'])} {pct(inj_ov['top1'])}")

        # --- core hypothesis metrics ---
        total = len(recs)
        n_missed = sum(1 for r in recs if r["raw_rank"] is None)
        n_actual_injected = sum(1 for r in recs if r["actual_injected"])
        print("\n  Hypothesis: recovery of the Maia top-5 promotion gap")
        print(f"    positions where actual move is NOT in Maia top-5 : "
              f"{n_missed} ({n_missed/total*100:.1f}% of {total})")
        print(f"    ...of which retrieval also had the actual move   : "
              f"{n_actual_injected} "
              f"({n_actual_injected/total*100:.1f}% of all, "
              f"{n_actual_injected/n_missed*100:.1f}% of the missed) "
              f"-> reachable ceiling")

        print(f"\n  {'inj_w':>6}  {'top1':>6}  {'hit@ceiling':>12}  {'hit_n':>6}  "
              f"{'new_errs':>8}  {'flip_correct':>13}  {'net vs raw':>11}")
        for w in SWEEP:
            correct = 0
            new_errs = 0
            flip_correct = 0
            hit = 0
            for r in recs:
                order = r["inj_orders"][w]
                injected = r["inj_injected"][w]
                chosen = order[0]
                chosen_injected_wrong = chosen in injected and chosen != r["actual"]
                if chosen == r["actual"]:
                    correct += 1
                    if r["actual_injected"]:
                        hit += 1
                elif chosen_injected_wrong:
                    new_errs += 1
                    if r["maia_top1_correct"]:
                        flip_correct += 1
            hit_rate = hit / n_actual_injected if n_actual_injected else 0.0
            net = (correct - sum(1 for r in recs if r["maia_top1_correct"])) / total * 100
            print(f"  {w:>6.2f}  {correct/total*100:>5.1f}%  {hit_rate*100:>11.1f}%  "
                  f"{hit:>6d}  {new_errs:>8d}  {flip_correct:>13d}  {net:>+10.1f}pp")

        # --- per-book hit@ceiling at the primary weight (why injection fails) ---
        print(f"\n  hit@ceiling by book status @inj_w={PRIMARY_WEIGHT}:")
        for book in BOOK_ORDER:
            pool = [r for r in recs if r["book"] == book]
            ceiled = [r for r in pool if r["actual_injected"]]
            hit = 0
            for r in ceiled:
                order = r["inj_orders"][PRIMARY_WEIGHT]
                if order[0] == r["actual"]:
                    hit += 1
            if ceiled:
                print(f"    {book:<14} reachable={len(ceiled):>4}  "
                      f"hit={hit:>4}  hit@ceiling={hit/len(ceiled)*100:5.1f}%")
            else:
                print(f"    {book:<14} reachable=   0")

        print("    cols: top1=injection top-1 acc; hit@ceiling=of the reachable"
              " ceiling, how often the injected actual move wins; hit_n=count; "
              "new_errs=injected non-actual move won; flip_correct=injected wrong"
              " move flipped a Maia-correct top-1 to wrong; net vs raw = top-1 delta.")

    print(f"\nDone in {time.time() - t0:.0f}s.")


if __name__ == "__main__":
    main()
