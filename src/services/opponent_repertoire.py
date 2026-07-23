import logging
import random
from io import StringIO
from typing import Any, Dict, Optional

import chess
import chess.pgn
from psycopg2.extras import RealDictCursor

from core import database

log = logging.getLogger(__name__)


def _position_key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def _normalize_username(value: Optional[str]) -> str:
    return (value or "").strip().casefold()


def _player_username(player: Dict[str, Any]) -> str:
    return _normalize_username(str(player.get("username") or player.get("name") or ""))


def _player_rating(player: Dict[str, Any]) -> Optional[int]:
    raw_rating = player.get("rating") or player.get("elo")
    try:
        rating = int(raw_rating)
    except (TypeError, ValueError):
        return None
    return rating if 100 <= rating <= 4000 else None


def index_opponent_game(
    conn,
    *,
    game_id: str,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    pgn: str,
    white_player: Optional[Dict[str, Any]] = None,
    black_player: Optional[Dict[str, Any]] = None,
) -> int:
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None:
        return 0

    normalized_opponent = _normalize_username(opponent_username)
    white_name = _player_username(white_player or {}) or _normalize_username(game.headers.get("White"))
    black_name = _player_username(black_player or {}) or _normalize_username(game.headers.get("Black"))

    if white_name == normalized_opponent:
        opponent_color = chess.WHITE
        played_color = "white"
    elif black_name == normalized_opponent:
        opponent_color = chess.BLACK
        played_color = "black"
    else:
        return 0

    inserted = 0
    board = game.board()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM opponent_repertoire_moves WHERE opponent_game_id = %s",
            (game_id,),
        )
        for ply_index, move in enumerate(game.mainline_moves()):
            if board.turn == opponent_color:
                try:
                    move_san = board.san(move)
                except ValueError:
                    move_san = move.uci()

                cur.execute(
                    """
                    INSERT INTO opponent_repertoire_moves (
                        opponent_game_id,
                        requested_by_user_id,
                        provider,
                        opponent_username,
                        position_key,
                        move_uci,
                        move_san,
                        ply_index,
                        played_color
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (opponent_game_id, ply_index) DO NOTHING
                    """,
                    (
                        game_id,
                        requested_by_user_id,
                        provider,
                        opponent_username,
                        _position_key(board),
                        move.uci(),
                        move_san,
                        ply_index,
                        played_color,
                    ),
                )
                inserted += cur.rowcount
            board.push(move)

    return inserted


def ensure_opponent_repertoire(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> int:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        indexed_count = 0
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
                  AND NOT EXISTS (
                      SELECT 1
                      FROM opponent_repertoire_moves r
                      WHERE r.opponent_game_id = g.id
                  )
                ORDER BY g.end_time DESC, g.imported_at DESC
                """,
                (requested_by_user_id, provider, opponent_username),
            )
            rows = [dict(row) for row in cur.fetchall()]

        for row in rows:
            try:
                indexed_count += index_opponent_game(conn, **row)
            except Exception:  # noqa: BLE001
                log.exception("Failed to index opponent game %s", row.get("game_id"))

        conn.commit()
        return indexed_count
    except Exception:
        conn.rollback()
        raise
    finally:
        database.connection_pool.putconn(conn)


def list_opponent_profiles(*, requested_by_user_id: str) -> list[Dict[str, Any]]:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    provider,
                    opponent_username,
                    COUNT(*)::int AS game_count,
                    JSONB_AGG(white_player) AS white_players,
                    JSONB_AGG(black_player) AS black_players
                FROM opponent_games
                WHERE requested_by_user_id = %s
                GROUP BY provider, opponent_username
                ORDER BY MAX(imported_at) DESC
                """,
                (requested_by_user_id,),
            )
            rows = [dict(row) for row in cur.fetchall()]

        return [
            {
                "provider": row["provider"],
                "opponent_username": row["opponent_username"],
                "game_count": row["game_count"],
                "rating": _rating_from_player_lists(
                    opponent_username=row["opponent_username"],
                    white_players=row.get("white_players") or [],
                    black_players=row.get("black_players") or [],
                ),
            }
            for row in rows
        ]
    finally:
        database.connection_pool.putconn(conn)


def get_opponent_rating(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> Optional[int]:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT white_player, black_player
                FROM opponent_games
                WHERE requested_by_user_id = %s
                  AND provider = %s
                  AND LOWER(opponent_username) = LOWER(%s)
                """,
                (requested_by_user_id, provider, opponent_username),
            )
            rows = [dict(row) for row in cur.fetchall()]

        if not rows:
            return None

        return _rating_from_player_lists(
            opponent_username=opponent_username,
            white_players=[row.get("white_player") or {} for row in rows],
            black_players=[row.get("black_player") or {} for row in rows],
        )
    finally:
        database.connection_pool.putconn(conn)


def pick_repertoire_move(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    board: chess.Board,
) -> Optional[Dict[str, Any]]:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    move_uci,
                    MIN(move_san) AS move_san,
                    COUNT(*)::int AS frequency
                FROM opponent_repertoire_moves
                WHERE requested_by_user_id = %s
                  AND provider = %s
                  AND LOWER(opponent_username) = LOWER(%s)
                  AND position_key = %s
                GROUP BY move_uci
                """,
                (requested_by_user_id, provider, opponent_username, _position_key(board)),
            )
            rows = [dict(row) for row in cur.fetchall()]

        if not rows:
            return None

        choice = random.choices(
            rows,
            weights=[max(1, int(row["frequency"])) for row in rows],
            k=1,
        )[0]
        return choice
    finally:
        database.connection_pool.putconn(conn)


def _rating_from_player_lists(
    *,
    opponent_username: str,
    white_players: list[Dict[str, Any]],
    black_players: list[Dict[str, Any]],
) -> int:
    normalized_opponent = _normalize_username(opponent_username)
    ratings: list[int] = []

    for player in white_players + black_players:
        if _player_username(player or {}) == normalized_opponent:
            rating = _player_rating(player or {})
            if rating is not None:
                ratings.append(rating)

    if not ratings:
        return 1500

    return round(sum(ratings) / len(ratings))
