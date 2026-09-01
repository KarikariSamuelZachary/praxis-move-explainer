"""
Recurrence-gate diagnosis (TEST-ONLY, DB re-analysis -- NO Maia).

Question: would a "recurrence >= 3 distinct games" gate on near-book
retrieved moves fix near-book's precision problem, or would it just prune
noise from a window that is fundamentally imprecise?

The candidate-injection prototype measured near-book retrieval at ~2-3%
per-move precision on reachable-ceiling positions (the actual move is
missing from Maia's top-5 but present in the near-book retrieved map).
RecMem's recurrence-gating idea (only trust a retrieved move once it
recurs across >=3 distinct games) only helps if the underlying problem is
one-off noise diluting a real signal. It does nothing if the +/-2 ply
window itself groups structurally dissimilar positions, producing
multiple genuinely-different moves that EACH show real recurrence.

This script re-derives the near-book retrieved maps and the RAW occurrence
counts (distinct games, not the recency-weighted score) for every held-out
position, entirely from the already-indexed `opponent_repertoire_moves`
table -- no Maia inference, no re-measurement of style/repertoire/traps.

Classifies each near-book-reachable position (actual move present in the
retrieved window) into:
  (a) actual count >= 3 AND it is the only move with count >= 3
      -> a recurrence>=3 gate preserves and concentrates the signal.
  (b) actual count 1-2
      -> the gate filters the actual move out too (gating doesn't recover).
  (c) >= 2 distinct moves each with count >= 3
      -> real structural ambiguity in the window; gating can't concentrate.

Also reports the overall prune-rate: across ALL near-book-retrieved moves,
what fraction have raw count >= 3 (i.e. how aggressively a >=3 gate prunes).

Methodology parity with the prototype / heldout harness:
  * chronological 80/20 split (oldest -> build, newest -> held-out)
  * build-set isolation via session-local temp tables shadowing
    opponent_games / opponent_repertoire_moves / opponent_game_blunders
  * Chess960 games skipped (Maia parity, even though no Maia is called here)
  * same ply-window near-book query (played_color match, ply_index +-2,
    position_key <> live) as pick_near_repertoire_moves.

NOTE ON SCOPE vs the original prototype run: the prototype's "reachable
ceiling" additionally required the actual move to be MISSING from Maia's
top-5. That Maia-candidate cache was stored under /tmp and is gone; this
task forbids re-running Maia. This script therefore scopes to "near-book
retrieved the actual move" (the recoverable-signal population), which is a
SUPERSET of the original reachable-ceiling (it also includes positions
where Maia already had the move in top-5). The (a)/(b)/(c) structure of the
near-book window itself is identical either way; the only difference is a
scope filter that removes positions where near-book recovery is redundant.
"""

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter, defaultdict
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

import chess
import chess.pgn
import psycopg2
from psycopg2.extras import RealDictCursor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    pass

from core import database  # noqa: E402
from services.opponent_repertoire import (  # noqa: E402
    _candidate_ply_index,
    _position_key,
    pick_near_repertoire_moves,
    pick_repertoire_move,
)
from services.opponent_style import _normalize_username, _opponent_color  # noqa: E402

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
BUILD_FRACTION = 0.8
NEAR_BOOK_PLY_WINDOW = 2
RECURRENCE_THRESHOLD = 3


class _StubPool:
    def __init__(self, conn):
        self.conn = conn

    def getconn(self):
        return self.conn

    def putconn(self, conn):  # noqa: ARG002
        return

    def closeall(self):
        self.conn.close()


def fetch_games(opponent: str) -> List[Dict[str, Any]]:
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
                (UID, PROVIDER, opponent),
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


def _is_chess960(pgn: str) -> bool:
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


def classify_position(opponent: str, board: chess.Board) -> Tuple[str, Optional[Dict[str, float]]]:
    rep = pick_repertoire_move(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=opponent, board=board,
    )
    if rep:
        return "in_book", None
    nb = pick_near_repertoire_moves(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=opponent, board=board,
        ply_window=NEAR_BOOK_PLY_WINDOW,
    )
    if nb:
        return "near_book", nb
    return "out_of_book", None


