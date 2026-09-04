import logging
import os
import time
import psycopg2
from psycopg2 import pool
from fastapi import Request

log = logging.getLogger(__name__)

connection_pool = None

def init_db():
    global connection_pool
    try:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")

        connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=database_url,
        )
        log.info("Database connection pool created successfully")
    except Exception as e:
        log.error("Failed to connect to database: %s", e)
        raise


def get_db(request: Request):
    acquisition_started = time.perf_counter()
    conn = connection_pool.getconn()
    if request.url.path == "/api/puzzles":
        log.info(
            "[PUZZLE_PROFILE] phase=connection_acquisition duration_ms=%.2f",
            (time.perf_counter() - acquisition_started) * 1000,
        )
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        connection_pool.putconn(conn)
