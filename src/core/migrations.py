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

            # --- opponent game ingestion ------------------------------------
            # Public games imported for training against an opponent profile.
            # Kept separate from user-owned/review games so future training
            # features can query opponent corpora without mixing ownership.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_import_jobs (
                    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id),
                    status              TEXT NOT NULL DEFAULT 'queued'
                                        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
                    lichess_username    TEXT,
                    chesscom_username   TEXT,
                    requested_limit     INTEGER NOT NULL DEFAULT 100,
                    imported_count      INTEGER NOT NULL DEFAULT 0,
                    error_message       TEXT,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at          TIMESTAMPTZ,
                    completed_at        TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_games (
                    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id),
                    import_job_id       UUID REFERENCES opponent_import_jobs(id) ON DELETE SET NULL,
                    provider            TEXT NOT NULL CHECK (provider IN ('lichess', 'chesscom')),
                    opponent_username   TEXT NOT NULL,
                    game_url            TEXT NOT NULL,
                    pgn                 TEXT NOT NULL,
                    white_player        JSONB NOT NULL DEFAULT '{}'::jsonb,
                    black_player        JSONB NOT NULL DEFAULT '{}'::jsonb,
                    result              TEXT NOT NULL DEFAULT '',
                    end_time            BIGINT NOT NULL DEFAULT 0,
                    time_class          TEXT NOT NULL DEFAULT '',
                    raw_summary         JSONB NOT NULL DEFAULT '{}'::jsonb,
                    imported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (requested_by_user_id, provider, opponent_username, game_url)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_games_lookup
                    ON opponent_games(requested_by_user_id, provider, opponent_username)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_repertoire_moves (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    opponent_game_id     UUID NOT NULL REFERENCES opponent_games(id) ON DELETE CASCADE,
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id),
                    provider             TEXT NOT NULL CHECK (provider IN ('lichess', 'chesscom')),
                    opponent_username    TEXT NOT NULL,
                    position_key         TEXT NOT NULL,
                    move_uci             TEXT NOT NULL,
                    move_san             TEXT NOT NULL DEFAULT '',
                    ply_index            INTEGER NOT NULL,
                    played_color         TEXT NOT NULL CHECK (played_color IN ('white', 'black')),
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (opponent_game_id, ply_index)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_repertoire_lookup
                    ON opponent_repertoire_moves(
                        requested_by_user_id,
                        provider,
                        opponent_username,
                        position_key
                    )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_import_jobs_user_created
                    ON opponent_import_jobs(requested_by_user_id, created_at DESC)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_games (
                    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id             TEXT NOT NULL REFERENCES users(clerk_id),
                    provider            TEXT CHECK (provider IN ('lichess', 'chesscom', 'pgn')),
                    source_username     TEXT,
                    game_url            TEXT NOT NULL DEFAULT '',
                    pgn                 TEXT NOT NULL,
                    white_player        JSONB NOT NULL DEFAULT '{}'::jsonb,
                    black_player        JSONB NOT NULL DEFAULT '{}'::jsonb,
                    result              TEXT NOT NULL DEFAULT '',
                    end_time            BIGINT NOT NULL DEFAULT 0,
                    time_class          TEXT NOT NULL DEFAULT '',
                    imported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (user_id, provider, game_url)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_games_user_time
                    ON user_games(user_id, end_time DESC, imported_at DESC)
                """
            )

            # --- weakness profile analysis ----------------------------------
            # Generic corpus analysis result tables. `source_type` lets this
            # profile either opponent imports now or user-owned games later.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS weakness_profile_jobs (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id  TEXT NOT NULL REFERENCES users(clerk_id),
                    source_type           TEXT NOT NULL CHECK (source_type IN ('opponent', 'user')),
                    provider              TEXT,
                    opponent_username     TEXT,
                    status                TEXT NOT NULL DEFAULT 'queued'
                                          CHECK (status IN ('queued', 'running', 'completed', 'failed')),
                    requested_limit       INTEGER NOT NULL DEFAULT 50,
                    analyzed_games_count  INTEGER NOT NULL DEFAULT 0,
                    analyzed_moves_count  INTEGER NOT NULL DEFAULT 0,
                    mistake_count         INTEGER NOT NULL DEFAULT 0,
                    blunder_count         INTEGER NOT NULL DEFAULT 0,
                    summary               JSONB NOT NULL DEFAULT '{}'::jsonb,
                    error_message         TEXT,
                    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at            TIMESTAMPTZ,
                    completed_at          TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS weakness_profile_moves (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    profile_job_id        UUID NOT NULL REFERENCES weakness_profile_jobs(id) ON DELETE CASCADE,
                    requested_by_user_id  TEXT NOT NULL REFERENCES users(clerk_id),
                    source_type           TEXT NOT NULL CHECK (source_type IN ('opponent', 'user')),
                    source_game_id        UUID,
                    game_url              TEXT NOT NULL DEFAULT '',
                    provider              TEXT,
                    opponent_username     TEXT,
                    phase                 TEXT NOT NULL,
                    move_bucket           TEXT NOT NULL,
                    move_number           INTEGER NOT NULL,
                    color                 TEXT NOT NULL,
                    san                   TEXT NOT NULL,
                    classification        TEXT NOT NULL,
                    cp_loss               INTEGER NOT NULL,
                    mistake_type          TEXT NOT NULL,
                    fen_before            TEXT NOT NULL,
                    fen_after             TEXT NOT NULL,
                    best_move_san         TEXT NOT NULL DEFAULT '',
                    best_move_uci         TEXT NOT NULL DEFAULT '',
                    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_weakness_profile_jobs_user_created
                    ON weakness_profile_jobs(requested_by_user_id, created_at DESC)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_weakness_profile_moves_job_loss
                    ON weakness_profile_moves(profile_job_id, cp_loss DESC)
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
