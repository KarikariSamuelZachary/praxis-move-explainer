import logging

from core import database

log = logging.getLogger(__name__)


def run_migrations():
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id          SERIAL PRIMARY KEY,
                    clerk_id    TEXT UNIQUE NOT NULL,
                    username    TEXT UNIQUE NOT NULL,
                    email       TEXT UNIQUE NOT NULL,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS skill_level    VARCHAR(20) DEFAULT NULL,
                    ADD COLUMN IF NOT EXISTS calibrated     BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS tactical_rating INTEGER DEFAULT NULL
                """
            )
        conn.commit()
        log.info("Database migrations completed successfully")
    except Exception:
        conn.rollback()
        log.exception("Database migration failed")
        raise
    finally:
        database.connection_pool.putconn(conn)
