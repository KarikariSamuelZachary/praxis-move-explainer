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
            cur.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'users'
                          AND column_name = 'username'
                    ) THEN
                        ALTER TABLE users ALTER COLUMN username DROP NOT NULL;
                    END IF;
                END $$;
                """
            )
            cur.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_username_key")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS woodpecker_sets (
                    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id      TEXT NOT NULL REFERENCES users(clerk_id),
                    name         TEXT NOT NULL,
                    created_at   TIMESTAMP DEFAULT NOW(),
                    status       TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed')),
                    cycle_number INT DEFAULT 1
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS woodpecker_entries (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    set_id        UUID NOT NULL REFERENCES woodpecker_sets(id) ON DELETE CASCADE,
                    user_id       TEXT NOT NULL REFERENCES users(clerk_id),
                    puzzle_id     TEXT NOT NULL,
                    theme         TEXT NOT NULL,
                    added_at      TIMESTAMP DEFAULT NOW(),
                    mastered_at   TIMESTAMP,
                    is_mastered   BOOLEAN DEFAULT FALSE,
                    source_reason TEXT CHECK (
                        source_reason IN (
                            'wrong_answer',
                            'slow_solution',
                            'hint_used',
                            'coach_recommended'
                        )
                    )
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS woodpecker_attempts (
                    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    entry_id         UUID NOT NULL REFERENCES woodpecker_entries(id) ON DELETE CASCADE,
                    user_id          TEXT NOT NULL,
                    cycle_number     INT NOT NULL,
                    solved_correctly BOOLEAN NOT NULL,
                    time_taken_ms    INT NOT NULL,
                    attempted_at     TIMESTAMP DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_woodpecker_entries_user_id
                    ON woodpecker_entries(user_id)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_woodpecker_entries_user_mastered
                    ON woodpecker_entries(user_id, is_mastered)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_woodpecker_attempts_entry_cycle
                    ON woodpecker_attempts(entry_id, cycle_number)
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
