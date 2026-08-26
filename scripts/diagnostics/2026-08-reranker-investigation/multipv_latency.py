"""
Full-pipeline latency measurement vs multipv (TEST-ONLY).

Measures the parts of get_sparring_move() that scale with the Maia
candidate pool size:

  * Maia:   Maia3Engine.best_move_candidates(board, multipv=N)
  * rerank: rerank_candidates(candidates, style, board, traps, near_book)

The reranker's setup_signature Jaccard loop runs per-candidate against the
family-filtered snapshot pool, so its cost grows with N -- this is the cost
the task's prior "354ms vs 202ms" Maia-only estimate did NOT capture.

Methodology (matching the prior latency work in this project): 2 warm-up
iterations per multipv (discarded), then measured iterations across
multiple independent positions/invocations. Reports median / p95 / max for
the combined pipeline and for the Maia and rerank components separately.

Fixed per-move costs (style/traps cache, near-book query, Stockfish safety
check) are multipv-independent and are reported once as context, not part
of the multipv comparison.
"""

import json
import os
import statistics
import sys
import time

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
from engines.maia_engine import Maia3Engine  # noqa: E402
from services.opponent_repertoire import pick_near_repertoire_moves  # noqa: E402
from services.opponent_style import compute_opponent_style  # noqa: E402
from services.opponent_style_reranker import rerank_candidates  # noqa: E402
from services.opponent_traps import compute_exploitable_traps  # noqa: E402

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
OPPONENT = "iaminspiredbroo"

MULTIPVS = [5, 8, 10]
WARMUPS = 2
MEASURED_RUNS_PER_POSITION = 5
POSITION_CACHE = "/tmp/opencode/heldout_positions.json"


def pctile(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def pick_positions(positions, k=6):
    """Out-of-book middlegame positions (where the reranker actually runs)."""
    chosen = []
    for p in positions:
        if p["book_status"] == "in_book":
            continue
        if not (10 < p["ply"] <= 30):
            continue
        if not p["candidates"]:
            continue
        chosen.append(p)
        if len(chosen) >= k:
            break
    return chosen


def main():
    with open(POSITION_CACHE) as fh:
        all_positions = json.load(fh)
    positions = pick_positions(all_positions)
    if not positions:
        raise SystemExit("no out-of-book middlegame positions found")

    database.init_db()
    conn = database.connection_pool.getconn()

    engine = Maia3Engine()
    engine.start()

    style = compute_opponent_style(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control="blitz",
    )
    n_snaps = len(style.get("setup_signatures") or [])
    traps = compute_exploitable_traps(
        conn, requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control="blitz",
    )

    # Fixed costs (multipv-independent), timed once for context.
    t0 = time.perf_counter()
    near_book_all = {
        p["fen"]: pick_near_repertoire_moves(
            requested_by_user_id=UID, provider=PROVIDER,
            opponent_username=OPPONENT, board=chess.Board(p["fen"]),
        )
        for p in positions
    }
    near_book_cost = time.perf_counter() - t0

    print("=" * 78)
    print("FULL-PIPELINE LATENCY vs MULTIPV (Maia candidates + reranker)")
    print("=" * 78)
    print(f"positions: {len(positions)} (out-of-book middlegame, ply 11-30)")
    print(f"setup signature pool: {n_snaps} snapshots "
          f"(family-filtered per position)")
    print(f"near_book query (fixed, multipv-independent): "
          f"median {near_book_cost / len(positions) * 1000:.1f} ms/pos "
          f"(total {near_book_cost * 1000:.0f} ms for {len(positions)} positions)")
    print(f"style/traps: computed once (cached in production, ~dict lookup "
          f"after first call)")
    print(f"note: Stockfish safety check (~2 evals) is multipv-independent "
          f"and not included in the comparison")

    for mpv in MULTIPVS:
        maia_ts = []
        rerank_ts = []
        total_ts = []
        # warm-up (discarded)
        for p in positions[:2]:
            engine.best_move_candidates(
                chess.Board(p["fen"]), multipv=mpv,
                self_elo=1546, oppo_elo=1546,
            )
        for p in positions:
            board = chess.Board(p["fen"])
            nb = near_book_all[p["fen"]]
            for _ in range(MEASURED_RUNS_PER_POSITION):
                t0 = time.perf_counter()
                candidates = engine.best_move_candidates(
                    board, multipv=mpv, self_elo=1546, oppo_elo=1546,
                )
                t1 = time.perf_counter()
                rerank_candidates(
                    candidates=candidates, style=style, board=board,
                    exploitable_trap_keys=traps, near_book_weights=nb,
                )
                t2 = time.perf_counter()
                maia_ts.append((t1 - t0) * 1000)
                rerank_ts.append((t2 - t1) * 1000)
                total_ts.append((t2 - t0) * 1000)

        n = len(total_ts)
        print(f"\nmultipv={mpv}  (n={n} measured runs)")
        for label, ts in (
            ("Maia candidates", maia_ts),
            ("reranker       ", rerank_ts),
            ("TOTAL pipeline ", total_ts),
        ):
            med = statistics.median(ts)
            p95 = pctile(ts, 0.95)
            mx = max(ts)
            print(f"  {label}  median={med:6.1f} ms  "
                  f"p95={p95:6.1f} ms  max={mx:6.1f} ms")

    engine.close()


if __name__ == "__main__":
    main()
