"""
Resumable, incremental background job that runs Stockfish move-classification
over an opponent's imported games and persists blunders/mistakes per game.

This module reuses the EXACT classification function/module that
/api/analyze -> /api/review uses for single-game Game Review
(``core.game_analyzer.GameAnalyzer.analyze_full_game``).  It does NOT
reimplement blunder/mistake detection — the cp-loss thresholds, the
"book move" ply cutoff, and the classification strings ("mistake",
"blunder", "inaccuracy", …) all come from GameAnalyzer.

Three tables (created in ``core.migrations``):

  * ``opponent_analysis_jobs``   — one row per opponent, tracks job STATE
    (idle / running / complete).  This is what the GET endpoint reads for
    the frontend's "Analyzing his games… 47/124" poll.

  * ``opponent_game_analysis``  — one row per imported game, written EVEN
    IF the game produced zero blunders.  This is the "analyzed" sentinel:
    the unanalyzed-games query is ``game_id NOT IN opponent_game_analysis``
    (NOT ``NOT IN opponent_game_blunders``), so a zero-blunder game is
    correctly skipped on re-trigger instead of being re-analyzed forever.

  * ``opponent_game_blunders``  — zero or more rows per game.  Only
    ``mistake`` and ``blunder`` classifications are persisted (inaccuracy-
    and-better is skipped per spec).  Each row is FK'd to the game's
    ``opponent_game_analysis`` row.

Trigger flow (inside the existing "Analyze Player" endpoint — the import
search box → POST /api/train/opponent-import):

  1. The existing import/refresh runs so the opponent's game set is
     current (``run_opponent_import_job`` in ``services.opponent_import``).
  2. After the import commits, ``try_start_opponent_analysis`` is called.
     In a SINGLE transaction it: SELECTs the ``opponent_analysis_jobs``
     row FOR UPDATE (creating it if absent), computes unanalyzed games via
     one indexed query, checks the status + heartbeat staleness, and
     either flips status→running or no-ops.  The transaction commits
     BEFORE any Stockfish is touched.
  3. If the trigger decided a run should happen,
     ``run_opponent_game_analysis`` is called synchronously (it runs in
     the same background thread as the import — the endpoint has already
     returned to the HTTP client).

Worker contract (``run_opponent_game_analysis``):

  * Fetches unanalyzed game ids (the SAME query as the trigger).
  * Processes ONE GAME AT A TIME: run classification, write that game's
    ``opponent_game_blunders`` rows (if any) and its
    ``opponent_game_analysis`` row, commit, then update
    ``opponent_analysis_jobs.heartbeat_at`` + ``analyzed_games``, commit.
  * A single game's exception is caught → write a ``failed``
    ``opponent_game_analysis`` row with the error → commit → continue.
    One bad game never kills the whole run.
  * When no unanalyzed games remain: set status=complete.
"""
import logging
import os
from io import StringIO
from typing import Any, Dict, List, Optional

import chess
import chess.pgn
from psycopg2.extras import RealDictCursor

from core import database
from core.game_analyzer import GameAnalyzer
from engines.stockfish_engine import StockfishEngine
from llms.mock_explainer import MockExplainer

log = logging.getLogger(__name__)

# Heartbeat staleness threshold. A running job whose heartbeat_at is older
# than this is treated as crashed and reclaimed by the next trigger. 5 min
# is long enough that a slow game (deep Stockfish search) won't trigger a
# false reclaim, yet short enough that a genuinely crashed worker is
# restarted within a reasonable window.
HEARTBEAT_STALE_SECONDS = 300

