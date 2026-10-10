"""Stage 2 verification: nodes-only, fresh-token engine, calc_probe parity.

Deliberately NOT the web app's Review singleton and NOT the
``StockfishEngine`` wrapper (whose ``evaluate(nodes=...)`` couples the node
limit with a wall-clock backstop). Raw ``python-chess`` SimpleEngine with
``Threads=1 Hash=16 UCI_ShowWDL``, ``Limit(nodes=...)`` only, and
``game=object()`` per call (ucinewgame clears TT) -- the same discipline as
``scripts/calc_probe/engine_probe.py``.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import chess
import chess.engine


def score_to_cp_mate(score: chess.engine.Score, turn: chess.Color) -> tuple[Optional[float], Optional[int]]:
    """Score from side-to-move POV -> (cp, mate). Mate -> cp None."""
    pov = score.pov(turn)
    if pov.is_mate():
        return None, pov.mate()
    return float(pov.score()), None


def mate_to_cp_equiv(mate: Optional[int]) -> Optional[float]:
    """Map mate score to the +/-10000 cp equivalent (engine-wrapper parity)."""
    if mate is None:
        return None
    return 10000.0 if mate > 0 else -10000.0


class Stage2Engine:
    """Private throwaway Stockfish process for verification."""

    def __init__(self, path: str, hash_mb: int = 16, timeout: float = 120.0):
        self._engine = chess.engine.SimpleEngine.popen_uci(path, timeout=timeout)
        self._engine.configure({"Threads": 1, "Hash": hash_mb, "UCI_ShowWDL": True})
        try:
            self.name = self._engine.id.get("name", "unknown")
        except Exception:  # noqa: BLE001
            self.name = "unknown"

    def close(self) -> None:
        try:
            self._engine.quit()
        except Exception:  # noqa: BLE001
            pass

    def analyse_before(self, fen: str, nodes: int, multipv: int = 4) -> tuple[List[Dict[str, Any]], float]:
        """MultiPV analysis of the pre-move position (user to move)."""
        board = chess.Board(fen)
        turn = board.turn
        t0 = time.perf_counter()
        infos = self._engine.analyse(
            board, chess.engine.Limit(nodes=nodes), multipv=multipv, game=object()
        )
        dt_ms = (time.perf_counter() - t0) * 1000.0
        out: List[Dict[str, Any]] = []
        for info in infos:
            pv = info.get("pv") or []
            entry: Dict[str, Any] = {
                "move": pv[0].uci() if pv else None,
                "cp": None,
                "mate": None,
                "pv_uci": [m.uci() for m in pv],
                "depth": info.get("depth"),
                "nodes": info.get("nodes"),
            }
            score = info.get("score")
            if score is not None:
                cp, mate = score_to_cp_mate(score, turn)
                entry["cp"] = cp
                entry["mate"] = mate
            out.append(entry)
        return out, dt_ms

    def analyse_after(self, fen_after: str, nodes: int) -> tuple[Dict[str, Any], float]:
        """Single-PV analysis of the post-move position (opponent to move).

        Returns the score from the OPPONENT's (side-to-move's) POV; the caller
        flips it into the mover's frame.
        """
        board = chess.Board(fen_after)
        turn = board.turn
        t0 = time.perf_counter()
        info = self._engine.analyse(
            board, chess.engine.Limit(nodes=nodes), multipv=1, game=object()
        )
        dt_ms = (time.perf_counter() - t0) * 1000.0
        if isinstance(info, list):
            info = info[0] if info else {}
        pv = info.get("pv") or []
        entry: Dict[str, Any] = {
            "move": pv[0].uci() if pv else None,
            "cp": None,
            "mate": None,
            "pv_uci": [m.uci() for m in pv],
            "depth": info.get("depth"),
            "nodes": info.get("nodes"),
        }
        score = info.get("score")
        if score is not None:
            cp, mate = score_to_cp_mate(score, turn)
            entry["cp"] = cp
            entry["mate"] = mate
        return entry, dt_ms
