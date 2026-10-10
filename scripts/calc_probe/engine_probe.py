"""Own Stockfish processes for the calc probe.

Deliberately NOT importing the web app's Review engine singleton
(src/engines/stockfish_engine.py). Each worker thread owns its process with
identical deterministic settings: Threads=1, fixed Hash, nodes limits only.
`game=object()` forces python-chess to send ucinewgame before every analysis
(clearing TT); SimpleEngine synchronises over the protocol (isready) per call.
"""

from __future__ import annotations

import threading
import time

import chess
import chess.engine

DEFAULT_THREADS = 1
DEFAULT_HASH_MB = 64


class ProbeEngine:
    def __init__(self, path: str, hash_mb: int = DEFAULT_HASH_MB,
                 threads: int = DEFAULT_THREADS, timeout: float = 120.0):
        # Generous UCI-handshake timeout: spawning a 100MB binary under a
        # fully loaded 2-core box starved the default and killed a run.
        self._engine = chess.engine.SimpleEngine.popen_uci(path,
                                                            timeout=timeout)
        self._engine.configure({"Threads": threads, "Hash": hash_mb,
                                "UCI_ShowWDL": True})
        self.name = self._engine.id.get("name", "unknown")
        self.stage1_ms: list[float] = []
        self.stage2_ms: list[float] = []
        self.solver_ms: list[float] = []
        # Per-call log: {"phase", "nodes", "ms", "reported_nps"}.
        self.call_log: list[dict] = []

    def close(self) -> None:
        try:
            self._engine.quit()
        except Exception:
            pass

    def _analyse(self, fen: str, nodes: int, multipv: int,
                 phase: str) -> list[dict]:
        board = chess.Board(fen)
        turn = board.turn  # defender (or solver) to move: scores kept in POV
        # Nodes only. NO wall-clock backstop is applied to any call (Review's
        # REVIEW_NODES_BACKSTOP must not carry over: 2M-node convergence calls
        # take several seconds). analyse() is also given no timeout, so a call
        # that ever hit a time limit would have to come from here and fail
        # loudly on the asserts below.
        limit = chess.engine.Limit(nodes=nodes)
        assert limit.time is None and limit.depth is None, limit
        t0 = time.perf_counter()
        infos = self._engine.analyse(board, limit, multipv=multipv,
                                     game=object())  # fresh game -> ucinewgame
        dt_ms = (time.perf_counter() - t0) * 1000.0
        reported_nps = infos[0].get("nps") if infos else None
        nodes_done = infos[0].get("nodes") if infos else None
        self.call_log.append({"phase": phase, "nodes": nodes_done,
                              "ms": dt_ms, "reported_nps": reported_nps,
                              "t_end": time.perf_counter()})
        out: list[dict] = []
        for info in infos:
            pv = info.get("pv") or []
            score = info.get("score")
            entry: dict = {"move": pv[0].uci() if pv else None,
                           "cp": None, "mate": None,
                           "wdl_w": None, "wdl_d": None, "wdl_l": None,
                           "nodes": info.get("nodes"), "depth": info.get("depth")}
            if score is not None:
                pov = score.pov(turn)  # defender's (side-to-move's) POV
                if pov.is_mate():
                    entry["mate"] = pov.mate()
                else:
                    entry["cp"] = pov.score()
            wdl = info.get("wdl")
            if wdl is not None:
                w = wdl.pov(turn)
                entry["wdl_w"] = w.wins
                entry["wdl_d"] = w.draws
                entry["wdl_l"] = w.losses
            out.append(entry)
        return out, dt_ms

    def analyse_stage1(self, fen: str, nodes: int) -> tuple[list[dict], float]:
        pvs, ms = self._analyse(fen, nodes, 2, "stage1")
        self.stage1_ms.append(ms)
        return pvs, ms

    def analyse_stage2(self, fen: str, nodes: int,
                       phase: str = "stage2") -> tuple[list[dict], float]:
        pvs, ms = self._analyse(fen, nodes, 4, phase)
        if phase == "stage2":
            self.stage2_ms.append(ms)
        return pvs, ms

    def analyse_solver(self, fen: str, nodes: int) -> tuple[list[dict], float]:
        pvs, ms = self._analyse(fen, nodes, 2, "solver")
        self.solver_ms.append(ms)
        return pvs, ms


class EnginePool:
    """One ProbeEngine per worker thread; created lazily, same settings."""

    def __init__(self, path: str, hash_mb: int = DEFAULT_HASH_MB):
        self._path = path
        self._hash_mb = hash_mb
        self._local = threading.local()
        self._all: list[ProbeEngine] = []
        self._lock = threading.Lock()
        self._spawn_lock = threading.Lock()  # serialize engine spawns

    def get(self) -> ProbeEngine:
        eng = getattr(self._local, "engine", None)
        if eng is None:
            # Serialized: concurrent spawns under full CPU load starved the
            # UCI handshake in the pilot's convergence phase.
            with self._spawn_lock:
                eng = getattr(self._local, "engine", None)
                if eng is None:
                    eng = ProbeEngine(self._path, self._hash_mb)
                    self._local.engine = eng
                    with self._lock:
                        self._all.append(eng)
        return eng

    def close_all(self) -> None:
        for eng in self._all:
            eng.close()
