import logging
import os
import time
import psycopg2
from psycopg2 import pool
from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

connection_pool = None

def init_db():
    global connection_pool
    try:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")

        # psycopg2's ThreadedConnectionPool fails fast (raises PoolError)
        # instead of waiting once maxconn connections are checked out, and it
        # only returns connections to the idle pool while fewer than minconn
        # are pooled (otherwise it closes them and the next request pays a
        # fresh TCP + auth + backend-fork).
        #
        # PRODUCTION CAVEAT (NOT YET VERIFIED): the real connection ceiling of
        # the deployed Azure Postgres instance has not been checked. Before
        # trusting these defaults in production, run `SHOW max_connections;`
        # against that instance (or read it in the Azure portal) and set
        # DB_POOL_MIN/DB_POOL_MAX accordingly. The pool is PER PROCESS: each
        # uvicorn worker builds its own pool, so N workers multiply this
        # budget against the server limit. Background import/analysis jobs
        # also draw from this same pool and can hold a connection for their
        # entire multi-minute run.
        pool_min = int(os.getenv("DB_POOL_MIN", "5"))
        pool_max = int(os.getenv("DB_POOL_MAX", "25"))
        connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=pool_min,
            maxconn=pool_max,
            dsn=database_url,
        )
        log.info("Database connection pool created successfully")
    except Exception as e:
        log.error("Failed to connect to database: %s", e)
        raise


def get_db(request: Request):
    acquisition_started = time.perf_counter()
    try:
        conn = connection_pool.getconn()
    except pool.PoolError as exc:
        # Exhaustion is transient backpressure, not a server bug: surface it
        # as 503 so clients can retry instead of seeing a raw 500.
        log.warning("Database connection pool exhausted (maxconn=%s)", connection_pool.maxconn)
        raise HTTPException(
            status_code=503,
            detail="Server is busy, please try again.",
        ) from exc
    if request.url.path == "/api/puzzles":
        log.info(
            "[PUZZLE_PROFILE] phase=connection_acquisition endpoint=puzzle_batch duration_ms=%.2f",
            (time.perf_counter() - acquisition_started) * 1000,
        )
    elif request.method == "GET" and request.url.path.startswith("/api/puzzles/"):
        log.info(
            "[PUZZLE_PROFILE] phase=connection_acquisition endpoint=puzzle_by_id duration_ms=%.2f path=%s",
            (time.perf_counter() - acquisition_started) * 1000,
            request.url.path,
        )
    elif request.method == "POST" and request.url.path == "/api/woodpecker/entries":
        log.info(
            "[WOODPECKER_PROFILE] phase=connection_acquisition endpoint=entry_create duration_ms=%.2f",
            (time.perf_counter() - acquisition_started) * 1000,
        )
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        connection_pool.putconn(conn)