def near_book_raw_counts(
    conn, opponent: str, board: chess.Board, ply_window: int = NEAR_BOOK_PLY_WINDOW
) -> Dict[str, int]:
    """RAW occurrence count (distinct games) per move in the near-book window.

    Identical WHERE clause to pick_near_repertoire_moves (same color, ply
    index within +/- ply_window, exclude the live position_key), but returns
    COUNT(DISTINCT opponent_game_id) -- the raw recurrence count, NOT the
    recency-weighted frequency.
    """
    played_color = "white" if board.turn == chess.WHITE else "black"
    current_ply_index = _candidate_ply_index(board)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT r.move_uci, COUNT(DISTINCT r.opponent_game_id)::int AS n_games
            FROM opponent_repertoire_moves r
            WHERE r.requested_by_user_id = %s
              AND r.provider = %s
              AND LOWER(r.opponent_username) = LOWER(%s)
              AND r.played_color = %s
              AND r.ply_index BETWEEN %s AND %s
              AND r.position_key <> %s
            GROUP BY r.move_uci
            """,
            (
                UID,
                PROVIDER,
                opponent,
                played_color,
                current_ply_index - ply_window,
                current_ply_index + ply_window,
                _position_key(board),
            ),
        )
        rows = cur.fetchall()
    return {r["move_uci"]: int(r["n_games"]) for r in rows}


def classify_recurrence(actual_uci: str, raw_counts: Dict[str, int]) -> str:
    """(a)/(b)/(c) classification. Mutually exclusive, exhaustive."""
    if not raw_counts:
        return "b"
    actual_count = raw_counts.get(actual_uci, 0)
    counts = list(raw_counts.values())
    n_ge3 = sum(1 for c in counts if c >= RECURRENCE_THRESHOLD)
    if actual_count <= 2:
        return "b"
    if n_ge3 >= 2:
        return "c"
    return "a"


def analyze(opponent: str) -> Dict[str, Any]:
    random.seed(1234)
    t0 = time.time()
    games = fetch_games(opponent)
    n_skipped = sum(1 for g in games if _is_chess960(g["pgn"]))
    games = [g for g in games if not _is_chess960(g["pgn"])]
    n = len(games)
    n_build = math.floor(n * BUILD_FRACTION)
    build = games[:n_build]
    heldout = games[n_build:]
    build_ids = [g["id"] for g in build]

    conn = install_build_set_isolation(build_ids)
    normalized = _normalize_username(opponent)

    n_nearbook = 0
    n_reachable = 0
    # classification tallies
    abc: Dict[str, int] = {"a": 0, "b": 0, "c": 0}
    # actual_count histogram for reachable positions
    actual_count_hist: Counter = Counter()
    # number-of-moves>=3 histogram for reachable positions (structural)
    n_ge3_hist: Counter = Counter()
    # plurality stats
    n_actual_plurality = 0
    # prune-rate accumulators across ALL near-book positions
    total_retrieved = 0
    survived = 0
    # per-position prune-rate (fraction of that position's retrieved moves
    # with count >= 3), for mean/median reporting
    per_position_prune: List[float] = []

    for g in heldout:
        for ply, board, actual_uci in iter_opponent_positions(g["pgn"], normalized):
            conn = heal_db_connection(conn, build_ids)
            status, nb = classify_position(opponent, board)
            if status != "near_book":
                continue
            n_nearbook += 1
            raw_counts = near_book_raw_counts(conn, opponent, board)

            # overall prune-rate over every retrieved move in this window
            for cnt in raw_counts.values():
                total_retrieved += 1
                if cnt >= RECURRENCE_THRESHOLD:
                    survived += 1
            if raw_counts:
                per_position_prune.append(
                    sum(1 for c in raw_counts.values()
                        if c >= RECURRENCE_THRESHOLD) / len(raw_counts)
                )

            if actual_uci not in raw_counts:
                continue
            n_reachable += 1
            cls = classify_recurrence(actual_uci, raw_counts)
            abc[cls] += 1
            ac = raw_counts[actual_uci]
            actual_count_hist[ac] += 1
            n_ge3_hist[sum(1 for c in raw_counts.values() if c >= 3)] += 1
            if ac == max(raw_counts.values()):
                n_actual_plurality += 1

    conn.close()
    return {
        "opponent": opponent,
        "n_games_total": n,
        "n_skipped_960": n_skipped,
        "n_build": len(build),
        "n_heldout_games": len(heldout),
        "n_nearbook": n_nearbook,
        "n_reachable": n_reachable,
        "abc": abc,
        "actual_count_hist": dict(sorted(actual_count_hist.items())),
        "n_ge3_hist": dict(sorted(n_ge3_hist.items())),
        "n_actual_plurality": n_actual_plurality,
        "total_retrieved": total_retrieved,
        "survived": survived,
        "per_position_prune": per_position_prune,
        "elapsed": time.time() - t0,
    }


def pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "  n/a"


def report(res: Dict[str, Any]) -> None:
    o = res["opponent"]
    print("=" * 80)
    print(f"opponent: {o}")
    print("=" * 80)
    print(f"  games (post-960 skip): {res['n_games_total']} "
          f"({res['n_skipped_960']} Chess960 skipped)")
    print(f"  chronological {int(BUILD_FRACTION*100)}/{int((1-BUILD_FRACTION)*100)} "
          f"-> build={res['n_build']} held-out={res['n_heldout_games']}")
    print(f"  near_book positions (held-out): {res['n_nearbook']}")
    print(f"  near-book REACHABLE (actual move in retrieved window): "
          f"{res['n_reachable']}")

    abc = res["abc"]
    n_r = res["n_reachable"]
    print("\n  (a)/(b)/(c) breakdown of reachable positions:")
    print(f"    (a) actual>=3 & unique >=3 move  : {abc['a']:>4}  "
          f"{pct(abc['a'], n_r)}   <- gate would preserve the signal")
    print(f"    (b) actual count 1-2 (thin)      : {abc['b']:>4}  "
          f"{pct(abc['b'], n_r)}   <- gate filters actual out too")
    print(f"    (c) >=2 moves each >=3 (ambiguous): {abc['c']:>4}  "
          f"{pct(abc['c'], n_r)}   <- structural ambiguity")

    print("\n  actual-move raw occurrence-count histogram (reachable positions):")
    hist = res["actual_count_hist"]
    for k in sorted(hist):
        bar = "#" * hist[k]
        print(f"    count={k:>3}: {hist[k]:>4}  {bar}")

    print("\n  '# moves with count >=3 in window' histogram (reachable):")
    for k in sorted(res["n_ge3_hist"]):
        print(f"    {k} move(s)>=3: {res['n_ge3_hist'][k]:>4}")

    print(f"\n  reachable positions where actual is the plurality (top count): "
          f"{res['n_actual_plurality']} ({pct(res['n_actual_plurality'], n_r)})")

    # prune rate
    tot = res["total_retrieved"]
    surv = res["survived"]
    print("\n  OVERALL PRUNE-RATE under recurrence>=3 (all near-book retrieved moves):")
    print(f"    total retrieved moves: {tot}")
    print(f"    survive (count>=3):    {surv}  ({pct(surv, tot)})")
    print(f"    PRUNED (count<3):      {tot - surv}  ({pct(tot - surv, tot)})")
    ppr = res["per_position_prune"]
    if ppr:
        mean = sum(ppr) / len(ppr)
        med = sorted(ppr)[len(ppr) // 2]
        print(f"    per-position mean survival: {mean*100:.1f}%  median: {med*100:.1f}%")
    print(f"  (elapsed {res['elapsed']:.0f}s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--opponents", nargs="+",
                    default=["samuel4real", "iaminspiredbroo"])
    args = ap.parse_args()

    results = [analyze(o) for o in args.opponents]
    for res in results:
        report(res)

    # cross-account verdict
    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)
    for res in results:
        abc = res["abc"]
        n_r = res["n_reachable"] or 1
        dom = max(("a", "b", "c"), key=lambda k: abc[k])
        share = abc[dom] / n_r * 100
        label = {"a": "gating would likely help (a-dominated)",
                 "b": "mixed/marginal (b-dominated)",
                 "c": "gating won't help -- window itself is the problem (c-dominated)"}[dom]
        print(f"  {res['opponent']:<16} dominant class {dom} at {share:.1f}%  -> {label}")


if __name__ == "__main__":
    main()
