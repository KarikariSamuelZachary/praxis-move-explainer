"""
Held-out replay accuracy measurement for the sparring bot (TEST-ONLY).
Reusable measurement infrastructure -- NOT a one-off diagnostic.

Does the sparring bot (Maia candidates + style/repertoire/trap/near-book
reranking) predict what the one real opponent in the DB actually played?

Methodology (the canonical held-out evaluation used throughout the 2026-08
reranker investigation and the 5-account generalization check):

  1. Loads all imported games for the target opponent (`--opponent`).
  2. Skips Chess960/Fischer-Random games (Maia has no UCI_Chess960 support,
     so those positions cannot be evaluated) and reports how many were
     dropped.
  3. Splits the remaining games CHRONOLOGICALLY 80/20 (oldest -> build set,
     newest -> held-out set) so no held-out game leaks into the
     style/repertoire/trap computations.
  4. Isolates the build set from the production tables using SESSION-LOCAL
     TEMP TABLES that shadow opponent_games / opponent_repertoire_moves /
     opponent_game_blunders on a dedicated connection, then points
     core.database.connection_pool at that connection. No production code
     or production tables are modified.
  5. Replays every held-out position where it was the opponent's move,
     classifies book status (in-book / near-book / out-of-book) by calling
     pick_repertoire_move / pick_near_repertoire_moves, caches Maia's
     top-N candidates once per position (--multipv), and stores the
     retrieved exact-book move for the book-only baseline.
  6. Measures THREE orderings side by side, split by book status and ply
     depth (opening <=10 / middlegame 11-30 / endgame >30):
       raw        -- Maia candidate order (baseline)
       reranked   -- rerank_candidates with the current stack
       book-only  -- exact-book mirror (play the retrieved book move when
                     in_book, else raw Maia top-1); isolates the exact-book
                     layer from the style/near-book signals.
     Re-runs for sparring_time_control = 'blitz' and None ('neutral').
  7. Caches per-position data to --positions-json so Maia inference is only
     paid once and later comparisons (ablation, diagnostics, re-runs) can
     reuse the cache without re-running the engine.

No changes are made to compute_opponent_style, pick_repertoire_move,
pick_near_repertoire_moves, compute_exploitable_traps, or rerank_candidates.
"""

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

import chess
import chess.pgn
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
from engines.maia_engine import Maia3Engine  # noqa: E402
from services.opponent_repertoire import (  # noqa: E402
    _player_rating,
    _player_username,
    pick_near_repertoire_moves,
    pick_repertoire_move,
)
from services.opponent_style import (  # noqa: E402
    _normalize_username,
    _opponent_color,
    compute_opponent_style,
)
from services.opponent_style_reranker import rerank_candidates  # noqa: E402
from services.opponent_traps import compute_exploitable_traps  # noqa: E402

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
OPPONENT = "iaminspiredbroo"
BUILD_FRACTION = 0.8
MULTIPV = 5
CONFIDENCE_FLOOR = 30

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
    """Recreate the build-set isolation connection if the DB dropped it.

    A single long-lived connection can be closed server-side (SSL / idle
    timeout) during the multi-minute Maia inference loop; the temp tables
    are session-local so they must be recreated on reconnect.
    """
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


def _is_chess960(pgn: str) -> bool:
    """Detect Chess960/Fischer-Random games, which Maia (standard chess) cannot
    evaluate. Chess.com flags them via a `Variant "Chess960"` header; the board
    fallback catches any game whose castling rights are non-standard.
    """
    try:
        game = chess.pgn.read_game(StringIO(pgn))
        if game is None:
            return False
        variant = (game.headers.get("Variant") or "").lower()
        if "960" in variant or "fischer" in variant or "frc" in variant:
            return True
        return bool(game.board().chess960)
    except Exception:  # noqa: BLE001
        return False


def iter_opponent_positions(pgn: str, normalized_opponent: str):
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None:
        return
    color = _opponent_color(game, normalized_opponent)
    if color is None:
        return
    board = game.board()
    for idx, move in enumerate(game.mainline_moves()):
        if board.turn == color:
            yield idx + 1, board.copy(), move.uci()
        board.push(move)


def classify_position(board: chess.Board) -> Tuple[str, Optional[str], Optional[Dict[str, float]]]:
    rep = pick_repertoire_move(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, board=board,
    )
    if rep:
        return "in_book", rep.get("move_uci"), None
    nb = pick_near_repertoire_moves(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, board=board,
    )
    if nb:
        return "near_book", None, nb
    return "out_of_book", None, None


