"""Production batch: build the clean-segment corpus (SQLite only, no prod writes).

Same verdict logic as the pilot (probe.py): SF19, Hash=16, Threads=1,
Stage 1 = MultiPV 2 @ 50k nodes, Stage 2 = MultiPV 4 @ 500k on Stage 1 PASSES
only, Stage 1 failures final, 150 cp margin, unique-longest-resistance rule.
Dropped vs pilot: fail-sample Stage 2, solver check, convergence run.
Kept: probe_calls + rolling-nps logging.

Cells are (rating band x solver-move bucket 3/4/5/6+ x family), worked
deepest buckets first. Targets are per BAND: distinct puzzles with a
non-trivial clean segment of length >= N (--target-n3/4/5/6).

Usage:
    venv/bin/python corpus.py --out corpus.sqlite --max-hours 20
"""

from __future__ import annotations

import argparse
import datetime
import os
import random
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from puzzle_line import BANDS, band_of, family_of
from engine_probe import EnginePool
from storage import connect, dumps
import probe as probe_mod

BAND_LABELS = [f"{lo}+" if hi is None else f"{lo}-{hi}" for lo, hi in BANDS]
FAMILIES = ("mate", "quiet", "endgame", "forcing")
DEPTHS = ("6+", "5", "4", "3")  # deepest buckets first
NS = (3, 4, 5, 6)
DEPTH_MAX = {"3": 3, "4": 4, "5": 5, "6+": 99}  # longest segment a cell can yield

CELLS = [(b, d, f) for d in DEPTHS for b in BAND_LABELS for f in FAMILIES]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Blind-calculation corpus build")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--db-url", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--stockfish", default=None)
    ap.add_argument("--hash-mb", type=int, default=16)
    ap.add_argument("--nodes-stage1", type=int, default=50_000)
    ap.add_argument("--nodes-stage2", type=int, default=500_000)
    ap.add_argument("--threshold", type=int, default=150)
    ap.add_argument("--target-n3", type=int, default=1500)
    ap.add_argument("--target-n4", type=int, default=1000)
    ap.add_argument("--target-n5", type=int, default=500)
    ap.add_argument("--target-n6", type=int, default=250)
    ap.add_argument("--chunk", type=int, default=100)
    ap.add_argument("--fetch0", type=int, default=3000)
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--report", default=None)
    return ap.parse_args(argv)


def corpus_write_result(con: sqlite3.Connection, run_id: str, res: dict,
                        seg_rows: list[dict]) -> None:
    """Same rows as the pilot plus verified_level/forced_reply_count; segment
    insert is OR IGNORE so a reprocessed puzzle can never duplicate rows."""
    cand = res["cand"]
    p, line = cand["p"], cand["line"]
    con.execute(
        "INSERT OR IGNORE INTO probe_puzzles (run_id, puzzle_id, rating,"
        " themes, popularity, nb_plays, band, family, depth_bucket,"
        " solver_move_count, puzzle_fen, solver_line_json, defender_line_json,"
        " solver_check_json, status, engine_calls)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, p["id"], p["rating"], " ".join(p["themes"]),
         p.get("popularity"), p.get("nb_plays"), cand["band"], cand["family"],
         cand["depth"], line["solver_move_count"], res["puzzle_fen"],
         dumps(line["solver_moves"]), dumps(line["defender_moves"]),
         dumps([]), res["status"], res["calls"]))
    for pl in res["plies"]:
        con.execute(
            "INSERT OR IGNORE INTO probe_defender_plies (run_id, puzzle_id,"
            " reply_index, fen_before, stored_move, single_legal, stage1_json,"
            " stage2_json, flipped, fail_sample, final_verdict, margin_cp,"
            " best_mate, second_mate, mate_involved, wdl_margin)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, p["id"], pl["i"], pl["fen_before"], pl["stored"],
             pl["single_legal"],
             dumps(pl["stage1"]) if pl["stage1"] else dumps({"single_legal": 1}),
             dumps(pl["stage2"]) if pl["stage2"] else None, pl["flipped"],
             0, pl["verdict"], pl["margin"], pl["best_mate"],
             pl["second_mate"], pl["mate"], pl["wdl"]))
    for r in seg_rows:
        con.execute(
            "INSERT OR IGNORE INTO clean_forced_segments (run_id, puzzle_id,"
            " segment_fen, line_uci, start_solver_index, solver_move_count,"
            " min_cp_margin, min_wdl_margin, mate_involved, checks, captures,"
            " quiet, promotions, engine_verified_replies, trivially_forced,"
            " forced_reply_count, verified_level, quiet_share, rating, themes,"
            " engine_version, nodes_stage1, nodes_stage2, multipv_stage1,"
            " multipv_stage2, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["run_id"], r["puzzle_id"], r["segment_fen"], r["line_uci"],
             r["start"], r["n"], r["min_cp"], r["min_wdl"], r["mate"],
             r["checks"], r["captures"], r["quiet"], r["promos"],
             r["verified"], r["trivial"], r["forced"], 1,
             r["quiet_share"], r["rating"], r["themes"], r["engine_version"],
             r["n1"], r["n2"], 2, 4, r["created_at"]))
    # No commit here: the caller commits once per chunk.


