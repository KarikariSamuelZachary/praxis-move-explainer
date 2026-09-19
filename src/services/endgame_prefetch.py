"""
Best-effort prefetch of a drill's known solution line into the tablebase
probe cache.

WHY THIS EXISTS
===============
A sourced Endgame Trainer drill can start at 6-7 men, which the local
Syzygy files cannot cover; every user move then asks tablebase.lichess.ovh
(~1 req/s, network-dependent). But the drill also has a KNOWN solution
line: endgame_positions.source_puzzle_id links back to the Lichess puzzle,
whose moves we can replay. Warming the cache with those positions at
drill-selection time means the expected path costs zero live API latency;
only a user's deviations fall through to the (still cached, paced)
fallback.

DESIGN
======
  * A single daemon worker drains a bounded FIFO queue, one probe at a
    time, pacing only when the previous probe actually hit the network
    (local <=5-man probes are memory-speed and never wait).
  * Absolutely best-effort: every failure is logged and dropped; nothing
    here can fail a selection request or a drill. If the persistent cache
    is not registered (app not booted, tests, scripts) the hook is a no-op,
    because per-process cache warming has no cross-user value.
  * The hook lives in services/endgame_library.select_drill_position, which
    builds its own DB connection in the worker (a route's request
    connection is closed by the time the background probe runs).
"""
import logging
import os
import queue
import threading
import time
from typing import Iterable, List, Optional

import chess
import psycopg2
from dotenv import load_dotenv

from services.tablebase import (
    TablebaseUnavailableError,
    persistent_cache_enabled,
    probe_tablebase,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

log = logging.getLogger(__name__)

_PREFETCH_INTERVAL = float(os.getenv("ENDGAME_PREFETCH_INTERVAL", "1.0"))
_QUEUE_MAX = int(os.getenv("ENDGAME_PREFETCH_QUEUE_MAX", "5000"))
_SEEN_MAX = 200_000
_DISABLED = os.getenv("ENDGAME_PREFETCH_DISABLED", "").lower() in ("1", "true", "yes")

_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=_QUEUE_MAX)
_seen: set = set()
_seen_lock = threading.Lock()
_worker_lock = threading.Lock()
_worker_started = False
_last_remote_probe: Optional[float] = None
_pace_lock = threading.Lock()


def _db_config():
    config = {
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 5432)),
    }
    if not all([config["dbname"], config["user"], config["password"]]):
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("no usable DB config for drill prefetch")
        return {"dsn": database_url}
    return config


def available() -> bool:
    """Prefetch only has cross-user value with the persistent cache, and is
    off entirely when disabled by env or when the app is not booted."""
    return not _DISABLED and persistent_cache_enabled()


def enqueue_fens(fens: Iterable[str]) -> int:
    if not available():
        return 0
    enqueued = 0
    for fen in fens:
        key = " ".join(fen.split()[:4])
        with _seen_lock:
            if key in _seen:
                continue
            if len(_seen) >= _SEEN_MAX:
                _seen.clear()
            _seen.add(key)
        try:
            _queue.put_nowait(("fen", fen))
            enqueued += 1
        except queue.Full:
            log.warning("endgame prefetch queue full (%d); dropping line", _QUEUE_MAX)
            break
    if enqueued:
        _ensure_worker()
    return enqueued


def enqueue_drill_line(position_id: str) -> bool:
    """Selection-time hook: enqueue one line task. Does NO database work on
    the caller's thread (the worker resolves the puzzle line), so it cannot
    slow a selection request down."""
    if not available():
        return False
    try:
        _queue.put_nowait(("line", str(position_id)))
        _ensure_worker()
        return True
    except queue.Full:
        log.warning("endgame prefetch queue full (%d); dropping drill line", _QUEUE_MAX)
        return False


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_worker, name="endgame-prefetch", daemon=True
        )
        thread.start()
        _worker_started = True


def _pace_after_remote() -> None:
    global _last_remote_probe
    with _pace_lock:
        if _last_remote_probe is None:
            return
        remaining = _PREFETCH_INTERVAL - (time.monotonic() - _last_remote_probe)
        if remaining > 0:
            time.sleep(remaining)


def _worker() -> None:
    global _last_remote_probe
    while True:
        task = _queue.get()
        try:
            kind, payload = task
            if kind == "line":
                enqueue_fens(_resolve_line_fens(payload))
                continue
            fen = payload
            _pace_after_remote()
            result = probe_tablebase(fen)
            if result.source == "lichess":
                with _pace_lock:
                    _last_remote_probe = time.monotonic()
        except TablebaseUnavailableError as exc:
            with _pace_lock:
                _last_remote_probe = time.monotonic()
            log.debug("endgame prefetch could not probe %s: %s", payload, exc)
        except ValueError as exc:
            log.debug("endgame prefetch skipping malformed FEN %s: %s", payload, exc)
        except Exception as exc:  # noqa: BLE001 - never let the worker die
            log.warning("endgame prefetch unexpected error for %s: %s", payload, exc)
        finally:
            _queue.task_done()


def solution_line_fens(puzzle_fen: str, moves: List[str], stored_fen: str) -> List[str]:
    """Every position along the puzzle's full move sequence, starting with
    the drill position (after the opponent's setup move). Falls back to just
    the stored FEN when the replay does not reproduce it."""
    board = chess.Board(puzzle_fen)
    replayed: List[str] = []
    for uci in moves:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return [stored_fen]
        if move not in board.legal_moves:
            return [stored_fen]
        board.push(move)
        replayed.append(board.fen())
    if not replayed:
        return [stored_fen]
    if " ".join(replayed[0].split()[:4]) != " ".join(stored_fen.split()[:4]):
        log.warning(
            "endgame prefetch replay mismatch (puzzle first move vs stored drill FEN)"
        )
        return [stored_fen]
    return replayed


def _resolve_line_fens(position_id: str) -> List[str]:
    """DB lookup (worker thread only): drill FEN -> source puzzle -> line."""
    try:
        conn = psycopg2.connect(**_db_config())
    except Exception as exc:  # noqa: BLE001
        log.warning("endgame prefetch could not open DB connection: %s", exc)
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fen, source_puzzle_id FROM endgame_positions WHERE id = %s",
                (str(position_id),),
            )
            row = cur.fetchone()
            if row is None:
                return []
            stored_fen, source_puzzle_id = row
            if not source_puzzle_id:
                return [stored_fen]
            cur.execute(
                "SELECT fen, moves FROM puzzles WHERE id = %s",
                (source_puzzle_id,),
            )
            puzzle = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        log.warning("endgame prefetch lookup failed for %s: %s", position_id, exc)
        return []
    finally:
        conn.close()

    if puzzle is None:
        return [stored_fen]
    puzzle_fen, moves = puzzle
    if not moves:
        return [stored_fen]
    return solution_line_fens(puzzle_fen, moves.split(), stored_fen)


def prefetch_drill_line(position_id: str) -> int:
    """Blocking convenience: resolve a drill's line and enqueue every FEN.
    The drill-selection hook uses enqueue_drill_line() instead so a selection
    request never waits on the database or replay. Best-effort throughout."""
    if not available():
        return 0
    return enqueue_fens(_resolve_line_fens(position_id))
