"""
Postgres-backed persistent probe cache for the Lichess tablebase fallback.

WHY THIS EXISTS
===============
services/tablebase.py's local Syzygy files cover every <=5-man position, so
those probes never touch the network. 6-7-man positions (a large share of
the sourced Endgame Trainer content) go to tablebase.lichess.ovh, which is
rate-limited and network-dependent. The module already has a short-lived
in-memory fallback cache (300s TTL, per process), but that does nothing
across restarts, across workers, or across users arriving hours apart.

This cache stores each successful fallback answer (normalized FEN ->
outcome/wdl/dtz) in Postgres, so a position is paid to Lichess ONCE per
deployment and every later probe is a primary-key lookup. Tablebase
verdicts are facts, not opinions, so a row never goes stale.

CONTRACT (deliberately softer than the probe module's)
======================================================
The cache is an optimization, never a dependency:
  * get() returns None on a miss OR on any DB failure;
  * put() swallows DB failures after logging;
  * neither ever raises into the probe path, and neither guesses an answer.
A cache outage degrades to the old behavior (an HTTP fallback attempt per
probe), which is exactly why it is safe to fail open here. Failures are
logged at WARNING with a 60s throttle so a down database cannot flood logs.

Thread safety: ThreadedConnectionPool + one checkout per operation; FastAPI
runs sync endpoints in a threadpool, so probes can be concurrent.
"""
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import psycopg2
from psycopg2 import pool

log = logging.getLogger(__name__)

_ERROR_LOG_THROTTLE_SECONDS = 60.0


def _connection_kwargs(dsn: Optional[str]) -> Dict[str, Any]:
    """Discrete DB_* vars first, then DATABASE_URL -- the same precedence as
    the rest of the codebase (seed scripts, endgame_prefetch, the test
    helpers). src/.env carries valid DB_* values plus a PLACEHOLDER
    DATABASE_URL, so a bare URL lookup is not safe in this tree."""
    if dsn:
        return {"dsn": dsn}
    config = {
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 5432)),
    }
    if all([config["dbname"], config["user"], config["password"]]):
        return config
    database_url = os.getenv("DATABASE_URL")
    if not database_url or "://" not in database_url:
        raise RuntimeError("no usable DB config for the tablebase probe cache")
    return {"dsn": database_url}


class PostgresProbeCache:
    """Persistent fen_key -> TablebaseResult fields store.

    Connection budget: this is a SECOND, additive pool on top of
    core/database.py's main app pool -- not a draw from it. It is capped at
    ONE connection (minconn=0 holds no idle connection; the single slot is
    opened lazily and reused). A cache operation that cannot get the slot
    degrades to a miss; it never waits and never starves the main pool,
    because the cap is per process and fixed. Keep this cap at 1 unless the
    production connection budget is recomputed: every extra slot here is an
    extra connection per uvicorn process per replica against the database's
    max_connections.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        minconn: int = 0,
        maxconn: int = 1,
    ):
        self._pool: Optional[pool.ThreadedConnectionPool] = (
            pool.ThreadedConnectionPool(
                minconn=minconn,
                maxconn=maxconn,
                **_connection_kwargs(dsn),
            )
        )
        self._lock = threading.Lock()
        self._last_error_log = 0.0

    def _warn(self, operation: str, exc: Exception) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._last_error_log < _ERROR_LOG_THROTTLE_SECONDS:
                return
            self._last_error_log = now
        log.warning(
            "tablebase probe cache %s failed (degrading to uncached): %s",
            operation, exc,
        )

    def get(self, fen_key: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self._pool.getconn()
        except Exception as exc:  # pool exhaustion / closed pool
            self._warn("get", exc)
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT outcome, wdl, dtz, source "
                    "FROM tablebase_probe_cache WHERE fen_key = %s",
                    (fen_key,),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception as exc:
            conn.rollback()
            self._warn("get", exc)
            return None
        finally:
            self._pool.putconn(conn)
        if row is None:
            return None
        return {"outcome": row[0], "wdl": row[1], "dtz": row[2], "source": row[3]}

    def put(self, fen_key: str, result: Dict[str, Any]) -> None:
        try:
            conn = self._pool.getconn()
        except Exception as exc:
            self._warn("put", exc)
            return
        try:
            with conn.cursor() as cur:
                # First writer wins: a stored verdict is a fact, never
                # overwritten by a re-probe.
                cur.execute(
                    """
                    INSERT INTO tablebase_probe_cache
                        (fen_key, outcome, wdl, dtz, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (fen_key) DO NOTHING
                    """,
                    (
                        fen_key,
                        result["outcome"],
                        result["wdl"],
                        result["dtz"],
                        result.get("source") or "lichess",
                    ),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            self._warn("put", exc)
        finally:
            self._pool.putconn(conn)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