def read_counters(con: sqlite3.Connection, run_id: str):
    """Recompute band/family counters from committed rows (resume-safe)."""
    band_have = {b: {n: 0 for n in NS} for b in BAND_LABELS}
    fam_proc = {f: 0 for f in FAMILIES}
    fam_seg = {f: 0 for f in FAMILIES}
    band_proc = {b: 0 for b in BAND_LABELS}
    for pid, band, fam in con.execute(
            "SELECT puzzle_id, band, family FROM probe_puzzles WHERE run_id=?",
            (run_id,)):
        band_proc[band] = band_proc.get(band, 0) + 1
        fam_proc[fam] = fam_proc.get(fam, 0) + 1
    by_puz: dict[str, list] = {}
    for pid, rating, themes, ln, triv in con.execute(
            "SELECT puzzle_id, rating, themes, solver_move_count,"
            " trivially_forced FROM clean_forced_segments WHERE run_id=?",
            (run_id,)):
        by_puz.setdefault(pid, []).append((ln, triv, rating, themes))
    for pid, segs in by_puz.items():
        nts = [ln for ln, triv, _, _ in segs if not triv]
        if not nts:
            continue
        m = max(nts)
        rating = segs[0][2]
        fam = family_of((segs[0][3] or "").split())
        band = band_of(rating)
        if fam in fam_seg:
            fam_seg[fam] += 1
        for n in NS:
            if m >= n and band in band_have:
                band_have[band][n] += 1
    stored_ids = {r[0] for r in con.execute(
        "SELECT puzzle_id FROM probe_puzzles WHERE run_id=?", (run_id,))}
    cell_proc: dict[tuple[str, str, str], int] = {}
    for b, d, f, n in con.execute(
            "SELECT band, depth_bucket, family, COUNT(*) FROM probe_puzzles"
            " WHERE run_id=? GROUP BY band, depth_bucket, family", (run_id,)):
        cell_proc[(b, d, f)] = n
    return band_have, band_proc, fam_proc, fam_seg, stored_ids, cell_proc


def band_done(band_have: dict, band: str, targets: dict) -> bool:
    return all(band_have[band][n] >= targets[n] for n in NS)


def min_unmet(band_have: dict, band: str, targets: dict) -> int | None:
    """Smallest N whose target is still unmet (None if band complete)."""
    for n in NS:
        if band_have[band][n] < targets[n]:
            return n
    return None


