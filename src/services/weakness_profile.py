import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import chess
from psycopg2.extras import Json, RealDictCursor

from core import database
from core.game_analyzer import GameAnalyzer
from engines.stockfish_engine import StockfishEngine
from llms.mock_explainer import MockExplainer

log = logging.getLogger(__name__)

BAD_CLASSIFICATIONS = {"mistake", "blunder"}


@dataclass
class CorpusGame:
    id: str
    pgn: str
    game_url: str
    provider: Optional[str] = None
    opponent_username: Optional[str] = None


def create_weakness_profile_job(
    *,
    requested_by_user_id: str,
    source_type: str,
    provider: Optional[str],
    opponent_username: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO weakness_profile_jobs (
                    requested_by_user_id,
                    source_type,
                    provider,
                    opponent_username,
                    requested_limit
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING
                    id::text AS job_id,
                    status,
                    source_type,
                    provider,
                    opponent_username,
                    requested_limit
                """,
                (
                    requested_by_user_id,
                    source_type,
                    provider,
                    _normalize_username(opponent_username),
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


def get_weakness_profile_job(*, job_id: str, requested_by_user_id: str) -> Optional[Dict[str, Any]]:
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
                    source_type,
                    provider,
                    opponent_username,
                    requested_limit,
                    analyzed_games_count,
                    analyzed_moves_count,
                    mistake_count,
                    blunder_count,
                    summary,
                    error_message
                FROM weakness_profile_jobs
                WHERE id = %s AND requested_by_user_id = %s
                """,
                (job_id, requested_by_user_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        database.connection_pool.putconn(conn)


def run_weakness_profile_job(job_id: str) -> None:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    engine = StockfishEngine()
    try:
        job = _load_job_for_update(conn, job_id)
        if not job:
            log.warning("Weakness profile job disappeared before start: %s", job_id)
            return

        _mark_job_running(conn, job_id)
        conn.commit()

        games = list(_fetch_corpus_games(conn, job))
        if not games:
            raise ValueError("No games found for the selected profile source.")

        engine.start()
        analyzer = GameAnalyzer(engine=engine, explainer=MockExplainer())
        summary = _analyze_corpus(conn, analyzer, job, games)
        _mark_job_completed(conn, job_id, summary)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.exception("Weakness profile job failed: %s", job_id)
        _mark_job_failed_after_rollback(job_id, str(exc))
    finally:
        engine.close()
        database.connection_pool.putconn(conn)


def _normalize_username(username: Optional[str]) -> Optional[str]:
    value = (username or "").strip()
    return value or None


def _load_job_for_update(conn, job_id: str) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                id::text AS job_id,
                requested_by_user_id,
                source_type,
                provider,
                opponent_username,
                requested_limit
            FROM weakness_profile_jobs
            WHERE id = %s
            FOR UPDATE
            """,
            (job_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _fetch_corpus_games(conn, job: Dict[str, Any]) -> Iterable[CorpusGame]:
    if job["source_type"] == "opponent":
        return _fetch_opponent_games(conn, job)

    return _fetch_user_games(conn, job)


def _fetch_opponent_games(conn, job: Dict[str, Any]) -> Iterable[CorpusGame]:
    filters = ["requested_by_user_id = %s"]
    params: list[Any] = [job["requested_by_user_id"]]

    if job.get("provider"):
        filters.append("provider = %s")
        params.append(job["provider"])

    if job.get("opponent_username"):
        filters.append("LOWER(opponent_username) = LOWER(%s)")
        params.append(job["opponent_username"])

    params.append(int(job["requested_limit"]))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT
                id::text,
                pgn,
                game_url,
                provider,
                opponent_username
            FROM opponent_games
            WHERE {" AND ".join(filters)}
            ORDER BY end_time DESC, imported_at DESC
            LIMIT %s
            """,
            params,
        )
        return [
            CorpusGame(
                id=row["id"],
                pgn=row["pgn"],
                game_url=row["game_url"],
                provider=row["provider"],
                opponent_username=row["opponent_username"],
            )
            for row in cur.fetchall()
        ]


def _fetch_user_games(conn, job: Dict[str, Any]) -> Iterable[CorpusGame]:
    params: list[Any] = [job["requested_by_user_id"], int(job["requested_limit"])]

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                id::text,
                pgn,
                game_url,
                provider
            FROM user_games
            WHERE user_id = %s
            ORDER BY end_time DESC, imported_at DESC
            LIMIT %s
            """,
            params,
        )
        return [
            CorpusGame(
                id=row["id"],
                pgn=row["pgn"],
                game_url=row["game_url"],
                provider=row["provider"],
                opponent_username=None,
            )
            for row in cur.fetchall()
        ]


def _analyze_corpus(
    conn,
    analyzer: GameAnalyzer,
    job: Dict[str, Any],
    games: list[CorpusGame],
) -> Dict[str, Any]:
    phase_totals: dict[str, dict[str, int]] = defaultdict(_counter_row)
    bucket_totals: dict[str, dict[str, int]] = defaultdict(_counter_row)
    mistake_types: Counter[str] = Counter()
    worst_positions: list[Dict[str, Any]] = []
    analyzed_moves_count = 0
    analyzed_games_count = 0
    mistake_count = 0
    blunder_count = 0

    for game in games:
        try:
            rows = analyzer.analyze_full_game(
                game.pgn,
                target_color="both",
                include_explanations=False,
            )
        except Exception:  # noqa: BLE001
            log.exception("Skipping game that failed weakness analysis: %s", game.game_url)
            continue

        analyzed_games_count += 1
        for row in rows:
            if row.get("san") == "Start":
                continue

            move_number = int(row.get("move_number") or 0)
            phase = classify_game_phase(row.get("fen_before") or row["fen"], move_number)
            bucket = classify_move_bucket(move_number)
            classification = row["classification"]
            cp_loss = int(row["cp_loss"])

            analyzed_moves_count += 1
            _increment_accuracy_row(phase_totals[phase], classification)
            _increment_accuracy_row(bucket_totals[bucket], classification)

            if classification == "mistake":
                mistake_count += 1
            if classification == "blunder":
                blunder_count += 1

            if classification not in BAD_CLASSIFICATIONS:
                continue

            mistake_type = classify_mistake_type(row, phase)
            mistake_types[mistake_type] += 1
            _insert_profile_move(
                conn,
                job=job,
                game=game,
                row=row,
                phase=phase,
                bucket=bucket,
                mistake_type=mistake_type,
            )
            worst_positions.append(
                {
                    "fen": row.get("fen_before") or row["fen"],
                    "fen_after": row["fen"],
                    "game_url": game.game_url,
                    "provider": game.provider,
                    "opponent_username": game.opponent_username,
                    "move_number": move_number,
                    "san": row["san"],
                    "color": row["color"],
                    "classification": classification,
                    "cp_loss": cp_loss,
                    "phase": phase,
                    "move_bucket": bucket,
                    "mistake_type": mistake_type,
                    "best_move_san": row.get("best_move_san") or "",
                    "best_move_uci": row.get("best_move_uci") or "",
                }
            )

    worst_positions.sort(key=lambda item: item["cp_loss"], reverse=True)

    return {
        "analyzed_games_count": analyzed_games_count,
        "analyzed_moves_count": analyzed_moves_count,
        "mistake_count": mistake_count,
        "blunder_count": blunder_count,
        "summary": {
            "accuracy_by_phase": _format_accuracy_rows(phase_totals),
            "accuracy_by_move_bucket": _format_accuracy_rows(bucket_totals),
            "common_mistake_types": [
                {"type": key, "count": count}
                for key, count in mistake_types.most_common(8)
            ],
            "worst_positions": worst_positions[:10],
        },
    }


def _counter_row() -> dict[str, int]:
    return {"total": 0, "bad": 0, "mistake": 0, "blunder": 0}


def _increment_accuracy_row(row: dict[str, int], classification: str) -> None:
    row["total"] += 1
    if classification in BAD_CLASSIFICATIONS:
        row["bad"] += 1
    if classification == "mistake":
        row["mistake"] += 1
    if classification == "blunder":
        row["blunder"] += 1


def _format_accuracy_rows(rows: dict[str, dict[str, int]]) -> list[Dict[str, Any]]:
    order = {
        "opening": 0,
        "middlegame": 1,
        "endgame": 2,
        "1-10": 0,
        "11-20": 1,
        "21-30": 2,
        "31-40": 3,
        "41+": 4,
    }
    formatted = []
    for key, row in rows.items():
        total = row["total"]
        accuracy = round(((total - row["bad"]) / total) * 100, 1) if total else 0.0
        formatted.append(
            {
                "key": key,
                "accuracy": accuracy,
                "total_moves": total,
                "mistakes": row["mistake"],
                "blunders": row["blunder"],
            }
        )
    return sorted(formatted, key=lambda item: order.get(item["key"], 99))


def classify_game_phase(fen: str, move_number: int) -> str:
    if move_number <= 10:
        return "opening"
    if move_number >= 31 or _non_king_material_count(fen) <= 10:
        return "endgame"
    return "middlegame"


def classify_move_bucket(move_number: int) -> str:
    if move_number <= 10:
        return "1-10"
    if move_number <= 20:
        return "11-20"
    if move_number <= 30:
        return "21-30"
    if move_number <= 40:
        return "31-40"
    return "41+"


def classify_mistake_type(row: Dict[str, Any], phase: str) -> str:
    san = str(row.get("san") or "")
    best = str(row.get("best_move_san") or "")
    cp_loss = int(row.get("cp_loss") or 0)

    if phase == "opening":
        return "opening mistake"
    if "#" in best or cp_loss >= 900:
        return "missed mate or decisive tactic"
    if "x" in best and "x" not in san:
        return "missed capture"
    if "+" in best and "+" not in san:
        return "missed check"
    if san.startswith(("K", "Q", "R", "B", "N")):
        return "piece placement"
    if phase == "endgame":
        return "endgame technique"
    return "tactical oversight"


def _non_king_material_count(fen: str) -> int:
    try:
        board = chess.Board(fen)
    except ValueError:
        return 32

    return sum(
        1
        for piece in board.piece_map().values()
        if piece.piece_type != chess.KING
    )


def _insert_profile_move(
    conn,
    *,
    job: Dict[str, Any],
    game: CorpusGame,
    row: Dict[str, Any],
    phase: str,
    bucket: str,
    mistake_type: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO weakness_profile_moves (
                profile_job_id,
                requested_by_user_id,
                source_type,
                source_game_id,
                game_url,
                provider,
                opponent_username,
                phase,
                move_bucket,
                move_number,
                color,
                san,
                classification,
                cp_loss,
                mistake_type,
                fen_before,
                fen_after,
                best_move_san,
                best_move_uci
            )
            VALUES (%s, %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job["job_id"],
                job["requested_by_user_id"],
                job["source_type"],
                game.id,
                game.game_url,
                game.provider,
                game.opponent_username,
                phase,
                bucket,
                int(row.get("move_number") or 0),
                row["color"],
                row["san"],
                row["classification"],
                int(row["cp_loss"]),
                mistake_type,
                row.get("fen_before") or row["fen"],
                row["fen"],
                row.get("best_move_san") or "",
                row.get("best_move_uci") or "",
            ),
        )


def _mark_job_running(conn, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE weakness_profile_jobs
            SET status = 'running', started_at = NOW(), error_message = NULL
            WHERE id = %s
            """,
            (job_id,),
        )


def _mark_job_completed(conn, job_id: str, summary: Dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE weakness_profile_jobs
            SET status = 'completed',
                analyzed_games_count = %s,
                analyzed_moves_count = %s,
                mistake_count = %s,
                blunder_count = %s,
                summary = %s,
                completed_at = NOW(),
                error_message = NULL
            WHERE id = %s
            """,
            (
                summary["analyzed_games_count"],
                summary["analyzed_moves_count"],
                summary["mistake_count"],
                summary["blunder_count"],
                Json(summary["summary"]),
                job_id,
            ),
        )


def _mark_job_failed_after_rollback(job_id: str, error_message: str) -> None:
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE weakness_profile_jobs
                SET status = 'failed',
                    completed_at = NOW(),
                    error_message = %s
                WHERE id = %s
                """,
                (error_message[:2000], job_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("Failed to mark weakness profile job failed: %s", job_id)
    finally:
        database.connection_pool.putconn(conn)
