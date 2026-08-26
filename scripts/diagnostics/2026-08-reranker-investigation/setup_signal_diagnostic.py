"""
setup_signature score-distribution & firing-pattern diagnostic (TEST-ONLY).

Pure measurement. No production code or weight changes.

For every held-out position (reusing the heldout_replay_accuracy.py cache
and the build-set TEMP-TABLE isolation pattern), collect:

  1. Per-candidate raw setup-similarity score (setup_S, the recency-weighted
     max Jaccard composite) and the final multiplier (1 + 2.5 * S), then
     aggregate the MAX multiplier across candidates per position:
     quantiles + "does setup bias flip top-1 vs raw Maia" + fraction of
     positions where any candidate gets mult > 1.05/1.10/1.20.

  2. The same, split by book status (in_book / near_book / out_of_book) and
     by a sharper exact-novelty axis (exact position_key present in the
     build set vs never-seen-exact).

  3. For never-seen positions: characterize the RAW (unweighted) score
     distribution across the full snapshot pool for a sample of positions
     -- flat noise vs. genuine spike.

  4. Relationship to the actual move: top-1-after-setup when the actual
     move has the highest setup score vs. not; then spot-check "wrong
     promotion" cases for a qualitative read.
"""

import json
import math
import os
import sys
import time
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
from services.opponent_repertoire import _position_key  # noqa: E402
from services.opponent_style import compute_opponent_style  # noqa: E402
from services.opponent_style_reranker import (  # noqa: E402
    SETUP_SIGNATURE_BIAS_STRENGTH,
    _filter_signatures_by_family,
    _pov_normalized_squares,
    _setup_similarity,
    rerank_candidates,
)
from services.opponent_traps import compute_exploitable_traps  # noqa: E402

UID = "user_3I3cNvpU6XgdUBTqEaYASS251nJ"
PROVIDER = "chesscom"
OPPONENT = "iaminspiredbroo"

BOOK_ORDER = ["in_book", "near_book", "out_of_book"]
NOVEL_ORDER = ["exact_seen", "exact_novel"]


class _StubPool:
    def __init__(self, conn):
        self.conn = conn

    def getconn(self):
        return self.conn

    def putconn(self, conn):  # noqa: ARG002
        return

    def closeall(self):
        self.conn.close()


def build_ids_from_db() -> List[str]:
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


def install_build_set_isolation(build_ids: List[str]):
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