def write_corpus_report(path: str, run_id: str, engine_version: str,
                        args, cells: list, cell_state: dict, band_have: dict,
                        band_proc: dict, fam_proc: dict, fam_seg: dict,
                        targets: dict, stats: dict, con: sqlite3.Connection,
                        stopped_why: str) -> None:
    L: list[str] = []
    add = L.append
    add(f"# Corpus build — {run_id} ({stopped_why})")
    add("")
    add(f"Engine: {engine_version} | seed={args.seed} | workers={args.workers} | "
        f"nodes={args.nodes_stage1}/{args.nodes_stage2} | threshold={args.threshold}cp")
    add("Policy: Stage 2 on Stage 1 passes only, failures final, no fail-sample, "
        "no solver check, no convergence. Segments verified_level=1 (Stage 2 @ 500k).")
    add("forced_reply_count = internal single-legal replies (chess-sense forced); "
        "forced + engine_verified == solver_move_count - 1; "
        "non-trivial <=> engine_verified_replies > 0.")
    add("")
    add("## Targets vs achieved (distinct puzzles, non-trivial, per band)")
    add("| band | N>=3 | N>=4 | N>=5 | N>=6 |")
    add("|---|---|---|---|---|")
    for b in BAND_LABELS:
        cells_out = []
        for n in NS:
            have = band_have[b][n]
            tgt = targets[n]
            mark = "OK" if have >= tgt else ""
            cells_out.append(f"{have}/{tgt} {mark}".rstrip())
        add(f"| {b} | " + " | ".join(cells_out) + " |")
    add("")
    add("## Cells (band x depth x family), deepest first")
    add("| band | depth | family | processed | status |")
    add("|---|---|---|---|---|")
    for cell in cells:
        st = cell_state[cell]
        add(f"| {cell[0]} | {cell[1]} | {cell[2]} | {st['processed']} | "
            f"{st['status']} |")
    add("")
    exhausted = [c for c in cells if cell_state[c]["status"] == "exhausted"]
    add(f"Cells exhausted below target: "
        f"{['/'.join(c) for c in exhausted] if exhausted else 'none'}")
    add("")
    add("## Yield")
    add("| band | processed | >=3 | >=4 | >=5 | >=6 |")
    add("|---|---|---|---|---|---|")
    for b in BAND_LABELS:
        add(f"| {b} | {band_proc.get(b, 0)} | " +
            " | ".join(str(band_have[b][n]) for n in NS) + " |")
    add("")
    add("| family | processed | w/ non-trivial segment | rate |")
    add("|---|---|---|---|")
    for f in FAMILIES:
        p = fam_proc.get(f, 0)
        s = fam_seg.get(f, 0)
        add(f"| {f} | {p} | {s} | {s/p:.3f}" if p else f"| {f} | 0 | 0 | -")
    add("")
    add("## Throughput")
    wall = stats.get("wall_s", 0.0)
    n_puz = sum(band_proc.values())
    add(f"- wall: {wall:.1f}s, puzzles: {n_puz}, "
        f"rate: {n_puz/max(wall,1)*3600:.0f}/h")
    calls = con.execute("SELECT phase, nodes, ms FROM probe_calls WHERE"
                        " run_id=? ORDER BY t_end", (run_id,)).fetchall()
    by_ph: dict[str, list] = {}
    for ph, nn, mm in calls:
        by_ph.setdefault(ph, []).append((nn or 0, mm or 0))
    for ph in ("stage1", "stage2"):
        e = by_ph.get(ph, [])
        wn = sum(x[0] for x in e)
        wm = sum(x[1] for x in e)
        add(f"- {ph}: n={len(e)} wall-nps={wn/max(wm,1)*1000:.0f}")
    wins = []
    for k in range(0, len(calls), 100):
        w = calls[k:k + 100]
        wn = sum(x[1] or 0 for x in w)
        wm = sum(x[2] or 0 for x in w)
        if wm:
            wins.append(wn / wm * 1000.0)
    if len(wins) >= 2:
        add(f"- rolling wall-nps/100 calls: " +
            ", ".join(f"{x:.0f}" for x in wins))
        add(f"- drift: {100*(wins[-1]-wins[0])/max(wins[0],1):+.1f}%")
    add("")
    with open(path, "w") as fh:
        fh.write("\n".join(L))


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.stockfish is None:
        args.stockfish = probe_mod.default_stockfish()
    if not args.stockfish or not os.path.exists(args.stockfish):
        raise SystemExit(f"stockfish binary not found: {args.stockfish!r}")
    targets = {3: args.target_n3, 4: args.target_n4, 5: args.target_n5,
               6: args.target_n6}
    t_start = time.perf_counter()
    run_id = args.run_id or datetime.datetime.now(
        datetime.timezone.utc).strftime("corpus-%Y%m%d-%H%M%S")
    con = connect(args.out)
    tmp = probe_mod.ProbeEngine(args.stockfish, args.hash_mb)
    engine_version = tmp.name
    tmp.close()
    engine_sha256 = probe_mod.sha256_file(args.stockfish)
    settings = {"mode": "corpus", "nodes_stage1": args.nodes_stage1,
                "nodes_stage2": args.nodes_stage2, "threshold_cp": args.threshold,
                "verify_survivors": True, "fail_sample_frac": 0.0,
                "check_solver": False, "targets": targets, "chunk": args.chunk,
                "workers": args.workers, "threads": 1, "hash_mb": args.hash_mb,
                "stockfish_path": args.stockfish, "engine_sha256": engine_sha256,
                "wall_clock_backstop": None, "seed": args.seed}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    con.execute("INSERT OR IGNORE INTO probe_runs (run_id, started_at,"
                " engine_version, engine_sha256, settings_json, seed,"
                " git_commit) VALUES (?,?,?,?,?,?,?)",
                (run_id, now, engine_version, engine_sha256, dumps(settings),
                 args.seed, probe_mod.git_commit()))
    con.commit()

    # Verdict-logic args shared with the pilot's analyse_puzzle (fail-sample
    # and solver check disabled for production).
    vargs = SimpleNamespace(
        nodes_stage1=args.nodes_stage1, nodes_stage2=args.nodes_stage2,
        threshold=args.threshold, verify_survivors=True, fail_sample_frac=0.0,
        seed=args.seed, check_solver=False)

    (band_have, band_proc, fam_proc, fam_seg, stored_ids,
     cell_proc0) = read_counters(con, run_id)
    seen_ids = set(stored_ids)
    cell_state = {c: {"processed": cell_proc0.get(c, 0), "status": "pending"}
                  for c in CELLS}
    chunk_times: list[tuple[float, int]] = []  # (t_end, total_processed)
    total_done = len(stored_ids)
    last_progress = time.perf_counter()
    stopped_why = "all targets met"
    pool = EnginePool(args.stockfish, args.hash_mb)
    lock = threading.Lock()
    dumped = {}  # id(engine) -> call_log entries already persisted

    def dump_calls() -> None:
        for e in pool._all:
            have = dumped.get(id(e), 0)
            new = e.call_log[have:]
            if new:
                probe_mod.write_call_log(con, run_id, new)
                dumped[id(e)] = have + len(new)

    def work(cand):
        try:
            res = probe_mod.analyse_puzzle(vargs, pool, cand)
        except Exception as e:  # never let one puzzle kill the run
            line = cand["line"]
            res = {"cand": cand,
                   "puzzle_fen": probe_mod.puzzle_fen_after_setup(
                       cand["p"]["fen"], line["setup_move"]),
                   "plies": [], "solver_rows": [], "calls": 0,
                   "status": f"error: {type(e).__name__}: {e}"}
        seg_rows = (probe_mod.build_segment_rows(
            run_id, res, args.threshold, engine_version, args.nodes_stage1,
            args.nodes_stage2, now) if res["status"] == "done" else [])
        with lock:
            corpus_write_result(con, run_id, res, seg_rows)
        return res

    def progress(force=False):
        nonlocal last_progress
        if not force and time.perf_counter() - last_progress < 300:
            return
        last_progress = time.perf_counter()
        bh, bp, fp, fs, _, _ = read_counters(con, run_id)
        rate = None
        if len(chunk_times) >= 2:
            (t0, n0), (t1, n1) = chunk_times[0], chunk_times[-1]
            if t1 > t0 and n1 > n0:
                rate = (n1 - n0) / (t1 - t0) * 3600  # puzzles/hour
        # Remaining estimate per band from live yield, worst over N.
        need_tot = 0.0
        for b in BAND_LABELS:
            pb = bp.get(b, 0)
            worst = 0.0
            for n in NS:
                rem = max(0, targets[n] - bh[b][n])
                y = (bh[b][n] / pb) if pb else 0.0
                if rem and y > 0:
                    worst = max(worst, rem / y)
            need_tot += worst
        eta = f"{need_tot/max(rate,1e-9):.1f}h @ {rate:.0f}/h" if rate else "n/a"
        print(f"[corpus] {time.strftime('%H:%M:%S')} done={sum(bp.values())} "
              f"band>=3: " + " ".join(f"{b}={bh[b][3]}" for b in BAND_LABELS) +
              f" | ETA~{eta}", flush=True)
        for b in BAND_LABELS:
            if not band_done(bh, b, targets):
                print(f"   {b}: " + " ".join(
                    f">={n}:{bh[b][n]}/{targets[n]}" for n in NS), flush=True)

    def time_up() -> bool:
        return (args.max_hours is not None
                and (time.perf_counter() - t_start) > args.max_hours * 3600)

    if not args.db_url:
        raise SystemExit("corpus needs --db-url (read-only Postgres)")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for ci, cell in enumerate(CELLS):
            band, depth, fam = cell
            if band_done(band_have, band, targets):
                cell_state[cell]["status"] = "band-complete"
                continue
            mu = min_unmet(band_have, band, targets)
            if mu is not None and DEPTH_MAX[depth] < mu:
                # A depth-D cell can only yield segments of length <= D, so
                # it cannot contribute to any still-unmet target (>= mu).
                # Skipping bounds the run: without this, a band missing only
                # N>=6 would grind through its entire depth-3 pool (~100k+).
                cell_state[cell]["status"] = f"skipped (depth<{mu})"
                continue
            queue: list = []
            need = args.fetch0
            rnd = 0
            exhausted = False
            while True:
                if band_done(band_have, band, targets):
                    cell_state[cell]["status"] = "band-complete"
                    break
                if not queue and not exhausted:
                    raw, pool_done = probe_mod.fetch_db_candidates(
                        args.db_url, band, fam, need, depth)
                    if pool_done:
                        exhausted = True  # fully scanned: pool is finite
                    fresh = []
                    for p in raw:
                        if p["id"] in seen_ids:
                            continue
                        seen_ids.add(p["id"])
                        c = probe_mod.classify_candidate(p)
                        if (c is not None and c["band"] == band
                                and c["family"] == fam
                                and c["depth"] == depth):
                            fresh.append(c)
                    rng = random.Random(args.seed + ci * 1000 + rnd)
                    rng.shuffle(fresh)
                    queue.extend(fresh)
                    rnd += 1
                    need = min(need * 4, 60000)  # cap: huge cells need no more
                    if not queue and exhausted:
                        # Fully scanned and nothing (new) matches this cell.
                        cell_state[cell]["status"] = "exhausted"
                        break
                    continue
                if not queue and exhausted:
                    cell_state[cell]["status"] = "exhausted"
                    break
                chunk = queue[:args.chunk]
                queue = queue[args.chunk:]
                futs = [ex.submit(work, c) for c in chunk]
                for f in as_completed(futs):
                    f.result()
                for c in chunk:
                    stored_ids.add(c["p"]["id"])
                con.commit()  # chunk boundary
                dump_calls()
                con.commit()
                total_done += len(chunk)
                cell_state[cell]["processed"] += len(chunk)
                cell_state[cell]["status"] = "active"
                (band_have, band_proc, fam_proc, fam_seg, _,
                 _) = read_counters(con, run_id)
                chunk_times.append((time.perf_counter(),
                                    sum(band_proc.values())))
                chunk_times = chunk_times[-12:]
                progress()
                if time_up():
                    stopped_why = f"--max-hours {args.max_hours} reached"
                    cell_state[cell]["status"] += "+stopped"
                    break
            if time_up():
                break
            if all(band_done(band_have, b, targets) for b in BAND_LABELS):
                stopped_why = "all targets met"
                break

    pool.close_all()
    if stopped_why == "all targets met" and not all(
            band_done(band_have, b, targets) for b in BAND_LABELS):
        stopped_why = "no productive cells left (targets unmet — see shortfall)"
    wall_s = time.perf_counter() - t_start
    con.execute("UPDATE probe_runs SET wall_s=?, engine_phase_s=? WHERE run_id=?",
                (wall_s, wall_s, run_id))
    con.commit()
    (band_have, band_proc, fam_proc, fam_seg, _,
     _) = read_counters(con, run_id)
    report_path = args.report or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "report-corpus.md")
    write_corpus_report(report_path, run_id, engine_version, args, CELLS,
                        cell_state, band_have, band_proc, fam_proc, fam_seg,
                        targets, {"wall_s": wall_s}, con, stopped_why)
    print(f"[corpus] {stopped_why}; report written to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
