import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, Optional

from psycopg2.extras import Json, RealDictCursor

from core import database
from integrations.chess_com import (
    fetch_chesscom_profile,
    fetch_recent_chesscom_games,
)
from integrations.lichess import fetch_recent_lichess_games
from services.opponent_game_analysis import (
    run_opponent_game_analysis,
    try_start_opponent_analysis,
)
from services.opponent_repertoire import (
    build_opponent_profile_snapshot,
    index_opponent_game,
    upsert_opponent_profile_snapshot,
    replay_opponent_game,
)

log = logging.getLogger(__name__)

_REPERTOIRE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="opponent-repertoire",
)


def _normalize_username(username: Optional[str]) -> Optional[str]:
    value = (username or "").strip()
    return value or None


def create_opponent_import_job(
    *,
    requested_by_user_id: str,
    lichess_username: Optional[str],
    chesscom_username: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO opponent_import_jobs (
                    requested_by_user_id,
                    lichess_username,
                    chesscom_username,
                    requested_limit
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    id::text AS job_id,
                    status,
                    lichess_username,
                    chesscom_username,
                    requested_limit
                """,
                (
                    requested_by_user_id,
                    _normalize_username(lichess_username),
                    _normalize_username(chesscom_username),
                    limit,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        database.connection_pool.putconn(conn)


def get_opponent_import_job(*, job_id: str, requested_by_user_id: str) -> Optional[Dict[str, Any]]:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id::text AS job_id,
                    status,
                    lichess_username,
                    chesscom_username,
                    requested_limit,
                    imported_count,
                    total_games,
                    opponent_prep_ready,
                    repertoire_index_status,
                    repertoire_indexed_games,
                    repertoire_total_games,
                    error_message
                FROM opponent_import_jobs
                WHERE id = %s AND requested_by_user_id = %s
                """,
                (job_id, requested_by_user_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        database.connection_pool.putconn(conn)


def run_opponent_import_job(job_id: str) -> None:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    total_started = time.perf_counter()
    profile: Dict[str, Any] = {
        "fetch_games": 0,
        "fetch_duration": 0.0,
        "fetch_api_calls": 0,
        "fetch_providers": [],
        "db_insert_games": 0,
        "db_insert_duration": 0.0,
        "db_insert_representative_duration": None,
        "pgn_replay_games": 0,
        "pgn_replay_duration": 0.0,
    }
    conn = database.connection_pool.getconn()
    job: Optional[Dict[str, Any]] = None
    providers_to_analyze: list[tuple[str, str]] = []
    repertoire_jobs: list[tuple[str, str]] = []
    try:
        job = _load_job_for_update(conn, job_id)
        if not job:
            log.warning("Opponent import job disappeared before start: %s", job_id)
            return

        _mark_job_running(conn, job_id)
        conn.commit()

        imported_count = 0
        errors: list[str] = []

        if job.get("lichess_username"):
            imported_count += _fetch_and_store_provider_games(
                conn,
                requested_by_user_id=job["requested_by_user_id"],
                job_id=job_id,
                provider="lichess",
                username=job["lichess_username"],
                limit=int(job["requested_limit"]),
                errors=errors,
                profile=profile,
            )

        if job.get("chesscom_username"):
            imported_count += _fetch_and_store_provider_games(
                conn,
                requested_by_user_id=job["requested_by_user_id"],
                job_id=job_id,
                provider="chesscom",
                username=job["chesscom_username"],
                limit=int(job["requested_limit"]),
                errors=errors,
                profile=profile,
            )

        if not errors:
            snapshot_started = time.perf_counter()
            for provider, username in _job_opponents(job):
                avatar_url, verified = _fetch_profile_metadata(provider, username)
                snapshot = build_opponent_profile_snapshot(
                    conn,
                    requested_by_user_id=job["requested_by_user_id"],
                    provider=provider,
                    opponent_username=username,
                    avatar_url=avatar_url,
                    verified=verified,
                )
                upsert_opponent_profile_snapshot(conn, snapshot)
                repertoire_jobs.append((provider, username))
            log.info(
                "[IMPORT_PROFILE] phase=snapshot_compute games=%d duration_ms=%.2f",
                imported_count,
                (time.perf_counter() - snapshot_started) * 1000,
            )

        if errors:
            _mark_job_failed(conn, job_id, imported_count, "; ".join(errors))
        else:
            _mark_job_completed(conn, job_id, imported_count)
        conn.commit()

        # Build the list of (provider, username) pairs to chain the
        # Stockfish analysis after. Only on a clean import (no errors).
        if not errors:
            if job.get("lichess_username"):
                providers_to_analyze.append(
                    ("lichess", job["lichess_username"])
                )
            if job.get("chesscom_username"):
                providers_to_analyze.append(
                    ("chesscom", job["chesscom_username"])
                )
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.exception("Opponent import job failed: %s", job_id)
        _mark_job_failed_after_rollback(job_id, str(exc))
    finally:
        database.connection_pool.putconn(conn)

    if repertoire_jobs:
        _enqueue_repertoire_indexing(
            job_id=job_id,
            requested_by_user_id=job["requested_by_user_id"],
            opponents=repertoire_jobs,
        )

    # --- Chain the Stockfish analysis AFTER releasing the import conn ---
    # The import is committed and the conn is back in the pool. The
    # analysis trigger does its own single-transaction state check + flip
    # (try_start_opponent_analysis), then the worker processes unanalyzed
    # games one at a time. Both get their own conns from the pool.
    #
    # This runs synchronously in the same background thread as the import
    # — the HTTP response was already sent (the endpoint returns 202
    # immediately). The frontend polls the import job's status (which is
    # already "completed"), navigates to the sparring page, and then
    # polls the analysis status endpoint for "Analyzing… 47/124".
    if job and providers_to_analyze:
        for provider, username in providers_to_analyze:
            try:
                trigger = try_start_opponent_analysis(
                    requested_by_user_id=job["requested_by_user_id"],
                    provider=provider,
                    opponent_username=username,
                )
                if trigger["should_run"]:
                    run_opponent_game_analysis(
                        requested_by_user_id=job["requested_by_user_id"],
                        provider=provider,
                        opponent_username=username,
                    )
            except Exception:  # noqa: BLE001
                log.exception(
                    "Failed to trigger opponent analysis for %s/%s",
                    provider, username,
                )

    _log_import_profile(profile)
    log.info(
        "[IMPORT_PROFILE] phase=total duration_ms=%.2f",
        (time.perf_counter() - total_started) * 1000,
    )


def _load_job_for_update(conn, job_id: str) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                id::text AS job_id,
                requested_by_user_id,
                lichess_username,
                chesscom_username,
                requested_limit,
                status
            FROM opponent_import_jobs
            WHERE id = %s
            FOR UPDATE
            """,
            (job_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _job_opponents(job: Dict[str, Any]) -> list[tuple[str, str]]:
    opponents: list[tuple[str, str]] = []
    if job.get("lichess_username"):
        opponents.append(("lichess", job["lichess_username"]))
    if job.get("chesscom_username"):
        opponents.append(("chesscom", job["chesscom_username"]))
    return opponents


def _fetch_profile_metadata(provider: str, username: str) -> tuple[Optional[str], bool]:
    if provider != "chesscom":
        return None, False
    try:
        payload = fetch_chesscom_profile(username)
    except Exception as exc:  # noqa: BLE001 -- profile metadata is optional
        log.warning("Opponent profile metadata fetch failed for %s: %s", username, exc)
        return None, False
    return payload.get("avatar"), bool(payload.get("verified"))


def _enqueue_repertoire_indexing(
    *,
    job_id: str,
    requested_by_user_id: str,
    opponents: list[tuple[str, str]],
) -> None:
    """Queue repertoire work after prep data has been committed and exposed."""
    _REPERTOIRE_EXECUTOR.submit(
        run_opponent_repertoire_index,
        job_id=job_id,
        requested_by_user_id=requested_by_user_id,
        opponents=opponents,
    )


def run_opponent_repertoire_index(
    *,
    job_id: str,
    requested_by_user_id: str,
    opponents: list[tuple[str, str]],
) -> None:
    """Index the complete opponent corpus independently of import readiness."""
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    started = time.perf_counter()
    conn = database.connection_pool.getconn()
    processed = 0
    total_games = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE opponent_import_jobs
                SET repertoire_index_status = 'running',
                    repertoire_indexed_games = 0
                WHERE id = %s
                """,
                (job_id,),
            )
            rows: list[Dict[str, Any]] = []
            for provider, opponent_username in opponents:
                cur.execute(
                    """
                    SELECT
                        g.id::text AS game_id,
                        g.requested_by_user_id,
                        g.provider,
                        g.opponent_username,
                        g.pgn,
                        g.white_player,
                        g.black_player
                    FROM opponent_games g
                    WHERE g.requested_by_user_id = %s
                      AND g.provider = %s
                      AND LOWER(g.opponent_username) = LOWER(%s)
                    ORDER BY g.end_time DESC, g.imported_at DESC
                    """,
                    (requested_by_user_id, provider, opponent_username),
                )
                rows.extend(dict(row) for row in cur.fetchall())
        total_games = len(rows)
        conn.commit()

        for row in rows:
            index_timing: Dict[str, float] = {}
            index_opponent_game(conn, **row, timing=index_timing)
            processed += 1
            if processed % _PROGRESS_COMMIT_EVERY == 0:
                _update_repertoire_index_progress(
                    conn,
                    job_id=job_id,
                    indexed_games=processed,
                )
                conn.commit()

        _update_repertoire_index_progress(
            conn,
            job_id=job_id,
            indexed_games=processed,
            status="complete",
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _update_repertoire_index_progress(
            conn,
            job_id=job_id,
            indexed_games=processed,
            status="failed",
        )
        conn.commit()
        log.exception(
            "Opponent repertoire indexing failed for %d opponent(s): %s",
            len(opponents),
            exc,
        )
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        log.info(
            "[IMPORT_PROFILE] phase=repertoire_insert games=%d "
            "duration_ms=%.2f avg_per_game_ms=%.2f providers=%d background=true",
            processed,
            duration_ms,
            duration_ms / processed if processed else 0.0,
            len(opponents),
        )
        database.connection_pool.putconn(conn)


def _update_repertoire_index_progress(
    conn,
    *,
    job_id: str,
    indexed_games: int,
    status: Optional[str] = None,
) -> None:
    with conn.cursor() as cur:
        if status is None:
            cur.execute(
                """
                UPDATE opponent_import_jobs
                SET repertoire_indexed_games = %s
                WHERE id = %s
                """,
                (indexed_games, job_id),
            )
        else:
            cur.execute(
                """
                UPDATE opponent_import_jobs
                SET repertoire_index_status = %s,
                    repertoire_indexed_games = %s
                WHERE id = %s
                """,
                (status, indexed_games, job_id),
            )


def _mark_job_running(conn, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opponent_import_jobs
            SET status = 'running', started_at = NOW(), error_message = NULL
            WHERE id = %s
            """,
            (job_id,),
        )


_PROGRESS_COMMIT_EVERY = 5


def _publish_import_progress(conn, job_id: str, imported_count: int, total_games: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opponent_import_jobs
            SET imported_count = %s, total_games = %s
            WHERE id = %s
            """,
            (imported_count, total_games, job_id),
        )


def _mark_job_completed(conn, job_id: str, imported_count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opponent_import_jobs
            SET status = 'completed',
                imported_count = %s,
                completed_at = NOW(),
                error_message = NULL,
                opponent_prep_ready = TRUE,
                repertoire_index_status = 'queued',
                repertoire_indexed_games = 0,
                repertoire_total_games = %s
            WHERE id = %s
            """,
            (imported_count, imported_count, job_id),
        )


def _mark_job_failed(conn, job_id: str, imported_count: int, error_message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opponent_import_jobs
            SET status = 'failed',
                imported_count = %s,
                completed_at = NOW(),
                error_message = %s
            WHERE id = %s
            """,
            (imported_count, error_message[:2000], job_id),
        )


def _mark_job_failed_after_rollback(job_id: str, error_message: str) -> None:
    conn = database.connection_pool.getconn()
    try:
        _mark_job_failed(conn, job_id, 0, error_message)
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("Failed to mark opponent import job failed: %s", job_id)
    finally:
        database.connection_pool.putconn(conn)


def _fetch_and_store_provider_games(
    conn,
    *,
    requested_by_user_id: str,
    job_id: str,
    provider: str,
    username: str,
    limit: int,
    errors: list[str],
    profile: Dict[str, Any],
) -> int:
    fetch_started = time.perf_counter()
    profile["fetch_api_calls"] += 1
    profile["fetch_providers"].append(provider)
    try:
        if provider == "lichess":
            games = fetch_recent_lichess_games(username=username, limit=limit)
        else:
            games = fetch_recent_chesscom_games(username=username, limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.exception("Opponent %s import failed for %s", provider, username)
        errors.append(f"{provider} {username}: {exc}")
        profile["fetch_duration"] += time.perf_counter() - fetch_started
        return 0

    profile["fetch_games"] += len(games)
    profile["fetch_duration"] += time.perf_counter() - fetch_started

    return _store_opponent_games(
        conn,
        requested_by_user_id=requested_by_user_id,
        job_id=job_id,
        provider=provider,
        opponent_username=username,
        games=games,
        profile=profile,
    )


def _store_opponent_games(
    conn,
    *,
    requested_by_user_id: str,
    job_id: str,
    provider: str,
    opponent_username: str,
    games: Iterable[Dict[str, Any]],
    profile: Dict[str, Any],
) -> int:
    game_list = list(games)
    total_games = len(game_list)
    inserted_or_updated = 0

    # Publish the total (and zero progress) before the first row lands so the
    # frontend can compute a real percentage while the import is still running.
    _publish_import_progress(conn, job_id, 0, total_games)
    conn.commit()

    with conn.cursor() as cur:
        for game in game_list:
            if not game.get("url") or not game.get("pgn"):
                continue

            # NOTE: raw_summary is intentionally stored as '{}'::jsonb, not
            # the full upstream game payload (`Json(game)`).  Every reader
            # of opponent_games selects pgn / white_player / black_player /
            # end_time / time_class / ids — none read raw_summary.  Storing
            # the full API response was ~5-30KB of dead ballast per game
            # (several MB for a 700-game opponent) with zero functional
            # value.  The column is kept NOT NULL DEFAULT '{}'::jsonb for
            # schema compatibility; we just never populate it.  If a future
            # feature needs the raw payload, re-fetch from the provider on
            # demand rather than restoring the write here.
            db_insert_started = time.perf_counter()
            cur.execute(
                """
                INSERT INTO opponent_games (
                    requested_by_user_id,
                    import_job_id,
                    provider,
                    opponent_username,
                    game_url,
                    pgn,
                    white_player,
                    black_player,
                    result,
                    end_time,
                    time_class,
                    raw_summary
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (requested_by_user_id, provider, opponent_username, game_url)
                DO UPDATE SET
                    import_job_id = EXCLUDED.import_job_id,
                    pgn = EXCLUDED.pgn,
                    white_player = EXCLUDED.white_player,
                    black_player = EXCLUDED.black_player,
                    result = EXCLUDED.result,
                    end_time = EXCLUDED.end_time,
                    time_class = EXCLUDED.time_class,
                    raw_summary = EXCLUDED.raw_summary,
                    imported_at = NOW()
                RETURNING id::text
                """,
                (
                    requested_by_user_id,
                    job_id,
                    provider,
                    opponent_username,
                    game["url"],
                    game["pgn"],
                    Json(game.get("white") or {}),
                    Json(game.get("black") or {}),
                    game.get("result") or "",
                    int(game.get("end_time") or 0),
                    game.get("time_class") or "",
                    Json({}),
                ),
            )
            game_id = cur.fetchone()[0]
            db_insert_duration = time.perf_counter() - db_insert_started
            profile["db_insert_games"] += 1
            profile["db_insert_duration"] += db_insert_duration
            if profile["db_insert_representative_duration"] is None:
                profile["db_insert_representative_duration"] = db_insert_duration

            replay_timing: Dict[str, float] = {}
            replay_opponent_game(game["pgn"], timing=replay_timing)
            profile["pgn_replay_games"] += 1
            profile["pgn_replay_duration"] += replay_timing.get(
                "pgn_replay_duration", 0.0
            )
            inserted_or_updated += 1

            if inserted_or_updated % _PROGRESS_COMMIT_EVERY == 0:
                _publish_import_progress(conn, job_id, inserted_or_updated, total_games)
                conn.commit()

    return inserted_or_updated


def _log_import_profile(profile: Dict[str, Any]) -> None:
    fetch_games = int(profile["fetch_games"])
    fetch_duration_ms = float(profile["fetch_duration"]) * 1000
    providers = profile["fetch_providers"]
    pagination = "sequential" if "chesscom" in providers else "none"
    log.info(
        "[IMPORT_PROFILE] phase=fetch_games games=%d duration_ms=%.2f "
        "api_calls=%d api_call_scope=provider_fetch pagination=%s "
        "execution=sequential",
        fetch_games,
        fetch_duration_ms,
        int(profile["fetch_api_calls"]),
        pagination,
    )

    db_games = int(profile["db_insert_games"])
    db_duration_ms = float(profile["db_insert_duration"]) * 1000
    db_avg_ms = db_duration_ms / db_games if db_games else 0.0
    representative = profile["db_insert_representative_duration"]
    representative_ms = (
        float(representative) * 1000 if representative is not None else 0.0
    )
    log.info(
        "[IMPORT_PROFILE] phase=db_insert games=%d duration_ms=%.2f "
        "avg_per_game_ms=%.2f representative_insert_ms=%.2f",
        db_games,
        db_duration_ms,
        db_avg_ms,
        representative_ms,
    )

    replay_games = int(profile["pgn_replay_games"])
    replay_duration_ms = float(profile["pgn_replay_duration"]) * 1000
    replay_avg_ms = replay_duration_ms / replay_games if replay_games else 0.0
    log.info(
        "[IMPORT_PROFILE] phase=pgn_replay games=%d duration_ms=%.2f "
        "avg_per_game_ms=%.2f",
        replay_games,
        replay_duration_ms,
        replay_avg_ms,
    )

# ---------------------------------------------------------------------------
# Opponent data cleanup
# ---------------------------------------------------------------------------
#
# The sparring feature imports an opponent's recent games into opponent_games
# and runs Stockfish blunder classification + repertoire indexing + (optional)
# weakness profile analysis on them.  The imported PGN corpus is the source of
# truth that opponent_style / opponent_repertoire / weakness_profile re-read on
# every page load to recompute style / time-control / opening distributions, so
# the bulky `pgn` column CANNOT be trimmed without breaking the sparring page
# (see ADR note in _store_opponent_games for the raw_summary trim, which IS
# safe because no reader selects it).
#
# When the user is done sparring, this function wipes all opponent-related
# state for the (user, optional provider+opponent_username) scope in a single
# transaction.  Re-sparring the same opponent later requires re-importing.
#
# FK ON DELETE CASCADE handles the child tables:
#   * opponent_games            -> opponent_repertoire_moves
#   *                            -> opponent_game_analysis
#   *                            -> opponent_game_blunders (via game_id AND
#                               via analysis_id -> opponent_game_analysis)
#   * weakness_profile_jobs     -> weakness_profile_moves
# The returned counts only include direct DELETEs; cascade-removed child rows
# are not counted individually (would require RETURNING + a second pass per
# child table for no functional benefit — the parent count is the user-facing
# signal).


def clear_opponent_data(
    *,
    requested_by_user_id: str,
    provider: Optional[str] = None,
    opponent_username: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete all opponent sparring data for a user, or for one opponent.

    Two scopes:
      * Full clear (provider=None, opponent_username=None): wipes every
        opponent_* row owned by the user — opponent_games (CASCADE removes
        repertoire_moves / game_analysis / game_blunders),
        opponent_analysis_jobs, opponent_import_jobs, and
        weakness_profile_jobs where source_type='opponent' (CASCADE removes
        weakness_profile_moves).
      * Per-opponent clear (both provider and opponent_username supplied):
        wipes only that opponent's rows. opponent_import_jobs is excluded
        from the per-opponent scope because its schema has separate
        lichess_username / chesscom_username columns (a single import job
        can fetch from both providers at once) so a clean per-opponent
        match is ambiguous; the import job row is tiny (status + counts
        only) and kept as an audit trail until a full clear.

    All deletes run in one transaction.  Returns a dict with the scope and
    per-table direct-delete counts (cascade-removed child rows are not
    counted).
    """
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    per_opponent = provider is not None and opponent_username is not None
    if provider is not None and opponent_username is None:
        raise ValueError(
            "provider without opponent_username is ambiguous — pass both "
            "for per-opponent clear, or neither for full clear"
        )
    if opponent_username is not None and provider is None:
        raise ValueError(
            "opponent_username without provider is ambiguous — pass both "
            "for per-opponent clear, or neither for full clear"
        )
    if provider is not None and provider not in ("lichess", "chesscom"):
        raise ValueError("provider must be 'lichess' or 'chesscom'")

    scope = "opponent" if per_opponent else "all"

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            if per_opponent:
                cur.execute(
                    """
                    DELETE FROM opponent_profile_snapshots
                    WHERE requested_by_user_id = %s
                      AND provider = %s
                      AND LOWER(opponent_username) = LOWER(%s)
                    """,
                    (requested_by_user_id, provider, opponent_username),
                )

                cur.execute(
                    """
                    DELETE FROM opponent_games
                    WHERE requested_by_user_id = %s
                      AND provider = %s
                      AND LOWER(opponent_username) = LOWER(%s)
                    """,
                    (requested_by_user_id, provider, opponent_username),
                )
                games_deleted = cur.rowcount

                cur.execute(
                    """
                    DELETE FROM opponent_analysis_jobs
                    WHERE requested_by_user_id = %s
                      AND provider = %s
                      AND LOWER(opponent_username) = LOWER(%s)
                    """,
                    (requested_by_user_id, provider, opponent_username),
                )
                analysis_jobs_deleted = cur.rowcount

                cur.execute(
                    """
                    DELETE FROM weakness_profile_jobs
                    WHERE requested_by_user_id = %s
                      AND source_type = 'opponent'
                      AND provider = %s
                      AND LOWER(opponent_username) = LOWER(%s)
                    """,
                    (requested_by_user_id, provider, opponent_username),
                )
                weakness_jobs_deleted = cur.rowcount

                import_jobs_deleted = 0
            else:
                cur.execute(
                    """
                    DELETE FROM opponent_profile_snapshots
                    WHERE requested_by_user_id = %s
                    """,
                    (requested_by_user_id,),
                )

                cur.execute(
                    """
                    DELETE FROM opponent_games
                    WHERE requested_by_user_id = %s
                    """,
                    (requested_by_user_id,),
                )
                games_deleted = cur.rowcount

                cur.execute(
                    """
                    DELETE FROM opponent_analysis_jobs
                    WHERE requested_by_user_id = %s
                    """,
                    (requested_by_user_id,),
                )
                analysis_jobs_deleted = cur.rowcount

                cur.execute(
                    """
                    DELETE FROM opponent_import_jobs
                    WHERE requested_by_user_id = %s
                    """,
                    (requested_by_user_id,),
                )
                import_jobs_deleted = cur.rowcount

                cur.execute(
                    """
                    DELETE FROM weakness_profile_jobs
                    WHERE requested_by_user_id = %s
                      AND source_type = 'opponent'
                    """,
                    (requested_by_user_id,),
                )
                weakness_jobs_deleted = cur.rowcount

        conn.commit()
        return {
            "scope": scope,
            "provider": provider if per_opponent else None,
            "opponent_username": opponent_username if per_opponent else None,
            "opponent_games_deleted": int(games_deleted or 0),
            "opponent_analysis_jobs_deleted": int(analysis_jobs_deleted or 0),
            "opponent_import_jobs_deleted": int(import_jobs_deleted or 0),
            "weakness_profile_jobs_deleted": int(weakness_jobs_deleted or 0),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        database.connection_pool.putconn(conn)
