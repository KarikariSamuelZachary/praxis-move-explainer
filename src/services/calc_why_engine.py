"""Private Stockfish wrapper for the calc "show why" search.

Own process (never the Review engine singleton), SF19 binary, Threads=1,
Hash=16, nodes-only limits, fresh game token per call (ucinewgame, TT
cleared), no wall-clock backstop. Same pattern as the offline calc_probe
engine code (scripts/calc_probe/engine_probe.py), reimplemented here rather
than imported: src must not depend on scripts/.

At most one why-search runs per process at a time: the router takes
_search_lock non-blocking and returns 429 when busy. The engine process
itself is a lazily-created singleton (spawning the 100MB binary per
request would dominate the search cost).
"""

from __future__ import annotations

import os
import shutil
import threading

import chess
import chess.engine

WHY_THREADS = 1
WHY_HASH_MB = 16
WHY_NODES = 500_000

_search_lock = threading.Lock()
_engine = None
_engine_guard = threading.Lock()
search_calls = 0  # engine analyses performed (tests assert no re-search)
_search_calls_guard = threading.Lock()


def resolve_engine_path() -> str | None:
    candidates = [
        os.getenv("STOCKFISH_PATH") or "",
        "/opt/stockfish/stockfish",
        os.path.expanduser("~/.local/opt/stockfish/stockfish"),
    ]
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    return shutil.which("stockfish")


def try_acquire_search() -> bool:
    return _search_lock.acquire(blocking=False)


def release_search() -> None:
    try:
        _search_lock.release()
    except RuntimeError:
        pass


def get_engine() -> tuple[chess.engine.SimpleEngine, str]:
    """Lazily-created shared engine + version string. Raises RuntimeError
    (binary missing) or TimeoutError (starved UCI handshake)."""
    global _engine
    with _engine_guard:
        if _engine is not None:
            try:
                name = _engine.id.get("name", "unknown")
            except Exception:
                name = "unknown"
            return _engine, name
        path = resolve_engine_path()
        if not path:
            raise RuntimeError("no Stockfish binary found for why-search")
        eng = chess.engine.SimpleEngine.popen_uci(path, timeout=120.0)
        eng.configure({"Threads": WHY_THREADS, "Hash": WHY_HASH_MB,
                       "UCI_ShowWDL": True})
        _engine = eng
        return eng, eng.id.get("name", "unknown")


def close_engine() -> None:
    global _engine
    with _engine_guard:
        if _engine is not None:
            try:
                _engine.quit()
            except Exception:
                pass
            _engine = None


def current_engine_version() -> str:
    """Engine version string, spawning the lazy singleton if needed (so a
    cache hit can still verify the stored version without a search)."""
    _, version = get_engine()
    return version


def position_key(fen: str) -> str:
    """Cache key: first 4 FEN fields (transpositions share one search)."""
    return " ".join(fen.split()[:4])


def _wdl_win_prob(wdl, turn: chess.Color) -> float | None:
    if wdl is None:
        return None
    w = wdl.pov(turn)
    return (w.wins + w.draws / 2.0) / 1000.0


def analyse(fen: str, nodes: int = WHY_NODES,
            multipv: int = 1) -> list[dict]:
    """One nodes-limited analysis. Scores in side-to-move POV:
    {"move", "pv" (uci list), "cp", "mate", "wdl_w/d/l", "win_prob",
    "depth", "nodes"}."""
    global search_calls
    engine, _ = get_engine()
    board = chess.Board(fen)
    turn = board.turn
    limit = chess.engine.Limit(nodes=nodes)
    assert limit.time is None and limit.depth is None, limit
    infos = engine.analyse(board, limit, multipv=multipv,
                           game=object())  # fresh game -> ucinewgame
    with _search_calls_guard:
        search_calls += 1
    out: list[dict] = []
    for info in infos:
        pv = info.get("pv") or []
        score = info.get("score")
        entry: dict = {
            "move": pv[0].uci() if pv else None,
            "pv": [m.uci() for m in pv],
            "cp": None, "mate": None,
            "wdl_w": None, "wdl_d": None, "wdl_l": None,
            "win_prob": None,
            "depth": info.get("depth"), "nodes": info.get("nodes"),
        }
        if score is not None:
            pov = score.pov(turn)
            if pov.is_mate():
                entry["mate"] = pov.mate()
            else:
                entry["cp"] = pov.score()
        wdl = info.get("wdl")
        if wdl is not None:
            w = wdl.pov(turn)
            entry["wdl_w"], entry["wdl_d"], entry["wdl_l"] = (
                w.wins, w.draws, w.losses)
            entry["win_prob"] = _wdl_win_prob(wdl, turn)
        out.append(entry)
    return out
