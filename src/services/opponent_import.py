import logging
from typing import Any, Dict, Iterable, Optional

from psycopg2.extras import Json, RealDictCursor

from core import database
from integrations.chess_com import fetch_recent_chesscom_games
from integrations.lichess import fetch_recent_lichess_games
from services.opponent_repertoire import index_opponent_game

log = logging.getLogger(__name__)


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

    conn = database.connection_pool.getconn()
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
            )

        if errors:
            _mark_job_failed(conn, job_id, imported_count, "; ".join(errors))
        else:
            _mark_job_completed(conn, job_id, imported_count)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.exception("Opponent import job failed: %s", job_id)
        _mark_job_failed_after_rollback(job_id, str(exc))
    finally:
        database.connection_pool.putconn(conn)


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


def _mark_job_completed(conn, job_id: str, imported_count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE opponent_import_jobs
            SET status = 'completed',
                imported_count = %s,
                completed_at = NOW(),
                error_message = NULL
            WHERE id = %s
            """,
            (imported_count, job_id),
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
) -> int:
    try:
        if provider == "lichess":
            games = fetch_recent_lichess_games(username=username, limit=limit)
        else:
            games = fetch_recent_chesscom_games(username=username, limit=limit)
    except Exception as exc:  # noqa: BLE001
        log.exception("Opponent %s import failed for %s", provider, username)
        errors.append(f"{provider} {username}: {exc}")
        return 0

    return _store_opponent_games(
        conn,
        requested_by_user_id=requested_by_user_id,
        job_id=job_id,
        provider=provider,
        opponent_username=username,
        games=games,
    )


def _store_opponent_games(
    conn,
    *,
    requested_by_user_id: str,
    job_id: str,
    provider: str,
    opponent_username: str,
    games: Iterable[Dict[str, Any]],
) -> int:
    inserted_or_updated = 0
    with conn.cursor() as cur:
        for game in games:
            if not game.get("url") or not game.get("pgn"):
                continue

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
                    Json(game),
                ),
            )
            game_id = cur.fetchone()[0]
            index_opponent_game(
                conn,
                game_id=game_id,
                requested_by_user_id=requested_by_user_id,
                provider=provider,
                opponent_username=opponent_username,
                pgn=game["pgn"],
                white_player=game.get("white") or {},
                black_player=game.get("black") or {},
            )
            inserted_or_updated += 1

    return inserted_or_updated
