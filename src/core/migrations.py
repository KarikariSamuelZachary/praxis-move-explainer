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
                    ADD COLUMN IF NOT EXISTS tactical_rating INTEGER DEFAULT NULL,
                    ADD COLUMN IF NOT EXISTS endgame_trainer_rating INTEGER DEFAULT NULL
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
                    user_id       TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
                    user_id     TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
                    user_id     TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE ON UPDATE CASCADE,
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
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
                    status              TEXT NOT NULL DEFAULT 'queued'
                                        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
                    lichess_username    TEXT,
                    chesscom_username   TEXT,
                    requested_limit     INTEGER NOT NULL DEFAULT 100,
                    imported_count      INTEGER NOT NULL DEFAULT 0,
                    total_games         INTEGER NOT NULL DEFAULT 0,
                    error_message       TEXT,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    started_at          TIMESTAMPTZ,
                    completed_at        TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                """
                ALTER TABLE opponent_import_jobs
                    ADD COLUMN IF NOT EXISTS total_games INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS opponent_prep_ready BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS repertoire_index_status TEXT NOT NULL DEFAULT 'queued',
                    ADD COLUMN IF NOT EXISTS repertoire_indexed_games INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS repertoire_total_games INTEGER NOT NULL DEFAULT 0
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_games (
                    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
            # The sparring/lookup queries filter
            # `LOWER(opponent_username) = LOWER(%s)`, which cannot use the
            # plain-column index above. This expression index keeps those
            # reads off a full-table scan (measured ~165ms seq scan on the
            # dev corpus before the index existed).
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_games_username_lower
                    ON opponent_games(
                        requested_by_user_id,
                        provider,
                        LOWER(opponent_username)
                    )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_repertoire_moves (
                    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    opponent_game_id     UUID NOT NULL REFERENCES opponent_games(id) ON DELETE CASCADE,
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
            # Same LOWER() problem as opponent_games: pick_repertoire_move /
            # pick_near_repertoire_moves filter the username case-insensitively.
            # This was the worst offender (measured ~147-239ms seq scans of
            # 154k rows per out-of-book sparring move).
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_repertoire_username_lower
                    ON opponent_repertoire_moves(
                        requested_by_user_id,
                        provider,
                        LOWER(opponent_username),
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
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
                    requested_by_user_id TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
            # Trap lookups filter the username case-insensitively too.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_game_blunders_username_lower
                    ON opponent_game_blunders(
                        requested_by_user_id,
                        provider,
                        LOWER(opponent_username)
                    )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_game_blunders_analysis_id
                    ON opponent_game_blunders(analysis_id)
                """
            )
            # --- opponent profile snapshots -------------------------------
            # Immutable-at-read-time summary of the imported opponent corpus.
            # The snapshot is replaced after every successful import so the
            # opponent-prep page does not reparse every PGN on each request.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS opponent_profile_snapshots (
                    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    requested_by_user_id     TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE ON UPDATE CASCADE,
                    provider                 TEXT NOT NULL CHECK (provider IN ('lichess', 'chesscom')),
                    opponent_username        TEXT NOT NULL,
                    game_count               INTEGER NOT NULL DEFAULT 0,
                    rating                   INTEGER NOT NULL DEFAULT 0,
                    ratings_by_time_class   JSONB,
                    playing_style            TEXT,
                    preferred_time_control  TEXT,
                    time_control_distribution JSONB,
                    opening_results         JSONB,
                    openings_lost_against   JSONB NOT NULL DEFAULT '[]'::jsonb,
                    avatar_url               TEXT,
                    verified                 BOOLEAN NOT NULL DEFAULT FALSE,
                    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (requested_by_user_id, provider, opponent_username)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_opponent_profile_snapshots_user_computed
                    ON opponent_profile_snapshots(requested_by_user_id, computed_at DESC)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_games (
                    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id             TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
                    requested_by_user_id  TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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
                    requested_by_user_id  TEXT NOT NULL REFERENCES users(clerk_id) ON UPDATE CASCADE,
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

            # A Clerk account can be deleted and recreated with the same
            # email address. The onboarding reconciliation path then renames
            # users.clerk_id. Every FK that stores that identity must cascade
            # the key update; otherwise PostgreSQL's default NO ACTION rule
            # rejects the rename as soon as any child row exists. The
            # migration is idempotent so existing databases get the same
            # behavior as fresh installs.
            user_fk_cascade_migrations = (
                ("woodpecker_entries", "woodpecker_entries_user_id_fkey", "user_id", ""),
                (
                    "tactical_rating_history",
                    "tactical_rating_history_user_id_fkey",
                    "user_id",
                    "",
                ),
                ("repertoires", "repertoires_user_id_fkey", "user_id", "ON DELETE CASCADE"),
                (
                    "opponent_import_jobs",
                    "opponent_import_jobs_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
                (
                    "opponent_games",
                    "opponent_games_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
                (
                    "opponent_repertoire_moves",
                    "opponent_repertoire_moves_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
                (
                    "opponent_analysis_jobs",
                    "opponent_analysis_jobs_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
                (
                    "opponent_game_analysis",
                    "opponent_game_analysis_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
                (
                    "opponent_game_blunders",
                    "opponent_game_blunders_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
                (
                    "opponent_profile_snapshots",
                    "opponent_profile_snapshots_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "ON DELETE CASCADE",
                ),
                ("user_games", "user_games_user_id_fkey", "user_id", ""),
                (
                    "weakness_profile_jobs",
                    "weakness_profile_jobs_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
                (
                    "weakness_profile_moves",
                    "weakness_profile_moves_requested_by_user_id_fkey",
                    "requested_by_user_id",
                    "",
                ),
            )
            for table_name, constraint_name, column_name, delete_clause in user_fk_cascade_migrations:
                cur.execute(
                    f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM information_schema.referential_constraints rc
                            JOIN information_schema.table_constraints tc
                              ON tc.constraint_schema = rc.constraint_schema
                             AND tc.constraint_name = rc.constraint_name
                            WHERE tc.constraint_schema = current_schema()
                              AND tc.table_name = '{table_name}'
                              AND tc.constraint_name = '{constraint_name}'
                              AND rc.update_rule = 'CASCADE'
                        ) THEN
                            ALTER TABLE {table_name}
                                DROP CONSTRAINT IF EXISTS {constraint_name};
                            ALTER TABLE {table_name}
                                ADD CONSTRAINT {constraint_name}
                                FOREIGN KEY ({column_name})
                                REFERENCES users(clerk_id)
                                {delete_clause}
                                ON UPDATE CASCADE;
                        END IF;
                    END $$;
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

            # --- endgame trainer --------------------------------------------
            # Static content library for the Endgame Trainer: topics are the
            # named theoretical endings shown on the Endgames library page,
            # positions are the drillable FEN variants under each topic.
            # Seed data is loaded out-of-band (like puzzles); these tables
            # hold no per-user state — session/progress tracking lives in
            # separate tables added with the training feature itself.
            #
            # category is a single piece-type/matchup tag for library
            # filtering, enforced with an inline CHECK (the same TEXT + CHECK
            # style as repertoires.color / opponent_import_jobs.status — this
            # schema does not use native PG enums). Values:
            #   pure_pawn     kings + pawns only
            #   knight        knight endings, no other piece types
            #                 (N vs P, N+P vs K, NN vs K)
            #   bishop        single-bishop endings (B+P vs K, B vs P)
            #   bishop_bishop bishop vs bishop (same- and opposite-colored)
            #   bishop_knight bishop & knight (B vs N; KBN vs K mate)
            #   rook          rook endings (R+P vs R, R vs P — Lucena,
            #                 Philidor, etc.)
            #   bishop_rook   rook & bishop matchups (R vs B, RB vs R)
            #   knight_rook   rook & knight matchups (R vs N, RN vs R)
            #   queen         queen endings, no other piece types
            #                 (Q vs P, Q+P vs Q)
            #   multi_piece   everything else (Q vs R, Q vs R+B, two-rook
            #                 endings, ...)
            #
            # difficulty_rating uses the same scale as puzzles.rating (plain
            # INTEGER, Lichess-style ~400-3000) so the Elo expected-score
            # calculation against users.endgame_trainer_rating can reuse the
            # existing rating math. sort_order is NULL-able: topics without a
            # curated position sort after ordered ones.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS endgame_topics (
                    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name              TEXT NOT NULL UNIQUE,
                    category          TEXT NOT NULL CHECK (category IN (
                        'pure_pawn',
                        'knight',
                        'bishop',
                        'bishop_bishop',
                        'bishop_knight',
                        'rook',
                        'bishop_rook',
                        'knight_rook',
                        'queen',
                        'multi_piece'
                    )),
                    description       TEXT NOT NULL,
                    difficulty_rating INTEGER NOT NULL,
                    sort_order        INTEGER,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # fen stores the full six-field FEN, NOT the four-field
            # normalization used by repertoire_positions: the halfmove clock
            # changes the tablebase outcome under the 50-move rule, so two
            # positions that differ only in counters are distinct drill
            # variants here. Side to move is deliberately NOT a column — it
            # is field 2 of the FEN ('w'/'b'), and duplicating it would only
            # create a second source of truth that can diverge.
            #
            # is_winning is tablebase-derived at seed time: TRUE = tablebase
            # win for the side to move, FALSE = tablebase draw. Tablebase
            # losses are intentionally excluded from drillable content, so
            # the column is NOT NULL — every seeded row must have a known
            # outcome. UNIQUE (topic_id, fen) gives the seeder an ON CONFLICT
            # target, matching the natural-key style of
            # repertoire_positions(repertoire_id, fen, move).
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS endgame_positions (
                    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    topic_id   UUID NOT NULL REFERENCES endgame_topics(id) ON DELETE CASCADE,
                    fen        TEXT NOT NULL,
                    is_winning BOOLEAN NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (topic_id, fen)
                )
                """
            )
            # Library page query path: filter by category, order by
            # sort_order. Matches the composite-index convention of
            # idx_repertoire_positions_repertoire_due.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_endgame_topics_category_sort
                    ON endgame_topics(category, sort_order)
                """
            )
            # FK lookup path for loading a topic's positions. Matches the
            # FK-index convention of idx_opponent_game_analysis_game_id.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_endgame_positions_topic_id
                    ON endgame_positions(topic_id)
                """
            )
            # Common-mistake explanations for failed drills: authored prose
            # attached to a FAILED session result, keyed at the TOPIC level
            # by the state machine's failure category -- NOT per individual
            # FEN. Rationale: failures are patterns ("you threw the win
            # away in this type of ending"), not properties of one seeded
            # variant, and per-position content would not scale once a
            # topic carries 100+ drill variants.
            #
            # failure_type vocabulary mirrors the Python
            # EndgameFailureCategory enum (services/endgame_session.py) --
            # the same DB-CHECK <-> Python-constant coupling as
            # endgame_topics.category. UNIQUE (topic_id, failure_type)
            # encodes "exactly one authored explanation per topic per
            # failure type": it gives the seeder an idempotent ON CONFLICT
            # target (same natural-key style as endgame_positions) and
            # keeps retrieval a single-row lookup. Message variety across
            # repeat failures would later be a relax-only change (drop the
            # constraint, have the lookup pick one of several rows at
            # random); nothing else depends on the uniqueness.
            #
            # Content is optional by design: most topics carry no rows for
            # a long time, and services/endgame_session.py's
            # attach_common_mistake() degrades a missing row to "no
            # explanation" rather than an error.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS endgame_common_mistakes (
                    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    topic_id     UUID NOT NULL REFERENCES endgame_topics(id) ON DELETE CASCADE,
                    failure_type TEXT NOT NULL CHECK (failure_type IN (
                        'threw_away_win',
                        'blundered_into_loss',
                        'lost_the_draw',
                        'ran_out_of_moves'
                    )),
                    explanation  TEXT NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (topic_id, failure_type)
                )
                """
            )
            # FK-index convention, mirrors idx_endgame_positions_topic_id.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_endgame_common_mistakes_topic_id
                    ON endgame_common_mistakes(topic_id)
                """
            )
            # Link a sourced drill position back to the Lichess puzzle it came
            # from (NULL for curated rows). This is what lets the prefetch
            # worker replay puzzles.moves and warm the tablebase cache with the
            # solution line at drill-selection time. Nullable because the
            # curated Lucena rows have no puzzle provenance.
            cur.execute(
                """
                ALTER TABLE endgame_positions
                    ADD COLUMN IF NOT EXISTS source_puzzle_id TEXT
                """
            )
            # Persistent cache for Lichess tablebase fallback answers.
            # services/tablebase.py is deliberately database-free; a
            # PostgresProbeCache adapter (services/tablebase_cache.py) is
            # registered at app startup and consulted only on the fallback
            # path. Without it, every move of a 6-7-man sourced drill re-asks
            # tablebase.lichess.ovh; with it, each distinct position is paid
            # once per deployment and repeated positions across users/moves
            # are instant. Not per-user state: a tablebase verdict is a fact,
            # identical for every user.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tablebase_probe_cache (
                    fen_key    TEXT PRIMARY KEY,
                    outcome    TEXT NOT NULL CHECK (outcome IN ('win', 'draw', 'loss')),
                    wdl        SMALLINT NOT NULL,
                    dtz        INTEGER,
                    source     TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            # --- endgame woodpecker queue -----------------------------------
            # A DELIBERATELY SEPARATE review queue for failed Endgame Trainer
            # drills -- not a nullable endgame_position_id bolted onto
            # woodpecker_entries. The puzzle table's every route treats
            # `puzzle_id` as its identity key: the duplicate guard and
            # INSERT (POST /entries), the due/count predicates (GET /queue),
            # and the attempt endpoint's client-asserted `solved_correctly`
            # contract. Serving both types from one table would force a
            # type branch into all of them (plus a can't-be-NOT-NULL
            # migration on puzzle_id and a two-way CHECK), with zero logic
            # shared beyond the FSRS block -- which is already shared as
            # core/fsrs.py pure functions. Two tables keep each queue's
            # invariants (NOT NULL key, FK target, caps, grading contract)
            # intact and make "the puzzle queue never returns endgame rows"
            # structural rather than a WHERE clause someone can forget.
            #
            # FSRS column shape is copied from woodpecker_entries verbatim
            # so card_from_row()/rating_for()/is_lapse()/is_mastered() and
            # the shared Scheduler work unchanged; state is the INTEGER
            # FSRS enum (1=Learning, 2=Review, 3=Relearning).
            #
            # position_id is a real FK (unlike the puzzle queue's TEXT
            # puzzle_id, which has no FK to the 5.8M-row puzzles table):
            # endgame_positions is small and deleting a position must purge
            # its queue rows. user_id cascades on user deletion so account
            # teardown is not blocked by queue rows (repertoires use the
            # same convention).
            #
            # Entries are created ONLY by the server on a FAILED trainer
            # move (routers/endgames.py), never by a public /entries POST:
            # unlike a client-asserted puzzle miss, an endgame miss is a
            # tablebase-verified fact, and the queue should not be
            # fabricatable. Consequently the puzzle queue's free-tier
            # daily/active caps are NOT mirrored here: those gate a
            # user-initiated add path, while an automatic capture that
            # silently dropped a real failure would be worse than a few
            # extra cards.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS endgame_woodpecker_entries (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id       TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE ON UPDATE CASCADE,
                    position_id   UUID NOT NULL REFERENCES endgame_positions(id) ON DELETE CASCADE,
                    theme         TEXT NOT NULL,
                    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    mastered_at   TIMESTAMPTZ,
                    is_mastered   BOOLEAN NOT NULL DEFAULT FALSE,
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
            # One ACTIVE card per (user, position), the DB-layer backstop
            # for the capture path's duplicate guard: a mastered card is
            # excluded, so failing the same drill again after mastery opens
            # a fresh card (same semantics as the puzzle queue's
            # is_mastered = FALSE guard). Partial because mastery is the
            # only state where a duplicate pair is legitimate.
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_endgame_woodpecker_active_entry
                    ON endgame_woodpecker_entries(user_id, position_id)
                    WHERE is_mastered = FALSE
                """
            )
            # One row per COMPLETED review replay (not per move): the
            # endgame review is a multi-request full-resolution drill, so
            # solved_correctly/time_taken_ms land only on the resolving
            # move, exactly like the puzzle queue's one-row-per-solve.
            # resolution / failure_category mirror the trainer's richer
            # vocabulary so the attempt log can be audited without
            # re-deriving tablebase verdicts.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS endgame_woodpecker_attempts (
                    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    entry_id         UUID NOT NULL REFERENCES endgame_woodpecker_entries(id) ON DELETE CASCADE,
                    user_id          TEXT NOT NULL,
                    solved_correctly BOOLEAN NOT NULL,
                    time_taken_ms    INT NOT NULL,
                    resolution       TEXT CHECK (resolution IN (
                        'checkmate',
                        'stalemate',
                        'insufficient_material',
                        'fifty_move_rule',
                        'promotion'
                    )),
                    failure_category TEXT CHECK (failure_category IN (
                        'threw_away_win',
                        'blundered_into_loss',
                        'lost_the_draw',
                        'ran_out_of_moves'
                    )),
                    attempted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # Due/count lookup path: mirrors idx_woodpecker_entries_user_mastered's
            # role, ordered by due like both queue readers.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_endgame_woodpecker_entries_user_due
                    ON endgame_woodpecker_entries(user_id, due)
                """
            )
            # FK-index convention, mirrors idx_endgame_positions_topic_id.
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_endgame_woodpecker_attempts_entry_id
                    ON endgame_woodpecker_attempts(entry_id)
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
