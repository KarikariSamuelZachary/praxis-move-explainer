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

            # email is no longer required at insert time — Clerk's
            # currentUser() can briefly return null right after an SSO
            # sign-up, and the onboarding POST can land before the email
            # is available. clerk_id is the real primary key; email is a
            # best-effort reconciliation field. Dropping the NOT NULL
            # lets the onboarding upsert succeed without it, and a
            # later request (or the auth webhook) can backfill it.
            cur.execute("ALTER TABLE users ALTER COLUMN email DROP NOT NULL")

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

            # --- repertoires ------------------------------------------------
            # User-owned opening repertoires. Each repertoire is a named
            # collection of positions for one color, and owns N
            # repertoire_positions rows which carry per-position FSRS
            # scheduling state — the same FSRS column shape as
            # woodpecker_entries, but with a TEXT `state` (default 'Learning')
            # instead of an INTEGER FSRS State enum to keep raw trainer state
            # human-readable here.
            #
            # user_id tracks users(clerk_id) as TEXT — same convention as
            # woodpecker_entries / opponent_games / every other user FK in
            # this schema. ON DELETE CASCADE so deleting a Clerk user removes
            # their repertoires and (via the secondary CASCADE) all positions.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS repertoires (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id     TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
                    name        TEXT NOT NULL,
                    color       TEXT NOT NULL CHECK (color IN ('white', 'black')),
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS repertoire_positions (
                    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    repertoire_id  UUID NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
                    -- fen is normalized to the first 4 FEN fields only
                    -- (board, side-to-move, castling rights, en passant
                    -- square). The halfmove clock and fullmove number MUST
                    -- be stripped by the writer before INSERT, so two
                    -- positions that differ only in those counters collapse
                    -- to the same row. The key includes `move` so a single
                    -- position can hold SEVERAL saved moves — a repertoire
                    -- that diverges (e.g. both Nf3 and Be2 prepared from
                    -- the same position) stores one row per branch.
                    fen            TEXT NOT NULL,
                    -- UCI format (e.g. "e2e4", "e7e8q"); NOT SAN.
                    move           TEXT NOT NULL,
                    due            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    stability      DOUBLE PRECISION,
                    difficulty     DOUBLE PRECISION,
                    state          TEXT NOT NULL DEFAULT 'Learning',
                    step           INTEGER,
                    reps           INTEGER NOT NULL DEFAULT 0,
                    lapses         INTEGER NOT NULL DEFAULT 0,
                    last_review    TIMESTAMPTZ,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (repertoire_id, fen, move)
                )
                """
            )
            # --- repertoire_positions branch migration (existing DBs) --------
            # Databases created before diverging-branch support carry
            # UNIQUE (repertoire_id, fen) (default constraint name
            # ..._repertoire_id_fen_key), which silently OVERWRITES a
            # second saved move from the same position via the upsert's
            # ON CONFLICT DO UPDATE. Replace it with
            # UNIQUE (repertoire_id, fen, move). Existing data has at
            # most one row per (repertoire_id, fen), so the new
            # constraint can never fail to apply; the drop is guarded
            # by the old constraint's presence so re-runs are no-ops
            # (fresh DBs never had it — their CREATE TABLE above
            # already ships the three-column key). A plain
            # (repertoire_id, fen) index replaces the lookup path the
            # dropped unique constraint used to cover (delete-guard
            # child detection, queue reconstruction, etc.).
            cur.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.table_constraints
                        WHERE constraint_name =
                              'repertoire_positions_repertoire_id_fen_key'
                          AND table_name = 'repertoire_positions'
                    ) THEN
                        ALTER TABLE repertoire_positions
                            DROP CONSTRAINT
                            repertoire_positions_repertoire_id_fen_key;
                    END IF;
                END $$;
                """
            )
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.table_constraints
                        WHERE constraint_name =
                              'repertoire_positions_repertoire_id_fen_move_key'
                          AND table_name = 'repertoire_positions'
                    ) THEN
                        ALTER TABLE repertoire_positions
                            ADD CONSTRAINT
                            repertoire_positions_repertoire_id_fen_move_key
                            UNIQUE (repertoire_id, fen, move);
                    END IF;
                END $$;
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_repertoire_positions_repertoire_fen
                    ON repertoire_positions(repertoire_id, fen)
                """
            )
            # Enforce the TEXT `state` vocabulary at the DB layer:
            # woodpecker_entries.state has CHECK (state IN (1, 2, 3))
            # for its INTEGER FSRS state; repertoire_positions.state
            # is TEXT and stores the FSRS State enum NAME, so the
            # parallel check is on the three legal names. This is the
            # only thing stopping a bad write from corrupting the
            # column with an arbitrary string.
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.check_constraints
                        WHERE constraint_name = 'repertoire_positions_state_check'
                    ) THEN
                        ALTER TABLE repertoire_positions
                            ADD CONSTRAINT repertoire_positions_state_check
                            CHECK (state IN ('Learning', 'Review', 'Relearning'));
                    END IF;
                END $$;
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_repertoire_positions_repertoire_due
                    ON repertoire_positions(repertoire_id, due)
                """
            )

            # --- repertoire training sessions -------------------------------
            # Session-level training log: ONE row per training/review
            # session against a repertoire. `mode` distinguishes a full
            # re-train pass ('train') from a spaced-review pass ('review').
            # `completed_at` is NULL for in-progress/abandoned sessions;
            # non-NULL marks a finished session and is what the list
            # endpoint ranks by (latest completed session per repertoire).
            # positions_correct / positions_total carry the raw score so
            # last_score_percent can be derived without a second join.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS repertoire_training_sessions (
                    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    repertoire_id     UUID NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
                    mode              TEXT NOT NULL CHECK (mode IN ('review', 'train')),
                    positions_total   INTEGER NOT NULL,
                    positions_correct INTEGER NOT NULL DEFAULT 0,
                    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at      TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_repertoire_training_sessions_repertoire_completed
                    ON repertoire_training_sessions(repertoire_id, completed_at DESC)
                """
            )
            # Enforce positions_total > 0 at the DB layer. A zero-total
            # session row would make last_score_percent a divide-by-zero
            # in GET /api/repertoires (positions_correct * 100.0 /
            # positions_total) and would also be semantically bogus: a
            # training session against an empty position set is a
            # client-side no-op, not a server-side row. Writers must
            # refuse to insert one (the start endpoint returns 400
            # before INSERT when positions is empty); this CHECK is the
            # backstop so a buggy writer or future code path can't
            # land a zero-total row that would later poison the score
            # computation. Mirrors the DO $$ idempotent guard style
            # used by woodpecker_entries_state_check and
            # repertoire_positions_state_check above — the CREATE
            # TABLE above is IF NOT EXISTS, so an existing install
            # that predates this constraint needs the ADD CONSTRAINT
            # path rather than failing on a duplicate CREATE.
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.check_constraints
                        WHERE constraint_name = 'repertoire_training_sessions_positions_total_check'
                    ) THEN
                        ALTER TABLE repertoire_training_sessions
                            ADD CONSTRAINT repertoire_training_sessions_positions_total_check
                            CHECK (positions_total > 0);
                    END IF;
                END $$;
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

            # --- opponent game analysis (Stockfish blunder classification) ---
            #
            # Three tables that persist the per-opponent move-classification
            # pass: a job-state row per opponent (for polling/progress), a
            # per-game analysis marker (the "this game has been scored"
            # sentinel), and the per-blunder detail rows.
            #
            # The per-game marker (opponent_game_analysis) is the crucial
            # table: it is written EVEN IF the game produced zero blunders.
            # Using presence-in-this-table (rather than presence-in-the-
            # blunders-table) as the "analyzed" sentinel is what makes
            # re-runs skip already-analyzed games correctly — a zero-blunder
            # game would otherwise be re-analyzed forever if the system
            # checked the blunders table for its existence.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_analysis_jobs (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id),
                    provider             TEXT NOT NULL CHECK (provider IN ('lichess', 'chesscom')),
                    opponent_username    TEXT NOT NULL,
                    status               TEXT NOT NULL DEFAULT 'idle'
                                         CHECK (status IN ('idle', 'running', 'complete')),
                    started_at           TIMESTAMPTZ,
                    heartbeat_at         TIMESTAMPTZ,
                    analyzed_games       INTEGER NOT NULL DEFAULT 0,
                    total_games          INTEGER NOT NULL DEFAULT 0,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (requested_by_user_id, provider, opponent_username)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_game_analysis (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id),
                    provider             TEXT NOT NULL CHECK (provider IN ('lichess', 'chesscom')),
                    opponent_username    TEXT NOT NULL,
                    game_id              UUID NOT NULL REFERENCES opponent_games(id) ON DELETE CASCADE,
                    status               TEXT NOT NULL CHECK (status IN ('analyzed', 'failed')),
                    analyzed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    error                TEXT,
                    UNIQUE (provider, opponent_username, game_id)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_game_analysis_game_id
                    ON opponent_game_analysis(game_id)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_game_blunders (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id),
                    provider             TEXT NOT NULL CHECK (provider IN ('lichess', 'chesscom')),
                    opponent_username    TEXT NOT NULL,
                    game_id              UUID NOT NULL REFERENCES opponent_games(id) ON DELETE CASCADE,
                    analysis_id          UUID NOT NULL REFERENCES opponent_game_analysis(id) ON DELETE CASCADE,
                    fen                  TEXT NOT NULL,
                    position_key         TEXT NOT NULL,
                    move_number          INTEGER NOT NULL,
                    move_san             TEXT NOT NULL,
                    classification       TEXT NOT NULL CHECK (classification IN ('mistake', 'blunder')),
                    centipawn_loss       INTEGER NOT NULL,
                    analyzed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_game_blunders_lookup
                    ON opponent_game_blunders(
                        requested_by_user_id,
                        provider,
                        opponent_username,
                        position_key
                    )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_game_blunders_analysis_id
                    ON opponent_game_blunders(analysis_id)
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

            # --- puzzles -----------------------------------------------------
            # The puzzles table is seeded out-of-band (see praxis_subset.csv /
            # src/seed_puzzles.py) rather than created here, so only add the
            # indexes when the table already exists. This keeps a fresh deploy
            # (puzzles not yet loaded) from crashing on startup while ensuring
            # every environment that actually serves puzzles filters by rating
            # and theme via indexes instead of 500k-row seq scans.
            cur.execute(
                """
                DO $$
                BEGIN
                    IF to_regclass('public.puzzles') IS NOT NULL THEN
                        CREATE INDEX IF NOT EXISTS idx_puzzles_rating
                            ON puzzles (rating);
                        -- GIN index accelerates the API's
                        -- `themes @> ARRAY[<theme>]::text[]` filter used by
                        -- GET /api/puzzles.
                        CREATE INDEX IF NOT EXISTS idx_puzzles_themes
                            ON puzzles USING GIN (themes);
                    END IF;
                END $$;
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
