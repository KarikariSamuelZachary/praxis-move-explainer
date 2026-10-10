"""Offline survivor-count pilot for the Blind Calculation trainer.

Reads puzzles from a local dump (CSV) or a READ-ONLY Postgres connection,
analyses defender replies with own Stockfish processes (never the web app's
Review singleton), and writes everything to a NEW local SQLite file.

Usage:
    python3 probe.py --out pilot.sqlite --per-cell 2 --workers 4
    python3 probe.py --out pilot.sqlite --source csv --csv ../../praxis_subset.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chess

from puzzle_line import (BANDS, band_bounds, band_of, build_line, depth_bucket,
                         family_of, line_positions, puzzle_fen_after_setup)
from verdicts import (build_segments, classify_solver_move, judge_stage,
                      segment_line_uci, wdl_margin)
from engine_probe import EnginePool, ProbeEngine
from storage import connect, dumps
import report as report_mod

DEFAULT_NODES_STAGE1 = 50_000
DEFAULT_NODES_STAGE2 = 500_000
DEFAULT_THRESHOLD_CP = 150
DEFAULT_SEED = 20261008
DEPTH_BUCKETS = ["3", "4", "5", "6+"]


def default_stockfish() -> str:
    for cand in (os.getenv("STOCKFISH_PATH") or "", "/opt/stockfish/stockfish",
                 "/usr/games/stockfish"):
        if cand and os.path.exists(cand):
            return cand
    return "stockfish"


def sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def in_fail_sample(seed: int, puzzle_id: str, reply_index: int,
                   frac: float) -> bool:
    """Deterministic seeded 10%-of-failures escalation sample."""
    if frac <= 0:
        return False
    n = max(1, round(100 * frac))
    return zlib.crc32(f"{seed}:fail:{puzzle_id}:{reply_index}".encode()) % 100 < n


def in_solver_subsample(seed: int, puzzle_id: str, frac: float) -> bool:
    if frac <= 0:
        return False
    n = max(1, round(100 * frac))
    return zlib.crc32(f"{seed}:solver:{puzzle_id}".encode()) % 100 < n


def git_commit() -> str | None:
    try:
        repo = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        out = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Puzzle sources (read-only)
# ---------------------------------------------------------------------------

def parse_themes_cell(cell: str) -> list[str]:
    cell = (cell or "").strip()
    if cell.startswith("{") and cell.endswith("}"):  # PG array literal
        return [t for t in cell[1:-1].split(",") if t]
    return cell.split()


def iter_csv(path: str):
    import zstandard  # type: ignore
    if path.endswith(".zst"):
        fh = zstandard.ZstdDecompressor().stream_reader(open(path, "rb"))
        import io
        text = io.TextIOWrapper(fh)
    else:
        text = open(path, newline="")
    with text:
        for row in csv.DictReader(text):
            moves_col = row.get("Moves") or row.get("moves") or ""
            themes_col = row.get("Themes") or row.get("themes") or ""
            yield {
                "id": row.get("PuzzleId") or row.get("id"),
                "fen": row.get("FEN") or row.get("fen"),
                "moves": moves_col.split(),
                "rating": int(row.get("Rating") or row.get("rating")),
                "themes": parse_themes_cell(themes_col),
                "popularity": (int(row["Popularity"]) if row.get("Popularity")
                               not in (None, "") else None),
                "nb_plays": (int(row["NbPlays"]) if row.get("NbPlays")
                             not in (None, "") else None),
            }


CELL_FILTERS = {
    "mate": "EXISTS (SELECT 1 FROM unnest(themes) t WHERE t ~ '^mateIn[0-9]+$'"
            " AND substring(t FROM 7)::int >= 2)",
    "quiet": "themes @> ARRAY['quietMove']",
    "endgame": "themes @> ARRAY['endgame']",
    "forcing": "themes && ARRAY['sacrifice','deflection','attraction',"
               "'discoveredAttack','doubleCheck','fork','pin','skewer',"
               "'interference','clearance']",
}


def fetch_db_candidates(dsn: str, band: str, family: str, need: int,
                        depth: str | None = None) -> tuple[list[dict], bool]:
    """Candidates for one (band x family [x depth]) cell, deterministic (id).

    Returns (rows, pool_exhausted): pool_exhausted True means the sample_key
    cutoff reached 1.0, i.e. the pool is finite and fully scanned.
    Adaptive sample_key cutoff instead of ORDER BY sample_key LIMIT.
    """
    import psycopg2  # read-only usage only
    if family not in CELL_FILTERS:
        raise ValueError(family)
    lo, hi = band_bounds(band)
    # All Lichess rows have even Moves counts, so solver-move count S = M/2
    # exactly (verified: 0 odd-length rows in 5.88M).
    conds = ["rating >= %s", CELL_FILTERS[family],
             "array_length(string_to_array(moves, ' '), 1) >= 6"]
    params_base_late: list = []
    if depth == "6+":
        conds.append("array_length(string_to_array(moves, ' '), 1) / 2 >= 6")
    elif depth in ("3", "4", "5"):
        conds.append("array_length(string_to_array(moves, ' '), 1) / 2 = %s")
        params_base_late = [int(depth)]
    else:
        params_base_late = []
        if depth is not None:
            raise ValueError(depth)
    params_base: list = [lo] + params_base_late
    if hi is not None:
        conds.append("rating < %s")
        params_base.append(hi)
    where = " AND ".join(conds) + " AND sample_key < %s"
    sql = ("SELECT id, fen, moves, rating, themes FROM puzzles WHERE "
           + where + " ORDER BY id")
    cutoff = 0.002
    rows: list = []
    pool_exhausted = False
    con = psycopg2.connect(dsn)
    try:
        con.set_session(readonly=True)
        while True:
            with con.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql, params_base + [cutoff])
                rows = cur.fetchall()
            if len(rows) >= need or cutoff >= 1.0:
                pool_exhausted = cutoff >= 1.0 and len(rows) < need
                break
            cutoff = min(1.0, cutoff * 4)
    finally:
        con.close()
    return ([{"id": r[0], "fen": r[1], "moves": r[2].split(), "rating": r[3],
              "themes": list(r[4]), "popularity": None, "nb_plays": None}
             for r in rows], pool_exhausted)


def write_call_log(con: sqlite3.Connection, run_id: str,
                   call_log: list[dict]) -> None:
    for c in call_log:
        con.execute("INSERT INTO probe_calls (run_id, phase, nodes, ms,"
                    " reported_nps, t_end) VALUES (?,?,?,?,?,?)",
                    (run_id, c.get("phase"), c.get("nodes"), c.get("ms"),
                     c.get("reported_nps"), c.get("t_end")))
    con.commit()


def load_strata_populations(sqlite_path: str) -> list[dict]:
    """Copy stratum populations from an earlier run's SQLite file instead of
    recomputing COUNT queries (grouped, so resume-duplicated rows can't
    double-count)."""
    src = sqlite3.connect(sqlite_path)
    try:
        rows = src.execute("SELECT band, family, depth_bucket,"
                           " MAX(population_count) FROM probe_strata"
                           " GROUP BY band, family, depth_bucket").fetchall()
    finally:
        src.close()
    return [{"band": b, "family": f, "depth": d, "n": n}
            for b, f, d, n in rows]


def write_strata(con: sqlite3.Connection, run_id: str,
                 populations: list[dict], sampled: list[dict]) -> None:
    have: dict[tuple[str, str, str], int] = {}
    for s in sampled:
        key = (s["band"], s["family"], s["depth"])
        have[key] = have.get(key, 0) + 1
    seen = set()
    for c in populations:
        key = (c["band"], c["family"], c["depth"])
        seen.add(key)
        con.execute("INSERT INTO probe_strata (run_id, band, family,"
                    " depth_bucket, population_count, sampled_count)"
                    " VALUES (?,?,?,?,?,?)",
                    (run_id, c["band"], c["family"], c["depth"], c["n"],
                     have.get(key, 0)))
    for lo, hi in BANDS:
        label = f"{lo}+" if hi is None else f"{lo}-{hi}"
        for fam in ("mate", "quiet", "endgame", "forcing"):
            for d in DEPTH_BUCKETS:
                if (label, fam, d) not in seen:
                    con.execute("INSERT INTO probe_strata (run_id, band,"
                                " family, depth_bucket, population_count,"
                                " sampled_count) VALUES (?,?,?,?,?,?)",
                                (run_id, label, fam, d, 0, 0))
    con.commit()


def db_population_counts(dsn: str) -> list[dict]:
    import psycopg2
    sql = """
    SELECT band, family, depth, COUNT(*) FROM (
      SELECT
        CASE WHEN rating < 1400 THEN '1200-1400'
             WHEN rating < 1600 THEN '1400-1600'
             WHEN rating < 1800 THEN '1600-1800'
             WHEN rating < 2000 THEN '1800-2000'
             WHEN rating < 2200 THEN '2000-2200'
             ELSE '2200+' END AS band,
        CASE WHEN EXISTS (SELECT 1 FROM unnest(themes) t
                          WHERE t ~ '^mateIn[0-9]+$'
                          AND substring(t FROM 7)::int >= 2) THEN 'mate'
             WHEN themes @> ARRAY['quietMove'] THEN 'quiet'
             WHEN themes @> ARRAY['endgame'] THEN 'endgame'
             WHEN themes && ARRAY['sacrifice','deflection','attraction',
               'discoveredAttack','doubleCheck','fork','pin','skewer',
               'interference','clearance'] THEN 'forcing'
             ELSE 'other' END AS family,
        CASE WHEN array_length(string_to_array(moves,' '),1)/2 = 3 THEN '3'
             WHEN array_length(string_to_array(moves,' '),1)/2 = 4 THEN '4'
             WHEN array_length(string_to_array(moves,' '),1)/2 = 5 THEN '5'
             ELSE '6+' END AS depth
        FROM puzzles WHERE rating >= 1200
          AND array_length(string_to_array(moves, ' '), 1) >= 6
    ) s GROUP BY band, family, depth
    """
    con = psycopg2.connect(dsn)
    try:
        con.set_session(readonly=True)
        with con.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        con.close()
    return [{"band": r[0], "family": r[1], "depth": r[2], "n": r[3]}
            for r in rows]


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def classify_candidate(p: dict) -> dict | None:
    try:
        line = build_line(p["fen"], p["moves"])
    except ValueError:
        return None
    if line["solver_move_count"] < 3:
        return None
    band = band_of(p["rating"])
    fam = family_of(p["themes"])
    if band is None or fam is None:
        return None
    return {"p": p, "line": line, "band": band, "family": fam,
            "depth": depth_bucket(line["solver_move_count"])}


def sample_cells(candidates: list[dict], per_cell: int,
                 seed: int) -> tuple[list[dict], dict]:
    """Group by (band, family, depth), round-robin across depths per cell."""
    rng = random.Random(seed)
    grid: dict[tuple[str, str, str], list[dict]] = {}
    for c in candidates:
        grid.setdefault((c["band"], c["family"], c["depth"]), []).append(c)
    for lst in grid.values():
        rng.shuffle(lst)
    picked: list[dict] = []
    counts: dict[tuple[str, str, str], int] = {}
    for lo, hi in BANDS:
        label = f"{lo}+" if hi is None else f"{lo}-{hi}"
        for fam in ("mate", "quiet", "endgame", "forcing"):
            buckets = [grid.get((label, fam, d), []) for d in DEPTH_BUCKETS]
            idx = [0, 0, 0, 0]
            made = 0
            while made < per_cell:
                progressed = False
                for b, lst in enumerate(buckets):
                    if made >= per_cell:
                        break
                    if idx[b] < len(lst):
                        picked.append(lst[idx[b]])
                        idx[b] += 1
                        made += 1
                        progressed = True
                if not progressed:
                    break
            for b, d in enumerate(DEPTH_BUCKETS):
                counts[(label, fam, d)] = idx[b]
    return picked, counts


# ---------------------------------------------------------------------------
# Per-puzzle analysis
# ---------------------------------------------------------------------------

def analyse_puzzle(args, pool: EnginePool, cand: dict) -> dict:
    p, line = cand["p"], cand["line"]
    pf = puzzle_fen_after_setup(p["fen"], line["setup_move"])
    solver_fens, defender_fens = line_positions(
        pf, line["solver_moves"], line["defender_moves"])
    calls = 0
    plies: list[dict] = []
    for i, (fen_b, stored) in enumerate(
            zip(defender_fens, line["defender_moves"])):
        board = chess.Board(fen_b)
        legal = list(board.legal_moves)
        if len(legal) == 1:
            plies.append({"i": i, "fen_before": fen_b, "stored": stored,
                          "single_legal": 1, "stage1": None, "stage2": None,
                          "flipped": None, "fail_sample": 0,
                          "verdict": "clean",
                          "margin": None, "mate": 0,
                          "best_mate": None, "second_mate": None,
                          "wdl": None})
            continue
        eng = pool.get()
        s1_pvs, _ = eng.analyse_stage1(fen_b, args.nodes_stage1)
        calls += 1
        j1 = judge_stage(stored, s1_pvs, args.threshold)
        stage1 = {"stored": stored, "pvs": s1_pvs, "judgement": j1}
        # Escalation policy (pilot): Stage 1 failures are FINAL. Stage 2 runs
        # on Stage 1 passes (verification) plus a seeded random 10% of Stage 1
        # failures (rescue-rate sample; the Stage 2 verdict is recorded but
        # does not change the final fail).
        fail_sample = 0
        if j1["verdict"] == "clean":
            run_s2 = args.verify_survivors
        else:
            run_s2 = in_fail_sample(args.seed, p["id"], i,
                                     args.fail_sample_frac)
            fail_sample = 1 if run_s2 else 0
        s2_pvs = None
        flipped = None
        if run_s2:
            s2_pvs, _ = eng.analyse_stage2(fen_b, args.nodes_stage2)
            calls += 1
            j2 = judge_stage(stored, s2_pvs, args.threshold)
            flipped = 1 if (j1["verdict"] != j2["verdict"]) else 0
            if j1["verdict"] == "clean":
                final, margin, mate = (j2["verdict"], j2["margin_cp"],
                                       j2["mate_involved"])
            else:
                final, margin, mate = (j1["verdict"], j1["margin_cp"],
                                       j1["mate_involved"])
            deciding = s2_pvs if j1["verdict"] == "clean" else s1_pvs
        else:
            final, margin, mate = (j1["verdict"], j1["margin_cp"],
                                   j1["mate_involved"])
            deciding = s1_pvs
        stage2 = ({"stored": stored, "pvs": s2_pvs,
                   "judgement": judge_stage(stored, s2_pvs, args.threshold)}
                  if s2_pvs is not None else None)
        plies.append({"i": i, "fen_before": fen_b, "stored": stored,
                      "single_legal": 0, "stage1": stage1, "stage2": stage2,
                      "flipped": flipped, "fail_sample": fail_sample,
                      "verdict": final, "margin": margin,
                      "mate": mate,
                      "best_mate": deciding[0].get("mate"),
                      "second_mate": (deciding[1].get("mate")
                                      if len(deciding) > 1 else None),
                      "wdl": wdl_margin(deciding)})
    solver_rows: list[dict] = []
    if args.check_solver and cand.get("solver_check"):
        eng = pool.get()
        for i, (fen_b, stored) in enumerate(
                zip(solver_fens, line["solver_moves"])):
            pvs, _ = eng.analyse_solver(fen_b, args.nodes_stage1)
            calls += 1
            best, second = pvs[0], (pvs[1] if len(pvs) > 1 else None)
            is_best = 1 if stored == best["move"] else 0
            margin = None
            if (best["cp"] is not None and second is not None
                    and second["cp"] is not None):
                margin = best["cp"] - second["cp"]
            alt = 0
            if (not is_best and second is not None
                    and stored == second["move"]
                    and best["mate"] is not None and best["mate"] > 0
                    and second["mate"] == best["mate"]):
                alt = 1
            solver_rows.append({"i": i, "fen_before": fen_b, "stored": stored,
                                "is_best": is_best, "margin": margin,
                                "alt_mate": alt})
    return {"cand": cand, "puzzle_fen": pf, "plies": plies,
            "solver_rows": solver_rows, "calls": calls, "status": "done"}


def build_segment_rows(run_id: str, res: dict, threshold: int,
                       engine_version: str, n1: int, n2: int,
                       now: str) -> list[dict]:
    cand = res["cand"]
    line = cand["line"]
    flags = [pl["verdict"] == "clean" for pl in res["plies"]]
    segs = build_segments(line["solver_moves"], line["defender_moves"], flags)
    solver_fens, _ = line_positions(res["puzzle_fen"], line["solver_moves"],
                                    line["defender_moves"])
    rows: list[dict] = []
    for s in segs:
        a, b = s["start"], s["end"]
        checks = captures = quiet = promos = 0
        for i in range(a, b + 1):
            c = classify_solver_move(solver_fens[i], line["solver_moves"][i])
            checks += c["check"]
            captures += c["capture"]
            quiet += c["quiet"]
            promos += c["promotion"]
        n_moves = b - a + 1
        margins = [res["plies"][i]["margin"] for i in range(a, b)
                   if res["plies"][i]["margin"] is not None]
        wdls = [res["plies"][i]["wdl"] for i in range(a, b)
                if res["plies"][i]["wdl"] is not None]
        mate = 1 if any(res["plies"][i]["mate"] for i in range(a, b)) else 0
        verified = sum(1 for i in range(a, b)
                       if not res["plies"][i]["single_legal"])
        # Chess-sense "forced replies": internal defender replies with exactly
        # one legal move. forced + verified == solver_move_count - 1 always
        # (every internal reply of a clean segment is clean by construction).
        forced = sum(1 for i in range(a, b)
                     if res["plies"][i]["single_legal"])
        rows.append({
            "puzzle_id": cand["p"]["id"], "segment_fen": solver_fens[a],
            "line_uci": segment_line_uci(line["solver_moves"],
                                         line["defender_moves"], a, b),
            "start": a, "n": n_moves,
            "min_cp": min(margins) if margins else None,
            "min_wdl": min(wdls) if wdls else None,
            "mate": mate, "checks": checks, "captures": captures,
            "quiet": quiet, "promos": promos,
            "verified": verified, "forced": forced,
            "trivial": 1 if verified == 0 else 0,
            "quiet_share": quiet / n_moves,
            "rating": cand["p"]["rating"],
            "themes": " ".join(cand["p"]["themes"]),
        })
    for r in rows:
        r.update({"run_id": run_id, "engine_version": engine_version,
                  "n1": n1, "n2": n2, "created_at": now})
    return rows


def write_result(con: sqlite3.Connection, run_id: str, res: dict,
                 seg_rows: list[dict]) -> None:
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
         dumps(res["solver_rows"]), res["status"], res["calls"]))
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
             pl["fail_sample"], pl["verdict"], pl["margin"], pl["best_mate"],
             pl["second_mate"], pl["mate"], pl["wdl"]))
    for sr in res["solver_rows"]:
        con.execute(
            "INSERT OR IGNORE INTO probe_solver_plies (run_id, puzzle_id,"
            " solver_index, fen_before, stored_move, is_best, margin_cp,"
            " alt_mate) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, p["id"], sr["i"], sr["fen_before"], sr["stored"],
             sr["is_best"], sr["margin"], sr["alt_mate"]))
    for r in seg_rows:
        con.execute(
            "INSERT INTO clean_forced_segments (run_id, puzzle_id, segment_fen,"
            " line_uci, start_solver_index, solver_move_count, min_cp_margin,"
            " min_wdl_margin, mate_involved, checks, captures, quiet,"
            " promotions, engine_verified_replies, trivially_forced,"
            " quiet_share, rating, themes, engine_version,"
            " nodes_stage1, nodes_stage2, multipv_stage1, multipv_stage2,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["run_id"], r["puzzle_id"], r["segment_fen"], r["line_uci"],
             r["start"], r["n"], r["min_cp"], r["min_wdl"], r["mate"],
             r["checks"], r["captures"], r["quiet"], r["promos"],
             r["verified"], r["trivial"],
             r["quiet_share"], r["rating"], r["themes"], r["engine_version"],
             r["n1"], r["n2"], 2, 4, r["created_at"]))
    con.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Blind-calculation survivor probe")
    ap.add_argument("--out", required=True, help="output SQLite file")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--source", choices=["auto", "db", "csv"], default="auto")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--db-url", default=os.getenv("DATABASE_URL"))
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--per-cell", type=int, default=10)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--nodes-stage1", type=int, default=DEFAULT_NODES_STAGE1)
    ap.add_argument("--nodes-stage2", type=int, default=DEFAULT_NODES_STAGE2)
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD_CP)
    ap.add_argument("--stockfish", default=None)
    ap.add_argument("--hash-mb", type=int, default=16,
                    help="Review's Hash setting (item 1: 64 caused a "
                         "first-call stall; 16 is Review's value)")
    ap.add_argument("--verify-survivors", dest="verify_survivors",
                    action="store_true", default=True)
    ap.add_argument("--no-verify-survivors", dest="verify_survivors",
                    action="store_false")
    ap.add_argument("--fail-sample-frac", type=float, default=0.10,
                    help="seeded fraction of Stage 1 failures to escalate "
                         "to Stage 2 (rescue-rate sample; failures stay final)")
    ap.add_argument("--check-solver", dest="check_solver",
                    action="store_true", default=False)
    ap.add_argument("--no-check-solver", dest="check_solver",
                    action="store_false")
    ap.add_argument("--solver-subsample", type=float, default=0.05,
                    help="seeded fraction of sampled puzzles getting the "
                         "solver check (only when --check-solver)")
    ap.add_argument("--conv-plies", type=int, default=100,
                    help="Stage-2-clean plies to rerun for convergence")
    ap.add_argument("--conv-nodes", type=int, default=2_000_000)
    ap.add_argument("--no-convergence", dest="convergence",
                    action="store_false", default=True)
    ap.add_argument("--report", default=None, help="report.md output path")
    ap.add_argument("--copy-strata-from", default=None,
                    help="SQLite file to copy probe_strata populations from "
                         "instead of recomputing COUNTs")
    ap.add_argument("--need-per-cell", type=int, default=None,
                    help="DB candidates to classify per cell (default: 40x per-cell)")
    ap.add_argument("--max-puzzles", type=int, default=None,
                    help="cap total sampled puzzles (dry runs)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.stockfish is None:
        args.stockfish = default_stockfish()
    t_start = time.perf_counter()
    run_id = args.run_id or datetime.datetime.now(
        datetime.timezone.utc).strftime("probe-%Y%m%d-%H%M%S")
    con = connect(args.out)

    # Engine version probe (own throwaway process).
    if not args.stockfish or not os.path.exists(args.stockfish):
        raise SystemExit(f"stockfish binary not found: {args.stockfish!r} "
                         "(pass --stockfish explicitly)")
    tmp = ProbeEngine(args.stockfish, args.hash_mb)
    engine_version = tmp.name
    tmp.close()
    engine_sha256 = sha256_file(args.stockfish)

    settings = {"nodes_stage1": args.nodes_stage1,
                "nodes_stage2": args.nodes_stage2,
                "multipv_stage1": 2, "multipv_stage2": 4,
                "threshold_cp": args.threshold,
                "verify_survivors": args.verify_survivors,
                "fail_sample_frac": args.fail_sample_frac,
                "escalation": "stage2 on stage1 passes only; "
                              "stage1 failures final + seeded fail sample",
                "check_solver": args.check_solver,
                "solver_subsample": args.solver_subsample,
                "conv_plies": args.conv_plies, "conv_nodes": args.conv_nodes,
                "per_cell": args.per_cell, "workers": args.workers,
                "threads": 1, "hash_mb": args.hash_mb,
                "stockfish_path": args.stockfish,
                "engine_sha256": engine_sha256,
                "wall_clock_backstop": None,
                "source": args.source}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    con.execute("INSERT OR IGNORE INTO probe_runs (run_id, started_at,"
                " engine_version, engine_sha256, settings_json, seed,"
                " git_commit) VALUES (?,?,?,?,?,?,?)",
                (run_id, now, engine_version, engine_sha256, dumps(settings),
                 args.seed, git_commit()))
    con.commit()

    use_db = args.source in ("db", "auto") and args.db_url
    if args.source == "auto" and args.db_url:
        try:
            import psycopg2
            psycopg2.connect(args.db_url).close()
        except Exception as e:
            print(f"[probe] DB unreachable ({e}); falling back to CSV")
            use_db = False
    pop_counts: list[dict] = []
    candidates: list[dict] = []
    con.execute("DELETE FROM probe_strata WHERE run_id=?", (run_id,))
    con.commit()
    if use_db:
        print("[probe] source=postgres (read-only)")
        if args.copy_strata_from:
            pop_counts = load_strata_populations(args.copy_strata_from)
            print(f"[probe] copied {len(pop_counts)} stratum populations from "
                  f"{args.copy_strata_from} (no COUNT queries)")
        else:
            pop_counts = db_population_counts(args.db_url)
        need = args.need_per_cell or max(80, args.per_cell * 40)
        for lo, hi in BANDS:
            label = f"{lo}+" if hi is None else f"{lo}-{hi}"
            for fam in ("mate", "quiet", "endgame", "forcing"):
                raw, _ = fetch_db_candidates(args.db_url, label, fam, need)
                for p in raw:
                    c = classify_candidate(p)
                    if c is not None and c["band"] == label \
                            and c["family"] == fam:
                        candidates.append(c)
        sampled, sample_counts = sample_cells(candidates, args.per_cell,
                                              args.seed)
    else:
        csv_path = args.csv or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "praxis_subset.csv")
        print(f"[probe] source=csv {csv_path}")
        for p in iter_csv(csv_path):
            c = classify_candidate(p)
            if c is not None:
                candidates.append(c)
        grid: dict[tuple[str, str, str], int] = {}
        for c in candidates:
            grid[(c["band"], c["family"], c["depth"])] = \
                grid.get((c["band"], c["family"], c["depth"]), 0) + 1
        sampled, _ = sample_cells(candidates, args.per_cell, args.seed)
        pop_counts = [{"band": b, "family": f, "depth": d, "n": n}
                      for (b, f, d), n in grid.items()]
    write_strata(con, run_id, pop_counts, sampled)

    print(f"[probe] run={run_id} engine={engine_version} "
          f"candidates={len(candidates)} sampled={len(sampled)}")
    if args.max_puzzles is not None:
        sampled = sampled[:args.max_puzzles]
        print(f"[probe] capped to {len(sampled)} puzzles (--max-puzzles)")
    for s in sampled:
        s["solver_check"] = in_solver_subsample(args.seed, s["p"]["id"],
                                                args.solver_subsample)
    n_solver_checked = sum(1 for s in sampled if s["solver_check"])
    print(f"[probe] solver-check subsample: {n_solver_checked}/{len(sampled)} "
          f"puzzles (frac={args.solver_subsample})")

    done = {r[0] for r in con.execute(
        "SELECT puzzle_id FROM probe_puzzles WHERE run_id=?", (run_id,))}
    todo = [s for s in sampled if s["p"]["id"] not in done]
    print(f"[probe] resuming: {len(done)} already done, {len(todo)} to do")

    pool = EnginePool(args.stockfish, args.hash_mb)
    results: list[dict] = []
    lock = threading.Lock()
    db_write_s = [0.0]  # mutable accumulator (main-thread + workers share)

    def work(cand):
        try:
            res = analyse_puzzle(args, pool, cand)
        except Exception as e:  # never let one puzzle kill the run
            line = cand["line"]
            res = {"cand": cand,
                   "puzzle_fen": puzzle_fen_after_setup(
                       cand["p"]["fen"], line["setup_move"]),
                   "plies": [], "solver_rows": [], "calls": 0,
                   "status": f"error: {type(e).__name__}: {e}"}
        seg_rows = (build_segment_rows(run_id, res, args.threshold,
                                       engine_version, args.nodes_stage1,
                                       args.nodes_stage2, now)
                    if res["status"] == "done" else [])
        t_wr = time.perf_counter()
        with lock:
            write_result(con, run_id, res, seg_rows)
        db_write_s[0] += time.perf_counter() - t_wr
        return res

    t_engine0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            results.append(res)
            if i % 10 == 0 or i == len(todo):
                print(f"[probe] {i}/{len(todo)} puzzles done", flush=True)
    engine_phase_s = time.perf_counter() - t_engine0
    write_call_log(con, run_id,
                   [c for e in pool._all for c in e.call_log])

    # Convergence pass (item 8): rerun Stage-2-clean plies at conv_nodes.
    # Fresh engine pool: the main-phase engines are idle from here on, so
    # close them first (halves peak Stockfish RSS on this 3GB box; the pilot
    # died here with wedged spawns under memory/CPU pressure).
    pool.close_all()
    conv_pool = EnginePool(args.stockfish, args.hash_mb)
    conv_done = {tuple(r) for r in con.execute(
        "SELECT puzzle_id, reply_index FROM probe_convergence WHERE run_id=?",
        (run_id,))}
    if args.convergence and args.conv_plies > 0:
        eligible = [r for r in con.execute(
            "SELECT puzzle_id, reply_index, fen_before, stored_move"
            " FROM probe_defender_plies WHERE run_id=? AND stage2_json IS NOT"
            " NULL AND final_verdict='clean'", (run_id,))]
        eligible = [r for r in eligible if (r[0], r[1]) not in conv_done]
        rng = random.Random(args.seed + 1)
        rng.shuffle(eligible)
        # Top-up semantics: run only enough NEW targets to reach conv_plies
        # total stored rows (a resumed run must not double the sample).
        n_new = max(0, args.conv_plies - len(conv_done))
        conv_targets = eligible[:n_new]
        print(f"[probe] convergence: {len(conv_targets)} plies to rerun at "
              f"{args.conv_nodes} nodes ({len(eligible)} eligible)")

        def conv_work(target):
            pid, idx, fen_b, stored = target
            eng = conv_pool.get()
            pvs, _ = eng.analyse_stage2(fen_b, args.conv_nodes, phase="conv")
            j = judge_stage(stored, pvs, args.threshold)
            verdict = j["verdict"]
            row = (run_id, pid, idx, fen_b, stored, args.conv_nodes, verdict,
                   1 if verdict == "fail" else 0)
            t_wr = time.perf_counter()
            with lock:
                con.execute("INSERT OR IGNORE INTO probe_convergence (run_id,"
                            " puzzle_id, reply_index, fen_before, stored_move,"
                            " nodes, verdict, flipped_to_fail)"
                            " VALUES (?,?,?,?,?,?,?,?)", row)
                con.commit()
            db_write_s[0] += time.perf_counter() - t_wr
            return verdict

        t_conv0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(conv_work, t) for t in conv_targets]
            for i, fut in enumerate(as_completed(futs), 1):
                fut.result()
                if i % 10 == 0 or i == len(conv_targets):
                    print(f"[probe] convergence {i}/{len(conv_targets)}",
                          flush=True)
        conv_phase_s = time.perf_counter() - t_conv0
    else:
        conv_phase_s = 0.0

    wall_s = time.perf_counter() - t_start
    conv_pool.close_all()
    write_call_log(con, run_id,
                   [c for e in conv_pool._all for c in e.call_log])
    con.execute("UPDATE probe_runs SET wall_s=?, sampling_s=?,"
                " engine_phase_s=?, conv_phase_s=? WHERE run_id=?",
                (wall_s, t_engine0 - t_start, engine_phase_s, conv_phase_s,
                 run_id))
    con.commit()

    call_log = [c for e in pool._all for c in e.call_log]
    call_log += [c for e in conv_pool._all for c in e.call_log]
    sampling_s = t_engine0 - t_start  # fetch + classify + sample + strata
    stats = {
        "wall_s": wall_s,
        "sampling_s": sampling_s,
        "engine_phase_s": engine_phase_s,
        "conv_phase_s": conv_phase_s,
        "db_write_s": db_write_s[0],
        "stage1_ms": [m for e in pool._all for m in e.stage1_ms],
        "stage2_ms": [m for e in pool._all for m in e.stage2_ms],
        "solver_ms": [m for e in pool._all for m in e.solver_ms],
        "call_log": call_log,
        "workers": args.workers,
        "cpu_count": os.cpu_count(),
    }
    report_path = args.report or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "report.md")
    report_mod.write_report(con, run_id, args, engine_version, stats,
                            report_path)
    print(f"[probe] report written to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