def build_set_position_keys(conn) -> set:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT position_key FROM opponent_repertoire_moves
        WHERE requested_by_user_id = %s AND provider = %s
          AND LOWER(opponent_username) = LOWER(%s)
        """,
        (UID, PROVIDER, OPPONENT),
    )
    keys = {row[0] for row in cur.fetchall()}
    return keys


def quantiles(vals: List[float], qs=(0.5, 0.75, 0.9, 0.95, 0.99, 1.0)):
    if not vals:
        return {q: None for q in qs}
    s = sorted(vals)
    out = {}
    for q in qs:
        idx = min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))
        out[q] = s[idx]
    return out


def snapshot_sets(sig):
    pawn = frozenset(sig.get("pawn_squares") or ())
    piece = frozenset(
        sq for letter in ("N", "B", "R", "Q", "K")
        for sq in (sig.get("piece_squares") or {}).get(letter, [])
    )
    return pawn, piece


def main() -> None:
    positions_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/heldout_positions.json"
    sample_n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    with open(positions_path) as fh:
        positions = json.load(fh)

    build_ids = build_ids_from_db()
    conn = install_build_set_isolation(build_ids)
    seen_keys = build_set_position_keys(conn)

    tc = "blitz"
    style = compute_opponent_style(
        requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control=tc,
    )
    traps = compute_exploitable_traps(
        conn, requested_by_user_id=UID, provider=PROVIDER,
        opponent_username=OPPONENT, sparring_time_control=tc,
    )

    setup_signatures = style.get("setup_signatures") or []
    print(f"setup_signatures in style profile: {len(setup_signatures)}")
    print(f"setup_present: {bool(setup_signatures)}")
    if setup_signatures:
        fams = Counter(s.get("family") for s in setup_signatures)
        print(f"families: {dict(fams)}")
        wmin = min(s.get('weight', 1.0) for s in setup_signatures)
        wmax = max(s.get('weight', 1.0) for s in setup_signatures)
        print(f"snapshot recency+tc weight range: [{wmin:.3f}, {wmax:.3f}]")

    # ---- per-position setup metrics ---------------------------------------
    recs = []  # one dict per position
    wrong_promotions = []  # spot-check pool
    never_seen_samples = []  # for distribution-shape analysis

    # precompute snapshot (pawn, piece, weight) tuples
    snap_tuples = [
        (snapshot_sets(s)[0], snapshot_sets(s)[1], float(s.get("weight") or 1.0))
        for s in setup_signatures
    ]

    for p in positions:
        candidates = p["candidates"]
        if not candidates:
            continue
        board = chess.Board(p["fen"])
        book = p["book_status"]
        actual = p["actual_uci"]
        pk = _position_key(board)
        exact_seen = pk in seen_keys

        rerank = rerank_candidates(
            candidates=candidates, style=style, board=board,
            exploitable_trap_keys=traps, near_book_weights=p["near_book_weights"],
        )
        rows = (
            rerank["bias_breakdown"]["weights"]
            if rerank.get("applied_bias") else None
        )
        if not rows:
            continue

        setup_Ss = [r.get("setup_S") for r in rows]
        setup_mults = [r.get("setup_multiplier") for r in rows]
        base_ws = [r.get("base_weight") for r in rows]
        moves = [r.get("move") for r in rows]
        # setup-only weight = base * setup_mult
        setup_only_weights = [
            b * (m if m is not None else 1.0)
            for b, m in zip(base_ws, setup_mults)
        ]
        max_mult = max(m for m in setup_mults if m is not None)
        max_S = max((s for s in setup_Ss if s is not None), default=None)
        raw_top = moves[0]
        setup_top_idx = max(range(len(setup_only_weights)),
                            key=lambda i: setup_only_weights[i])
        changed_top1 = setup_top_idx != 0
        any_gt = {t: any(m > t for m in setup_mults if m is not None)
                  for t in (1.05, 1.10, 1.20)}

        actual_idx = moves.index(actual) if actual in moves else None
        actual_setup_S = setup_Ss[actual_idx] if actual_idx is not None else None
        actual_is_max = (
            actual_setup_S is not None and max_S is not None
            and abs(actual_setup_S - max_S) < 1e-9
        )
        setup_rank_of_actual = (
            sum(1 for w in setup_only_weights if w > setup_only_weights[actual_idx]) + 1
            if actual_idx is not None else None
        )

        recs.append({
            "book": book,
            "novel": "exact_seen" if exact_seen else "exact_novel",
            "ply": p["ply"],
            "max_mult": max_mult,
            "max_S": max_S,
            "changed_top1": changed_top1,
            "any_gt": any_gt,
            "actual_in_cands": actual_idx is not None,
            "actual_is_max_setup": actual_is_max,
            "setup_rank_of_actual": setup_rank_of_actual,
            "actual": actual,
            "promoted": moves[setup_top_idx],
            "setup_top_idx": setup_top_idx,
        })

        # wrong-promotion pool: setup-only top != actual, and actual is a candidate
        if actual_idx is not None and moves[setup_top_idx] != actual:
            wrong_promotions.append({
                "fen": p["fen"],
                "ply": p["ply"],
                "book": book,
                "novel": "exact_seen" if exact_seen else "exact_novel",
                "actual": actual,
                "promoted": moves[setup_top_idx],
                "raw_order": moves,
                "setup_Ss": setup_Ss,
                "setup_mults": setup_mults,
                "base_ws": base_ws,
                "setup_rank_of_actual": setup_rank_of_actual,
            })

        # never-seen sample for distribution-shape analysis
        if (not exact_seen) and len(never_seen_samples) < sample_n and actual_idx is not None:
            never_seen_samples.append((board, actual, setup_signatures, p["ply"], p["fen"]))

    n = len(recs)

    def report_group(label, sub):
        if not sub:
            print(f"  {label:<14} n=0")
            return
        mm = [r["max_mult"] for r in sub]
        ms = [r["max_S"] for r in sub if r["max_S"] is not None]
        q = quantiles(mm)
        qs = quantiles(ms, qs=(0.5, 0.9, 0.99, 1.0))
        chg = sum(1 for r in sub if r["changed_top1"]) / len(sub)
        gt = {t: sum(1 for r in sub if r["any_gt"][t]) / len(sub)
              for t in (1.05, 1.10, 1.20)}
        print(f"  {label:<14} n={len(sub):<5} "
              f"maxMult p50={q[0.5]:.3f} p75={q[0.75]:.3f} p90={q[0.9]:.3f} "
              f"p95={q[0.95]:.3f} p99={q[0.99]:.3f} max={q[1.0]:.3f}")
        print(f"  {label:<14}        maxS    p50={qs[0.5]:.3f} p90={qs[0.9]:.3f} "
              f"p99={qs[0.99]:.3f} max={qs[1.0]:.3f}   "
              f"| flipTop1={chg*100:.1f}%  anyMult>1.05={gt[1.05]*100:.1f}% "
              f">1.10={gt[1.10]*100:.1f}% >1.20={gt[1.20]*100:.1f}%")

    print("\n" + "=" * 78)
    print(f"SETUP_SIGNATURE DIAGNOSTIC — {OPPONENT} (TC={tc}) — {n} positions")
    print(f"SETUP_SIGNATURE_BIAS_STRENGTH = {SETUP_SIGNATURE_BIAS_STRENGTH} "
          f"(mult = 1 + {SETUP_SIGNATURE_BIAS_STRENGTH} * S)")
    print("=" * 78)
    print("\n--- max multiplier per position, OVERALL + by book status ---")
    report_group("OVERALL", recs)
    for b in BOOK_ORDER:
        report_group(b, [r for r in recs if r["book"] == b])
    print("\n--- by exact novelty (position_key in build set?) ---")
    for b in NOVEL_ORDER:
        report_group(b, [r for r in recs if r["novel"] == b])

    # histogram of max multiplier
    print("\n--- histogram of max multiplier per position (OVERALL) ---")
    mm_all = sorted(r["max_mult"] for r in recs)
    bins = [(1.0, 1.02), (1.02, 1.05), (1.05, 1.10), (1.10, 1.20),
            (1.20, 1.40), (1.40, 1.75), (1.75, 2.2), (2.2, 3.5)]
    for lo, hi in bins:
        c = sum(1 for m in mm_all if lo <= m < hi)
        print(f"  [{lo:.2f},{hi:.2f}): {c:4d}  {'#' * min(60, c)}")

    # ---- relationship to actual move --------------------------------------
    print("\n--- relationship to actual move (positions where actual is a candidate) ---")
    with_actual = [r for r in recs if r["actual_in_cands"]]
    n_wa = len(with_actual)
    max_setup_group = [r for r in with_actual if r["actual_is_max_setup"]]
    not_max_setup_group = [r for r in with_actual if not r["actual_is_max_setup"]]
    def top1_rate(sub):
        if not sub:
            return None
        return sum(1 for r in sub if r["setup_rank_of_actual"] == 1) / len(sub)
    print(f"  positions where actual in candidates: {n_wa}")
    print(f"  actual has highest setup score: {len(max_setup_group)} -> "
          f"setup-only top1 = {top1_rate(max_setup_group)*100:.1f}%")
    print(f"  actual does NOT have highest setup score: {len(not_max_setup_group)} -> "
          f"setup-only top1 = {top1_rate(not_max_setup_group)*100:.1f}%")

    # ---- never-seen raw score distribution shape --------------------------
    print("\n--- never-seen: raw (unweighted) score distribution across snapshot pool ---")
    print(f"  sampled {len(never_seen_samples)} never-seen-exact positions "
          f"(actual-move resulting board vs {len(snap_tuples)} snapshots)")
    spikiness = []
    for board, actual, sigs, ply, fen in never_seen_samples:
        live_pawn, _, live_opp_pawn = _pov_normalized_squares(board, board.turn)
        eff_sigs, fam, conf = _filter_signatures_by_family(
            sigs, live_pawn, live_opp_pawn, player_color=None)
        pool = eff_sigs if eff_sigs is not None else sigs
        rb = board.copy(stack=False)
        mv = chess.Move.from_uci(actual)
        if mv not in rb.legal_moves:
            continue
        rb.push(mv)
        cand_pawn, cand_piece, _ = _pov_normalized_squares(rb, board.turn)
        scores = []
        for s in pool:
            hp, hpc = snapshot_sets(s)
            scores.append(_setup_similarity(cand_pawn, cand_piece, hp, hpc))
        scores.sort()
        n_s = len(scores)
        p50 = scores[n_s // 2]
        p95 = scores[min(n_s - 1, math.ceil(0.95 * n_s) - 1)]
        p99 = scores[min(n_s - 1, math.ceil(0.99 * n_s) - 1)]
        mx = scores[-1]
        spike = mx / p95 if p95 > 0 else float("inf")
        spikiness.append(spike)
        hi_ct = {t: sum(1 for s in scores if s >= t) for t in (0.3, 0.4, 0.5)}
        print(f"    ply={ply:3d} n={n_s:4d} p50={p50:.3f} p95={p95:.3f} "
              f"p99={p99:.3f} max={mx:.3f}  spike=max/p95={spike:.2f}  "
              f"n>=.3:{hi_ct[0.3]} n>=.4:{hi_ct[0.4]} n>=.5:{hi_ct[0.5]}  "
              f"fam={fam}")

    if spikiness:
        spikiness.sort()
        print(f"  spike-ratio (max/p95) across sampled never-seen positions: "
              f"p50={spikiness[len(spikiness)//2]:.2f} "
              f"p90={spikiness[min(len(spikiness)-1, math.ceil(0.9*len(spikiness))-1)]:.2f} "
              f"max={spikiness[-1]:.2f}")

    # ---- wrong-promotion spot-check dump --------------------------------
    print("\n--- wrong-promotion cases (setup-only promotes non-actual to top) ---")
    print(f"  total wrong-promotion cases: {len(wrong_promotions)}")
    for w in wrong_promotions[:15]:
        print(f"  fen={w['fen']}  ply={w['ply']}  book={w['book']} novel={w['novel']}")
        print(f"    actual={w['actual']}  promoted={w['promoted']}  "
              f"setup_rank_actual={w['setup_rank_of_actual']}")
        for i, m in enumerate(w["raw_order"]):
            print(f"      cand#{i+1} {m:6s} base={w['base_ws'][i]:.4f} "
                  f"setup_S={w['setup_Ss'][i]} mult={w['setup_mults'][i]}")
    print("\nDone.")


if __name__ == "__main__":
    main()
