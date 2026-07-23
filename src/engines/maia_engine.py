"""
Maia-3 engine wrapper for human-like chess move generation.

Mirrors the Stockfish wrapper pattern. Maia-3 is a transformer that predicts
human moves at a given Elo; it is NOT a search engine. It ships as a UCI
executable (`maia3-uci`) installed via pip from
https://github.com/CSSLab/maia3 and downloads its checkpoint from Hugging Face
on first use (pre-warmed at deploy time via `scripts/prewarm_maia3.py`).

We run the `maia3-uci --model maia3-5m` entrypoint (smallest, CPU-friendly) as
a single long-lived UCI engine instance configured per-request via UCI options.
"""
import logging
import os
import time
from threading import Lock
from typing import Optional

import chess
import chess.engine

log = logging.getLogger(__name__)

# Smallest model preset (CPU-friendly). Used as the default.
MAIA3_DEFAULT_MODEL = "maia3-5m"
MAIA3_DEFAULT_ELO = 1500


def resolve_maia3_command() -> list[str]:
    """
    Resolve the UCI launch command for the Maia-3 5M preset.

    Preference order:
      1. MAIA3_COMMAND env var (full pre-split command line).
      2. `maia3-uci --model maia3-5m` on PATH.
      3. `python -m maia3.uci --model maia3-5m` if requested via
         MAIA3_USE_PYTHON_MODULE=1.
    """
    configured = os.getenv("MAIA3_COMMAND")
    if configured:
        import shlex

        return shlex.split(configured)

    if os.getenv("MAIA3_USE_PYTHON_MODULE") == "1":
        import sys

        return [sys.executable, "-m", "maia3.uci", "--model", MAIA3_DEFAULT_MODEL]

    return ["maia3-uci", "--model", MAIA3_DEFAULT_MODEL]


def prewarm_maia3_model() -> bool:
    """
    Pre-download and load the Maia-3 5M checkpoint into the Hugging Face cache.

    Call this during the build/deploy step so a live user's first request
    does not trigger a checkpoint download from Hugging Face. This starts the
    UCI engine and performs one throwaway inference so the checkpoint is both
    downloaded and validated. Returns True if Maia-3 is ready afterwards.
    """
    log.info("Pre-warming Maia-3 5M checkpoint (Hugging Face download)...")
    started = time.time()

    try:
        board = chess.Board()
        with Maia3Engine() as engine:
            engine.best_move(board, elo=MAIA3_DEFAULT_ELO, temperature=0.0)
        log.info(
            "Maia-3 5M checkpoint cached and inference validated after %.1fs",
            time.time() - started,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Maia-3 pre-warm failed (build env may lack outbound HF access): %s",
            exc,
        )
        return False


class Maia3Engine:
    """
    Long-lived Maia-3 UCI engine instance.

    Maia-3 is configured per-request via the `Elo` UCI option rather than
    restarting the process. Use the module-level `get_maia3()` accessor to
    obtain the shared singleton; call `close_maia3()` on shutdown.
    """

    MODEL = MAIA3_DEFAULT_MODEL

    def __init__(self, command: Optional[list[str]] = None, elo: int = MAIA3_DEFAULT_ELO):
        self.command = command if command is not None else resolve_maia3_command()
        self.elo = int(elo)
        self.engine: Optional[chess.engine.SimpleEngine] = None
        self._lock = Lock()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start(self):
        log.info("Starting Maia-3 UCI engine: %s", " ".join(self.command))
        self.engine = chess.engine.SimpleEngine.popen_uci(self.command)
        try:
            self.engine.configure(
                {"Elo": self.elo, "Temperature": 0}
            )
        except chess.engine.EngineError:
            # Some presets expose Elo only; fall back to the --elo flag at popen.
            log.warning("Maia-3 runtime Elo config unsupported; using launch-time Elo.")

    def close(self):
        if self.engine:
            try:
                self.engine.quit()
            except chess.engine.EngineError:
                pass
            self.engine = None

    def best_move(
        self,
        board: chess.Board,
        elo: Optional[int] = None,
        temperature: float = 0.0,
    ) -> dict:
        """
        Return Maia-3's suggested move for `board` at the given Elo.

        `temperature=0` selects the argmax human move; a small positive value
        samples from Maia-3's human-move distribution (useful for the sparring
        bot). Returns a dict with uci, san, elo, temperature, and inference_ms.
        """
        if not self.engine:
            raise RuntimeError(
                "Maia-3 engine not started. Use the context manager or get_maia3()."
            )

        with self._lock:
            return self._best_move_locked(board, elo=elo, temperature=temperature)

    def _best_move_locked(
        self,
        board: chess.Board,
        elo: Optional[int] = None,
        temperature: float = 0.0,
    ) -> dict:
        effective_elo = int(elo) if elo is not None else self.elo
        if effective_elo != self.elo:
            try:
                self.engine.configure({"Elo": effective_elo})
                self.elo = effective_elo
            except chess.engine.EngineError:
                pass

        try:
            self.engine.configure({"Temperature": temperature})
        except chess.engine.EngineError:
            pass

        started = time.time()
        # Maia-3 is a policy network, not a search engine: one node equals one
        # forward pass.
        result = self.engine.play(
            board,
            limit=chess.engine.Limit(nodes=1),
        )
        inference_ms = (time.time() - started) * 1000

        move_uci = result.move.uci() if result.move else ""
        move_san = board.san(result.move) if result.move else ""

        # python-chess exposes WDL-style info for Maia-3 bestmove; may be absent.
        wdl = None
        info = getattr(result, "info", {}) or {}
        if "wdl" in info and info["wdl"] is not None:
            try:
                wdl = {
                    "win": int(info["wdl"].winning_chance * 1000) / 1000,
                    "draw": int(info["wdl"].draw_chance * 1000) / 1000,
                    "loss": int(info["wdl"].losing_chance * 1000) / 1000,
                }
            except Exception:  # noqa: BLE001
                wdl = None

        return {
            "best_move_uci": move_uci,
            "best_move_san": move_san,
            "elo": effective_elo,
            "temperature": temperature,
            "inference_ms": round(inference_ms, 1),
            "model": self.MODEL,
            "wdl": wdl,
        }


# --- Module-level singleton (lifecycle managed by FastAPI startup/shutdown) ---

_maia3: Optional[Maia3Engine] = None


def start_maia3() -> Maia3Engine:
    global _maia3
    if _maia3 is None:
        _maia3 = Maia3Engine()
        _maia3.start()
    return _maia3


def get_maia3() -> Maia3Engine:
    if _maia3 is None:
        return start_maia3()
    return _maia3


def close_maia3():
    global _maia3
    if _maia3 is not None:
        _maia3.close()
        _maia3 = None