def raw_order(candidates: List[Dict[str, Any]]) -> List[str]:
    return [c.get("move", "") for c in candidates]


def reranked_order(candidates: List[Dict[str, Any]], rerank: Dict[str, Any]) -> List[str]:
    if rerank.get("applied_bias"):
        rows = sorted(
            rerank["bias_breakdown"]["weights"],
            key=lambda r: (-r["weight"], r["rank"]),
        )
        return [r["move"] for r in rows]
    return [c.get("move", "") for c in candidates]


def rank_of(order: List[str], actual: str) -> Optional[int]:
    for i, m in enumerate(order, start=1):
        if m == actual:
            return i
    return None


def summarize(rs: List[Optional[int]]) -> Dict[str, Any]:
    n = len(rs)
    if n == 0:
        return {"n": 0, "top1": None, "top3": None, "top5": None,
                "top8": None, "top10": None, "median_rank": None, "miss": None}
    ranked = sorted(r for r in rs if r is not None)
    m = len(ranked)
    if m:
        med = float(ranked[m // 2]) if m % 2 else (ranked[m // 2 - 1] + ranked[m // 2]) / 2.0
    else:
        med = None

    def cum(k: int) -> float:
        return sum(1 for r in rs if r is not None and r <= k) / n

    return {
        "n": n,
        "top1": sum(1 for r in rs if r == 1) / n,
        "top3": cum(3),
        "top5": cum(5),
        "top8": cum(8),
        "top10": cum(10),
        "median_rank": med,
        "miss": sum(1 for r in rs if r is None) / n,
    }


def pct(x: Optional[float]) -> str:
    return "   --" if x is None else f"{x * 100:6.1f}%"


def fmt_med(x: Optional[float]) -> str:
    return "  --" if x is None else f"{x:5.1f}"


def fmt(n: int, s: Dict[str, Any]) -> str:
    flag = "*" if 0 < n < CONFIDENCE_FLOOR else " "
    return (f"{n:>4}{flag}  {pct(s['top1'])} {pct(s['top3'])} "
            f"{pct(s['top5'])}  {pct(s['top8'])}  {pct(s['top10'])}  "
            f"{fmt_med(s['median_rank'])}  {pct(s['miss'])}")


def build_bucket_tables(results: List[Dict[str, Any]], key: str):
    """key in {'raw_rank','rerank_rank'} -> {(book,ply): summary} + overall."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for book in BOOK_ORDER:
        for pb in PLY_ORDER:
            rs = [r[key] for r in results
                  if r["book"] == book and ply_bucket(r["ply"]) == pb]
            out[(book, pb)] = summarize(rs)
    out["_overall"] = summarize([r[key] for r in results])
    return out


def main() -> None:
    global OPPONENT
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions-json", default=None,
                    help="Cache file for precomputed positions (resume).")
    ap.add_argument("--max-games", type=int, default=None,
                    help="Cap held-out games for a smoke run.")
    ap.add_argument("--multipv", type=int, default=MULTIPV,
                    help="Maia candidate pool size (default %(default)s).")
    ap.add_argument("--opponent", default=OPPONENT,
                    help="Opponent username (default %(default)s).")
    ap.add_argument("--no-maia", action="store_true",
                    help="Skip Maia; empty candidate list (dry-run of plumbing).")
    args = ap.parse_args()
    multipv = args.multipv
    OPPONENT = args.opponent
    random.seed(1234)  # deterministic pick_repertoire_move sampling

    t0 = time.time()
    games = fetch_games()
    n_skipped_960 = sum(1 for g in games if _is_chess960(g["pgn"]))
    games = [g for g in games if not _is_chess960(g["pgn"])]
    n = len(games)
    n_build = math.floor(n * BUILD_FRACTION)
    build = games[:n_build]
    heldout = games[n_build:]
    build_ids = [g["id"] for g in build]
    heldout_ids = [g["id"] for g in heldout]
    overlap = set(build_ids) & set(heldout_ids)

    conn = install_build_set_isolation(build_ids)
    rating = build_set_rating(build)

    positions: List[Dict[str, Any]] = []
    if args.positions_json and os.path.exists(args.positions_json):
        with open(args.positions_json) as fh:
            positions = json.load(fh)
    else:
        engine = Maia3Engine()
        engine.start()
        normalized = _normalize_username(OPPONENT)
        n_done = 0
        n_games = 0
        for g in heldout:
            if args.max_games is not None and n_games >= args.max_games:
                break
            n_games += 1
            for ply, board, actual_uci in iter_opponent_positions(g["pgn"], normalized):
                conn = heal_db_connection(conn, build_ids)
                book_status, book_move, nb_weights = classify_position(board)
                candidates = (
                    [] if args.no_maia else
                    engine.best_move_candidates(
                        board, multipv=multipv, self_elo=rating, oppo_elo=rating,
                    )
                )
                positions.append({
                    "fen": board.fen(),
                    "ply": ply,
                    "actual_uci": actual_uci,
                    "book_status": book_status,
                    "book_move": book_move,
                    "candidates": candidates,
                    "near_book_weights": nb_weights,
                })
                n_done += 1
                if n_done % 200 == 0:
                    print(f"  ... {n_done} positions, {time.time() - t0:.0f}s elapsed",
                          flush=True)
                    if args.positions_json:
                        with open(args.positions_json, "w") as fh:
                            json.dump(positions, fh)
        engine.close()
        if args.positions_json:
            with open(args.positions_json, "w") as fh:
                json.dump(positions, fh)

    # --- measure for each TC ---
    # Backfill book_move for caches built before the book-only baseline existed
    # (old caches store book_status but not the retrieved exact-book move).
    for p in positions:
        if p.get("book_move") is None and p["book_status"] == "in_book":
            conn = heal_db_connection(conn, build_ids)
            rep = pick_repertoire_move(
                requested_by_user_id=UID, provider=PROVIDER,
                opponent_username=OPPONENT, board=chess.Board(p["fen"]),
            )
            p["book_move"] = rep["move_uci"] if rep else None

    conditions = [("blitz", "blitz"), ("neutral", None)]
    per_tc: Dict[str, List[Dict[str, Any]]] = {}
    per_tc_meta: Dict[str, Dict[str, Any]] = {}
    for tc_label, tc in conditions:
        style = compute_opponent_style(
            requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc,
        )
        traps = compute_exploitable_traps(
            conn, requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, sparring_time_control=tc,
        )
        rows: List[Dict[str, Any]] = []
        for p in positions:
            candidates = p["candidates"]
            if not candidates:
                continue
            board = chess.Board(p["fen"])
            rerank = rerank_candidates(
                candidates=candidates, style=style, board=board,
                exploitable_trap_keys=traps, near_book_weights=p["near_book_weights"],
            )
            raw_r = rank_of(raw_order(candidates), p["actual_uci"])
            # exact-book-only baseline: mirror the book move when available,
            # otherwise raw Maia top-1 (no style / near-book signals).
            if p["book_status"] == "in_book":
                book_r = 1 if p.get("book_move") == p["actual_uci"] else None
            else:
                book_r = raw_r
            rows.append({
                "book": p["book_status"],
                "ply": p["ply"],
                "raw_rank": raw_r,
                "rerank_rank": rank_of(reranked_order(candidates, rerank), p["actual_uci"]),
                "book_only_rank": book_r,
            })
        per_tc[tc_label] = rows
        per_tc_meta[tc_label] = {
            "style_sufficient": style["sufficient"],
            "eff_sample": style["effective_sample_size"],
            "game_count": style["game_count"],
            "sac_freq": style["sacrifice_frequency"],
            "avg_len": style["average_game_length"],
            "traps": len(traps),
            "queens_stay": style["queens_stay_on_rate"],
        }

    # --- report ---
    print("\n" + "=" * 80)
    print("HELD-OUT REPLAY ACCURACY — sparring bot vs. real opponent moves")
    print("=" * 80)
    print(f"opponent: {OPPONENT} ({PROVIDER})   user: {UID}")
    print(f"split: chronological {int(BUILD_FRACTION*100)}/{int((1-BUILD_FRACTION)*100)} "
          f"-> build={len(build)} held-out={len(heldout)}  (total={n}, "
          f"{n_skipped_960} Chess960 skipped)")
    print(f"build/held-out game-id overlap: {len(overlap)}")
    print(f"opponent rating (build set only): {rating}")
    print(f"held-out opponent-move positions: {len(positions)}")
    print(f"multipv: {multipv}")
    print(f"confidence floor: {CONFIDENCE_FLOOR} positions/bucket "
          f"('*' = below floor, treat as low-confidence)")

    for tc_label in ("blitz", "neutral"):
        results = per_tc[tc_label]
        raw = build_bucket_tables(results, "raw_rank")
        rer = build_bucket_tables(results, "rerank_rank")
        book = build_bucket_tables(results, "book_only_rank")
        meta = per_tc_meta[tc_label]
        print(f"\n{'=' * 80}\nTC = {tc_label}   "
              f"(style sufficient={meta['style_sufficient']}, "
              f"eff_sample={meta['eff_sample']}, games={meta['game_count']}, "
              f"traps={meta['traps']})")
        print(f"      sac_freq={meta['sac_freq']}, avg_game_len={meta['avg_len']}, "
              f"queens_stay={meta['queens_stay']}")

        print("\n  Columns: n  top1  top3  top5  top8  top10  median_rank  "
              f"miss(=not in Maia top-{multipv})")
        for title, tab in (("RAW MAIA (baseline)", raw),
                           ("RERANKED (Maia + layers)", rer),
                           ("BOOK-ONLY (exact-book mirror)", book)):
            print(f"\n  --- {title} ---")
            hdr = "    book \\ ply     " + "".join(f"{pb:>64}" for pb in PLY_ORDER)
            print(hdr)
            for bbook in BOOK_ORDER:
                cells = [tab[(bbook, pb)]["n"] and fmt(tab[(bbook, pb)]["n"], tab[(bbook, pb)]) or "".ljust(64) for pb in PLY_ORDER]
                print(f"    {bbook:<14}   " + "   ".join(cells))
            ov = tab["_overall"]
            print(f"    {'OVERALL':<14}   " + fmt(ov["n"], ov))

        # rank distribution per book status (overall)
        print(f"\n  Rank distribution of actual move "
              f"(1..{multipv} = rank, {multipv + 1} = not in top-{multipv}):")
        for key, label in (("raw_rank", "raw Maia"), ("rerank_rank", "reranked")):
            dist = {book: Counter() for book in BOOK_ORDER}
            for r in results:
                rank = r[key]
                dist[r["book"]][((multipv + 1) if rank is None else rank)] += 1
            print(f"    {label:<10}: " + " | ".join(
                f"{book}: " + ",".join(f"{k}:{dist[book][k]}" for k in sorted(dist[book]))
                for book in BOOK_ORDER
            ))

    # --- three-way delta (headline) ---
    print(f"\n{'=' * 80}\nRERANKER & BOOK-ONLY vs BASELINE (delta in pp)\n{'=' * 80}")
    for tc_label in ("blitz", "neutral"):
        results = per_tc[tc_label]
        raw = build_bucket_tables(results, "raw_rank")
        rer = build_bucket_tables(results, "rerank_rank")
        book = build_bucket_tables(results, "book_only_rank")
        print(f"\n  TC = {tc_label}")
        print(f"    {'bucket':<24} {'n':>5}  {'rerank d1':>10} {'rerank d3':>10} "
              f"{'book-only d1':>13} {'book-only d3':>13}")
        for bbook in BOOK_ORDER:
            for pb in PLY_ORDER:
                a = raw[(bbook, pb)]
                b = rer[(bbook, pb)]
                c = book[(bbook, pb)]
                if a["n"] == 0:
                    continue
                flag = "*" if 0 < a["n"] < CONFIDENCE_FLOOR else " "
                print(f"    {bbook+' / '+pb:<24} {a['n']:>4}{flag} "
                      f"{(b['top1']-a['top1'])*100:>+9.1f}pp {(b['top3']-a['top3'])*100:>+9.1f}pp "
                      f"{(c['top1']-a['top1'])*100:>+12.1f}pp {(c['top3']-a['top3'])*100:>+12.1f}pp")
        a = raw["_overall"]
        b = rer["_overall"]
        c = book["_overall"]
        print(f"    {'OVERALL':<24} {a['n']:>5}  {(b['top1']-a['top1'])*100:>+9.1f}pp "
              f"{(b['top3']-a['top3'])*100:>+9.1f}pp "
              f"{(c['top1']-a['top1'])*100:>+12.1f}pp {(c['top3']-a['top3'])*100:>+12.1f}pp")

    print(f"\nDone in {time.time() - t0:.0f}s.")


if __name__ == "__main__":
    main()