# Only "mistake" and "blunder" are persisted to opponent_game_blunders.
# Inaccuracy-and-better is skipped per spec — the table is for the
# opponent's meaningful errors, not a full move-by-move log.
BLUNDER_CLASSIFICATIONS = {"mistake", "blunder"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def try_start_opponent_analysis(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> Dict[str, Any]:
    """Trigger flow: the single-transaction state check + flip.

    Called after the import/refresh has committed (so the opponent's game
    set is current).  Performs:

      1. SELECT the opponent_analysis_jobs row FOR UPDATE (create if
         absent).
      2. Compute unanalyzed games via one indexed query.
      3. Decision:
         - status=running + heartbeat fresh (<5 min): no-op (another run
           is actively in progress).
         - status=running + heartbeat stale (>=5 min): reclaim (crashed
           worker) — proceed to flip.
         - zero unanalyzed games: no-op (nothing to do).
         - otherwise: flip status→running, set started_at=now,
           heartbeat_at=now, total_games=<imported count>,
           analyzed_games=<already-analyzed count>.
      4. COMMIT (before any Stockfish is touched).

    Returns ``{"should_run": bool, "reason": str, "unanalyzed_count": int}``.
    The caller launches the worker iff ``should_run`` is True.
    """
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # --- Step 1: get or create the job row with FOR UPDATE ---
            cur.execute(
                """
                SELECT id::text, status, heartbeat_at,
                       analyzed_games, total_games
                FROM opponent_analysis_jobs
                WHERE requested_by_user_id = %s
                  AND provider = %s
                  AND LOWER(opponent_username) = LOWER(%s)
                FOR UPDATE
                """,
                (requested_by_user_id, provider, opponent_username),
            )
            row = cur.fetchone()

            if row is None:
                cur.execute(
                    """
                    INSERT INTO opponent_analysis_jobs (
                        requested_by_user_id, provider, opponent_username
                    )
                    VALUES (%s, %s, %s)
                    RETURNING id::text, status, heartbeat_at,
                              analyzed_games, total_games
                    """,
                    (requested_by_user_id, provider, opponent_username),
                )
                row = dict(cur.fetchone())
            else:
                row = dict(row)

            # --- Step 2: compute unanalyzed games (the one indexed query) ---
            unanalyzed_ids = _fetch_unanalyzed_game_ids(
                conn,
                requested_by_user_id=requested_by_user_id,
                provider=provider,
                opponent_username=opponent_username,
            )
            already_analyzed_count = _count_analyzed_games(
                conn,
                requested_by_user_id=requested_by_user_id,
                provider=provider,
                opponent_username=opponent_username,
            )
            total_games = len(unanalyzed_ids) + already_analyzed_count

            # --- Step 3: decision ---
            if len(unanalyzed_ids) == 0:
                conn.commit()
                return {
                    "should_run": False,
                    "reason": "no unanalyzed games",
                    "unanalyzed_count": 0,
                }

            status = row["status"]
            heartbeat_at = row["heartbeat_at"]

            if status == "running" and heartbeat_at is not None:
                age_seconds = (heartbeat_at - _db_now(conn)).total_seconds()
                # heartbeat_at - now is negative; flip to positive age
                age_seconds = -age_seconds
                is_fresh = age_seconds < HEARTBEAT_STALE_SECONDS
                if is_fresh:
                    conn.commit()
                    return {
                        "should_run": False,
                        "reason": "fresh running job",
                        "unanalyzed_count": len(unanalyzed_ids),
                    }
                # else: stale — reclaim below
                log.info(
                    "Reclaiming stale analysis job for %s/%s (heartbeat "
                    "age=%.0fs > %ss)",
                    provider, opponent_username,
                    age_seconds, HEARTBEAT_STALE_SECONDS,
                )

            # Flip to running
            cur.execute(
                """
                UPDATE opponent_analysis_jobs
                SET status = 'running',
                    started_at = NOW(),
                    heartbeat_at = NOW(),
                    total_games = %s,
                    analyzed_games = %s
                WHERE id = %s::uuid
                """,
                (total_games, already_analyzed_count, row["id"]),
            )

        conn.commit()
        return {
            "should_run": True,
            "reason": "started",
            "unanalyzed_count": len(unanalyzed_ids),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        database.connection_pool.putconn(conn)


def run_opponent_game_analysis(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    _analyzer: Optional[GameAnalyzer] = None,
) -> None:
    """Worker: process unanalyzed games one at a time.

    Fetches unanalyzed game ids (same query as the trigger), then processes
    each game: run classification via ``GameAnalyzer.analyze_full_game``
    (the EXACT module /api/review uses), write ``opponent_game_blunders``
    rows (if any) + ``opponent_game_analysis`` row, commit, update heartbeat
    + analyzed_games, commit.

    A single game's exception is caught → write a ``failed``
    ``opponent_game_analysis`` row → continue.  One bad game never kills
    the whole run.

    When no unanalyzed games remain: set status=complete.

    ``_analyzer`` is for testing (inject a mock analyzer to avoid running
    real Stockfish).  Production calls omit it and the worker creates its
    own StockfishEngine + GameAnalyzer.
    """
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    engine: Optional[StockfishEngine] = None
    try:
        unanalyzed_ids = _fetch_unanalyzed_game_ids(
            conn,
            requested_by_user_id=requested_by_user_id,
            provider=provider,
            opponent_username=opponent_username,
        )
        if not unanalyzed_ids:
            _set_job_status(
                conn,
                requested_by_user_id=requested_by_user_id,
                provider=provider,
                opponent_username=opponent_username,
                status="complete",
            )
            conn.commit()
            return

        # Create the analyzer (unless injected for testing)
        if _analyzer is not None:
            analyzer = _analyzer
        else:
            engine = StockfishEngine(
                depth=int(os.getenv("REVIEW_DEPTH", "18")),
            )
            engine.start()
            analyzer = GameAnalyzer(engine=engine, explainer=MockExplainer())

        for game_id in unanalyzed_ids:
            try:
                _process_one_game(
                    conn,
                    analyzer,
                    requested_by_user_id=requested_by_user_id,
                    provider=provider,
                    opponent_username=opponent_username,
                    game_id=game_id,
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                log.exception(
                    "Failed to analyze game %s for %s/%s — marking failed",
                    game_id, provider, opponent_username,
                )
                _write_failed_analysis(
                    conn,
                    requested_by_user_id=requested_by_user_id,
                    provider=provider,
                    opponent_username=opponent_username,
                    game_id=game_id,
                    error=str(exc),
                )
                conn.commit()

            _update_heartbeat(
                conn,
                requested_by_user_id=requested_by_user_id,
                provider=provider,
                opponent_username=opponent_username,
            )
            conn.commit()

        # All games processed — check if anything remains (could be zero
        # if all games were processed successfully or marked failed).
        remaining = _fetch_unanalyzed_game_ids(
            conn,
            requested_by_user_id=requested_by_user_id,
            provider=provider,
            opponent_username=opponent_username,
        )
        if not remaining:
            _set_job_status(
                conn,
                requested_by_user_id=requested_by_user_id,
                provider=provider,
                opponent_username=opponent_username,
                status="complete",
            )
            conn.commit()
    except Exception:
        conn.rollback()
        log.exception(
            "Opponent game analysis worker crashed for %s/%s — the job "
            "row will be reclaimable via stale heartbeat",
            provider, opponent_username,
        )
        raise
    finally:
        if engine is not None:
            engine.close()
        database.connection_pool.putconn(conn)


def get_opponent_analysis_status(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> Optional[Dict[str, Any]]:
    """Plain read of the opponent_analysis_jobs row.

    No recomputation of unanalyzed games — the frontend polls this for
    "Analyzing his games… 47/124" and needs only the persisted state.
    Returns None if no job row exists yet (opponent never triggered).
    """
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id::text,
                    provider,
                    opponent_username,
                    status,
                    analyzed_games,
                    total_games,
                    started_at,
                    heartbeat_at
                FROM opponent_analysis_jobs
                WHERE requested_by_user_id = %s
                  AND provider = %s
                  AND LOWER(opponent_username) = LOWER(%s)
                """,
                (requested_by_user_id, provider, opponent_username),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        database.connection_pool.putconn(conn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_unanalyzed_game_ids(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> List[str]:
    """The single indexed query: imported game ids NOT IN
    opponent_game_analysis.

    Both the trigger (``try_start_opponent_analysis``) and the worker
    (``run_opponent_game_analysis``) call this.  The GET endpoint does NOT
    — it reads the persisted job row state only.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT g.id::text AS game_id
            FROM opponent_games g
            WHERE g.requested_by_user_id = %s
              AND g.provider = %s
              AND LOWER(g.opponent_username) = LOWER(%s)
              AND NOT EXISTS (
                  SELECT 1
                  FROM opponent_game_analysis a
                  WHERE a.game_id = g.id
              )
            ORDER BY g.end_time DESC, g.imported_at DESC
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        return [row["game_id"] for row in cur.fetchall()]


def _count_analyzed_games(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> int:
    """Count games that already have an opponent_game_analysis row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)::int
            FROM opponent_game_analysis a
            WHERE a.requested_by_user_id = %s
              AND a.provider = %s
              AND LOWER(a.opponent_username) = LOWER(%s)
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        return cur.fetchone()[0]


def _db_now(conn) -> Any:
    """Get the current timestamp from the DB server (single source of
    truth for heartbeat staleness comparisons)."""
    with conn.cursor() as cur:
        cur.execute("SELECT NOW()")
        return cur.fetchone()[0]


def _process_one_game(
    conn,
    analyzer: GameAnalyzer,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    game_id: str,
) -> None:
    """Analyze one game and write its opponent_game_analysis row +
    opponent_game_blunders rows.

    Does NOT commit — the caller commits on success or rolls back on
    exception (then writes a failed analysis row).
    """
    game_data = _fetch_game_data(
        conn,
        requested_by_user_id=requested_by_user_id,
        game_id=game_id,
    )
    if game_data is None:
        raise ValueError(f"Game {game_id} not found")

    opponent_color = _determine_opponent_color(
        pgn=game_data["pgn"],
        white_player=game_data["white_player"],
        black_player=game_data["black_player"],
        opponent_username=opponent_username,
    )
    if opponent_color is None:
        raise ValueError(
            f"Could not determine opponent color for game {game_id} "
            f"(opponent username {opponent_username!r} does not match "
            f"either PGN player header)"
        )

    review_rows = analyzer.analyze_full_game(
        game_data["pgn"],
        target_color=opponent_color,
        include_explanations=False,
    )

    blunders: List[Dict[str, Any]] = []
    for r in review_rows:
        if r.get("san") == "Start":
            continue
        classification = r["classification"]
        if classification not in BLUNDER_CLASSIFICATIONS:
            continue
        fen_before = r.get("fen_before") or r["fen"]
        blunders.append(
            {
                "fen": fen_before,
                "position_key": _position_key_from_fen(fen_before),
                "move_number": int(r.get("move_number") or 0),
                "move_san": r["san"],
                "classification": classification,
                "centipawn_loss": int(r["cp_loss"]),
            }
        )

    _write_game_results(
        conn,
        requested_by_user_id=requested_by_user_id,
        provider=provider,
        opponent_username=opponent_username,
        game_id=game_id,
        blunders=blunders,
    )


def _fetch_game_data(
    conn,
    *,
    requested_by_user_id: str,
    game_id: str,
) -> Optional[Dict[str, Any]]:
    """Fetch PGN + player JSONB for a single game."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT pgn, white_player, black_player
            FROM opponent_games
            WHERE id = %s::uuid AND requested_by_user_id = %s
            """,
            (game_id, requested_by_user_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _write_game_results(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    game_id: str,
    blunders: List[Dict[str, Any]],
) -> None:
    """Upsert the opponent_game_analysis row (status=analyzed), delete any
    stale blunder rows for this game, and insert the new blunder rows.

    The upsert + delete + insert are all in the caller's transaction (no
    commit here) so a single game's writes are atomic.
    """
    with conn.cursor() as cur:
        # Delete any stale blunder rows from a previous (failed) attempt
        # for this game — keeps re-runs idempotent.
        cur.execute(
            "DELETE FROM opponent_game_blunders WHERE game_id = %s::uuid",
            (game_id,),
        )

        cur.execute(
            """
            INSERT INTO opponent_game_analysis (
                requested_by_user_id, provider, opponent_username,
                game_id, status
            )
            VALUES (%s, %s, %s, %s::uuid, 'analyzed')
            ON CONFLICT (provider, opponent_username, game_id) DO UPDATE SET
                status = 'analyzed',
                error = NULL,
                analyzed_at = NOW()
            RETURNING id::text
            """,
            (requested_by_user_id, provider, opponent_username, game_id),
        )
        analysis_id = cur.fetchone()[0]

        for b in blunders:
            cur.execute(
                """
                INSERT INTO opponent_game_blunders (
                    requested_by_user_id, provider, opponent_username,
                    game_id, analysis_id, fen, position_key,
                    move_number, move_san, classification, centipawn_loss
                )
                VALUES (%s, %s, %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
                """,
                (
                    requested_by_user_id,
                    provider,
                    opponent_username,
                    game_id,
                    analysis_id,
                    b["fen"],
                    b["position_key"],
                    b["move_number"],
                    b["move_san"],
                    b["classification"],
                    b["centipawn_loss"],
                ),
            )


def _write_failed_analysis(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    game_id: str,
    error: str,
) -> None:
    """Upsert a failed opponent_game_analysis row for a game that raised."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO opponent_game_analysis (
                requested_by_user_id, provider, opponent_username,
                game_id, status, error
            )
            VALUES (%s, %s, %s, %s::uuid, 'failed', %s)
            ON CONFLICT (provider, opponent_username, game_id) DO UPDATE SET
                status = 'failed',
                error = EXCLUDED.error,
                analyzed_at = NOW()
            """,
            (
                requested_by_user_id,
                provider,
                opponent_username,
                game_id,
                error[:2000],
            ),
        )


def _update_heartbeat(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> None:
    """Update heartbeat_at = NOW() and increment analyzed_games by 1."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opponent_analysis_jobs
            SET heartbeat_at = NOW(),
                analyzed_games = analyzed_games + 1
            WHERE requested_by_user_id = %s
              AND provider = %s
              AND LOWER(opponent_username) = LOWER(%s)
            """,
            (requested_by_user_id, provider, opponent_username),
        )


def _set_job_status(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    status: str,
) -> None:
    """Set the job row's status (typically 'complete' at end of run)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opponent_analysis_jobs
            SET status = %s
            WHERE requested_by_user_id = %s
              AND provider = %s
              AND LOWER(opponent_username) = LOWER(%s)
            """,
            (status, requested_by_user_id, provider, opponent_username),
        )


# ---------------------------------------------------------------------------
# PGN / color-detection helpers
# (These duplicate the small private helpers in opponent_repertoire.py
#  rather than reaching into another module's privates. The codebase
#  already follows this pattern — opponent_style.py has its own
#  _normalize_username identical to opponent_repertoire.py's.)
# ---------------------------------------------------------------------------

def _normalize_username(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def _player_username(player: Dict[str, Any]) -> str:
    return _normalize_username(
        str(player.get("username") or player.get("name") or "")
    )


def _position_key_from_fen(fen: str) -> str:
    """First 4 FEN fields — same convention as the repertoire sampler's
    ``_position_key(board)`` (which does ``" ".join(board.fen().split()[:4])``).
    Used so a later clustering step can join blunder positions to
    repertoire positions by the same key."""
    return " ".join(fen.split()[:4])


def _determine_opponent_color(
    pgn: str,
    white_player: Dict[str, Any],
    black_player: Dict[str, Any],
    opponent_username: str,
) -> Optional[str]:
    """Determine which color the opponent played in this game.

    Checks the white_player/black_player JSONB columns first (populated
    from the import API response), then falls back to the PGN's [White]/
    [Black] headers if the JSONB doesn't carry the name.  Returns
    ``"white"`` / ``"black"`` / ``None`` (None = couldn't match either
    side, which would indicate a data inconsistency).
    """
    normalized = _normalize_username(opponent_username)

    white_name = _player_username(white_player or {})
    black_name = _player_username(black_player or {})

    if not white_name or not black_name:
        game = chess.pgn.read_game(StringIO(pgn))
        if game:
            if not white_name:
                white_name = _normalize_username(game.headers.get("White"))
            if not black_name:
                black_name = _normalize_username(game.headers.get("Black"))

    if white_name == normalized:
        return "white"
    if black_name == normalized:
        return "black"
    return None