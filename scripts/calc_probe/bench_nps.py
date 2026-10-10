"""One-off nps benchmark for item 1: single vs contended Stockfish throughput.

Usage: python3 -u bench_nps.py [--stockfish PATH] [--hash-mb 64]
Prints per-call nodes, ms, nps (engine-reported and wall-derived).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chess
import chess.engine

FENS = [
    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    "r6k/pp2r2p/4Rp1Q/3p4/8/1N1P2R1/PqP2bPP/7K b - - 0 24",
    "2r3k1/5ppp/3p4/p1nP4/P1N1P3/5P2/5BPP/2R3K1 w - - 0 30",
    "6k1/5ppp/8/8/8/5PPP/5RK1/8 w - - 0 50",
]


def make_engine(path, hash_mb, threads=1):
    e = chess.engine.SimpleEngine.popen_uci(path)
    e.configure({"Threads": threads, "Hash": hash_mb, "UCI_ShowWDL": True})
    return e


def call(e, fen, nodes, multipv, fresh):
    b = chess.Board(fen)
    t0 = time.perf_counter()
    infos = e.analyse(b, chess.engine.Limit(nodes=nodes), multipv=multipv,
                      game=object() if fresh else None)
    ms = (time.perf_counter() - t0) * 1000.0
    rep_nps = infos[0].get("nps")
    nodes_done = infos[0].get("nodes")
    return nodes_done, ms, rep_nps


def worker(path, hash_mb, nodes, multipv, fresh, out, idx):
    e = make_engine(path, hash_mb)
    try:
        for i, fen in enumerate(FENS):
            out.append((idx, i) + call(e, fen, nodes, multipv, fresh))
    finally:
        e.quit()


def scenario(path, hash_mb, n_engines, nodes, multipv, fresh, label):
    out: list = []
    threads = [threading.Thread(target=worker,
                                args=(path, hash_mb, nodes, multipv, fresh,
                                      out, k))
               for k in range(n_engines)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    print(f"== {label} ==")
    for idx, i, nn, ms, rnps in sorted(out):
        wall_nps = (nn / ms * 1000.0) if nn and ms else 0
        print(f"  eng{idx} fen{i}: nodes={nn} wall_ms={ms:.0f} "
              f"reported_nps={rnps} wall_nps={wall_nps:.0f}")
    all_ms = [ms for _, _, _, ms, _ in out]
    all_n = [nn for _, _, nn, _, _ in out]
    print(f"  total_wall={wall:.1f}s sum_nodes={sum(all_n)} "
          f"aggregate_nps={sum(all_n)/wall:.0f}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stockfish", default=None)
    ap.add_argument("--hash-mb", type=int, default=64)
    a = ap.parse_args()
    path = (a.stockfish or os.getenv("STOCKFISH_PATH")
            or "/usr/games/stockfish")
    tmp = make_engine(path, a.hash_mb)
    print(f"engine={tmp.id.get('name')} cpu_count={os.cpu_count()}")
    tmp.quit()
    N = 500_000
    scenario(path, a.hash_mb, 1, N, 4, True, "1 engine, Hash=%d, fresh-token" % a.hash_mb)
    scenario(path, a.hash_mb, 1, N, 4, False, "1 engine, Hash=%d, reused game" % a.hash_mb)
    scenario(path, 16, 1, N, 4, True, "1 engine, Hash=16, fresh-token")
    scenario(path, a.hash_mb, 2, N, 4, True, "2 engines concurrent (== ncpu?)")
    scenario(path, a.hash_mb, 4, N, 4, True, "4 engines concurrent (smoke setting)")


if __name__ == "__main__":
    main()
