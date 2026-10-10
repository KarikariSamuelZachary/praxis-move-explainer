"""Tests for the calc probe. Runnable without pytest:
    python3 test_calc_probe.py [--stockfish PATH] [--db-url URL]
DB-dependent test (setup-move hand-verification) is skipped without --db-url.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chess

from puzzle_line import build_line, line_positions, puzzle_fen_after_setup
from verdicts import build_segments, judge_stage
from engine_probe import ProbeEngine

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + f" {name}" + (f" — {detail}" if detail and not cond else ""))


def pv(move, cp=None, mate=None):
    return {"move": move, "cp": cp, "mate": mate,
            "wdl_w": None, "wdl_d": None, "wdl_l": None}


def test_segments_synthetic():
    solver = ["s0", "s1", "s2", "s3", "s4"]
    defender = ["d0", "d1", "d2", "d3"]
    segs = build_segments(solver, defender, [True, False, True, True])
    check("segments: exactly two", len(segs) == 2, f"{segs}")
    if len(segs) == 2:
        check("segments: [s0,s1]", segs[0] == {"start": 0, "end": 1}, f"{segs[0]}")
        check("segments: [s2,s3,s4]", segs[1] == {"start": 2, "end": 4}, f"{segs[1]}")
    check("segments: all-fail gives none",
          build_segments(solver, defender, [False] * 4) == [])
    check("segments: lone clean gives length-2 tail",
          build_segments(solver, defender, [False, False, False, True])
          == [{"start": 3, "end": 4}])


def test_mate_margins():
    # Tie mates fail. Best -5 vs second -5 (same distance): fail.
    j = judge_stage("m1", [pv("m1", mate=-5), pv("m2", mate=-5)], 150)
    check("mate tie fails", j["verdict"] == "fail", f"{j}")
    # Strictly shorter alternative mate passes (stored resists longest).
    j = judge_stage("m1", [pv("m1", mate=-5), pv("m2", mate=-3)], 150)
    check("unique longest resistance passes",
          j["verdict"] == "clean" and j["mate_involved"] == 1, f"{j}")
    # Alternative mates LONGER (less resistance) than stored: fail.
    j = judge_stage("m1", [pv("m1", mate=-3), pv("m2", mate=-5)], 150)
    check("shorter stored mate fails", j["verdict"] == "fail", f"{j}")
    # Non-mate second line next to a mated best: fail.
    j = judge_stage("m1", [pv("m1", mate=-4), pv("m2", cp=100)], 150)
    check("mate best without mate second fails", j["verdict"] == "fail", f"{j}")
    # Mate FOR the defender: fail.
    j = judge_stage("m1", [pv("m1", mate=3), pv("m2", cp=200)], 150)
    check("defender mates fails",
          j["verdict"] == "fail" and j["reason"] == "defender_mates", f"{j}")
    # Stored not best: disagree.
    j = judge_stage("m2", [pv("m1", cp=50), pv("m2", cp=0)], 150)
    check("disagree fails",
          j["verdict"] == "fail" and j["reason"] == "disagree", f"{j}")
    # Plain margins.
    j = judge_stage("m1", [pv("m1", cp=200), pv("m2", cp=0)], 150)
    check("margin>=threshold clean",
          j["verdict"] == "clean" and j["margin_cp"] == 200, f"{j}")
    j = judge_stage("m1", [pv("m1", cp=100), pv("m2", cp=0)], 150)
    check("margin<threshold fails", j["verdict"] == "fail", f"{j}")
    # Second mates against defender while best does not: clean.
    j = judge_stage("m1", [pv("m1", cp=50), pv("m2", mate=-2)], 150)
    check("second-mates-against clean", j["verdict"] == "clean", f"{j}")


def test_setup_move_db(db_url):
    import psycopg2
    con = psycopg2.connect(db_url)
    con.set_session(readonly=True)
    with con.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SELECT id, fen, moves FROM puzzles ORDER BY sample_key"
                    " LIMIT 3")
        rows = cur.fetchall()
    con.close()
    check("setup: got 3 puzzles", len(rows) == 3, f"got {len(rows)}")
    for pid, fen, moves in rows:
        toks = moves.split()
        try:
            line = build_line(fen, toks)
            pf = puzzle_fen_after_setup(fen, line["setup_move"])
            board = chess.Board(pf)
            legal = (chess.Move.from_uci(line["solver_moves"][0])
                     in board.legal_moves)
            turn_flipped = board.turn != chess.Board(fen).turn
            check(f"setup: first solver move legal ({pid})", legal,
                  f"{pid} {line['solver_moves'][0]} in {pf}")
            check(f"setup: turn flipped after setup ({pid})", turn_flipped,
                  f"{pid}")
            # Full line replays without illegality (build_line already pushed
            # every move; reaching here means the whole line is legal).
            check(f"setup: full line replays ({pid})", True)
        except ValueError as e:
            check(f"setup: {pid} replays", False, str(e))


def test_determinism(stockfish):
    eng = ProbeEngine(stockfish)
    try:
        board = chess.Board(
            "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
        fen = board.fen()
        pvs1, _ = eng.analyse_stage1(fen, 50_000)
        pvs2, _ = eng.analyse_stage1(fen, 50_000)
        norm = lambda pvs: [(e["move"], e["cp"], e["mate"]) for e in pvs]
        check("determinism: identical MultiPV output",
              norm(pvs1) == norm(pvs2), f"{norm(pvs1)} vs {norm(pvs2)}")
    finally:
        eng.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stockfish", default=None)
    ap.add_argument("--db-url", default=os.getenv("DATABASE_URL"))
    a = ap.parse_args()
    sf = a.stockfish or os.getenv("STOCKFISH_PATH") or "/usr/games/stockfish"
    test_segments_synthetic()
    test_mate_margins()
    if a.db_url:
        try:
            test_setup_move_db(a.db_url)
        except Exception as e:
            check("setup: db reachable", False, str(e))
    else:
        print("skip setup-move db test (no --db-url)")
    try:
        test_determinism(sf)
    except Exception as e:
        check("determinism: engine runs", False, str(e))
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
