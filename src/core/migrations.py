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

            # --- woodpecker_entries -----------------------------------------
            # Per-puzzle FSRS scheduling schema. The legacy cycle-based
            # woodpecker_sets table has been dropped; entries are now linked
            # directly to users(clerk_id) and carry FSRS scheduling columns.
            #
            # FSRS State is an IntEnum (Learning=1, Review=2, Relearning=3),
            # hence `state` is an INTEGER with a CHECK constraint.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS woodpecker_entries (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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
                    ),
                    due          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    stability    DOUBLE PRECISION,
                    difficulty   DOUBLE PRECISION,
                    state        INTEGER NOT NULL DEFAULT 1
                                    CHECK (state IN (1, 2, 3)),
                    step         INTEGER,
                    reps         INTEGER NOT NULL DEFAULT 0,
                    lapses       INTEGER NOT NULL DEFAULT 0,
                    last_review  TIMESTAMPTZ
                )
                """
            )
            # Upgrade existing installs that still carry the legacy set_id
            # column / FK from the old woodpecker_sets model.
            cur.execute(
                "ALTER TABLE woodpecker_entries DROP COLUMN IF EXISTS set_id"
            )
            cur.execute(
                """
                ALTER TABLE woodpecker_entries
                    ADD COLUMN IF NOT EXISTS due         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ADD COLUMN IF NOT EXISTS stability   DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS difficulty  DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS state       INTEGER NOT NULL DEFAULT 1,
                    ADD COLUMN IF NOT EXISTS step        INTEGER,
                    ADD COLUMN IF NOT EXISTS reps        INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS lapses      INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS last_review TIMESTAMPTZ
                """
            )
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.check_constraints
                        WHERE constraint_name = 'woodpecker_entries_state_check'
                    ) THEN
                        ALTER TABLE woodpecker_entries
                            ADD CONSTRAINT woodpecker_entries_state_check
                            CHECK (state IN (1, 2, 3));
                    END IF;
                END $$;
                """
            )

            # Drop the legacy cycle-based sets table. CASCADE removes any
            # residual FK from woodpecker_entries.set_id if it still exists.
            cur.execute("DROP TABLE IF EXISTS woodpecker_sets CASCADE")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS woodpecker_attempts (
                    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    entry_id         UUID NOT NULL REFERENCES woodpecker_entries(id) ON DELETE CASCADE,
                    user_id          TEXT NOT NULL,
                    solved_correctly BOOLEAN NOT NULL,
                    time_taken_ms    INT NOT NULL,
                    attempted_at     TIMESTAMP DEFAULT NOW()
                )
                """
            )
            # Upgrade existing installs that still carry the legacy
            # cycle_number column / index from the removed sets cycle model.
            cur.execute("DROP INDEX IF EXISTS idx_woodpecker_attempts_entry_cycle")
            cur.execute(
                "ALTER TABLE woodpecker_attempts DROP COLUMN IF EXISTS cycle_number"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tactical_rating_history (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id     TEXT NOT NULL REFERENCES users(clerk_id),
                    old_rating  INT NOT NULL,
                    new_rating  INT NOT NULL,
                    change      INT NOT NULL,
                    puzzle_id   TEXT NOT NULL,
                    solved      BOOLEAN NOT NULL,
                    created_at  TIMESTAMP DEFAULT NOW()
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
                CREATE INDEX IF NOT EXISTS idx_tactical_rating_history_user_id
                    ON tactical_rating_history(user_id)
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
