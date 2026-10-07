import logging
import time
import random
from io import StringIO
from typing import Any, Dict, List, Optional

import chess
import chess.pgn
from psycopg2.extras import Json, RealDictCursor

from core import database
from services.opponent_style import (
    _analyze_game,
    _blunder_ply,
    compute_opening_results,
    compute_time_control_distribution,
)
from services.opponent_traps import compute_opponent_traps

log = logging.getLogger(__name__)

# Minimum total recorded samples at a position before pick_repertoire_move
# will commit to a book move. Below this the sampler returns None and the
# caller falls through to Maia. Set to 1 so EVERY seen move is admissible:
# the user imported these games deliberately and a single real occurrence
# is signal, not noise. When 2+ moves are recorded at a position the
# recency-weighted sampler below (random.choices with weights=weighted)
# picks in proportion to frequency, so a once-seen side line gets a small
# but nonzero share vs a thrice-seen main line. Tune up only if once-seen
# exploratory moves start leaking into sparring too often.
MIN_REPERTOIRE_SAMPLES = 1

# Exponential recency decay rate, per year, applied to each recorded
# move's sampling weight. weight = frequency * exp(-lambda * age_years).
# With lambda = 0.5/yr the half-life is ln(2)/0.5 ~= 1.39 years, so:
#   1 month  -> 0.96   (recent opening prep dominates)
#   6 months -> 0.78
#   1 year   -> 0.61
#   2 years  -> 0.37
#   3 years  -> 0.22   (old habits still contribute, but weakly)
# This matches the intuition that opening repertoires evolve on a
# multi-month/year timescale, not weekly — so recent games should steer
# sampling without discarding established prep just because it's old.
# Games with end_time = 0 (missing date) get neutral weight 1.0 so a
# missing timestamp never nukes a move's candidacy.
RECENCY_DECAY_LAMBDA_PER_YEAR = 0.5
# Seconds per year (365.25 days, leap-year averaged) used to convert the
# Unix-seconds age into years inside the SQL decay expression.
_SECONDS_PER_YEAR = 365.25 * 86400.0

# Near-book repertoire similarity (feature D): half-width, in half-moves,
# of the "nearby position" window used by pick_near_repertoire_moves. A
# repertoire move is considered "near" the live position when its stored
# ply_index sits within +/-NEAR_BOOK_PLY_WINDOW of the live position's own
# ply index AND it was played from the same color (played_color). See
# pick_near_repertoire_moves' docstring for why a ply window was chosen as
# the v1 "near" gate instead of a position_key prefix match.
NEAR_BOOK_PLY_WINDOW = 2


def _position_key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def _candidate_ply_index(board: chess.Board) -> int:
    """0-indexed half-move index of the move the side-to-move is about to
    play -- the same index convention as opponent_repertoire_moves.ply_index.

    ply_index is the 0-based enumerate() index from index_opponent_game, so
    the opponent's k-th move (1-indexed ply k) is stored at ply_index k-1.
    _candidate_ply_index mirrors the reranker's _candidate_ply (which returns
    the 1-indexed ply) but subtracts 1 so it is directly comparable to the
    stored column. Start position (white to move) -> 0; after 1.e4 (black to
    move) -> 1; after 1.e4 e5 (white to move) -> 2.
    """
    return board.fullmove_number * 2 - (1 if board.turn == chess.WHITE else 0) - 1


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


def replay_opponent_game(
    pgn: str,
    *,
    timing: Optional[Dict[str, float]] = None,
) -> None:
    """Replay a PGN without writing repertoire rows."""
    started = time.perf_counter() if timing is not None else None
    game = chess.pgn.read_game(StringIO(pgn))
    if game is not None:
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)
    if started is not None:
        timing["pgn_replay_duration"] = time.perf_counter() - started


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
    timing: Optional[Dict[str, float]] = None,
    write_repertoire: bool = True,
) -> int:
    pgn_replay_started = time.perf_counter() if timing is not None else None
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None:
        return 0

    normalized_opponent = _normalize_username(opponent_username)
    white_name = _player_username(white_player or {}) or _normalize_username(
        game.headers.get("White")
    )
    black_name = _player_username(black_player or {}) or _normalize_username(
        game.headers.get("Black")
    )

    if white_name == normalized_opponent:
        opponent_color = chess.WHITE
        played_color = "white"
    elif black_name == normalized_opponent:
        opponent_color = chess.BLACK
        played_color = "black"
    else:
        return 0

    board = game.board()
    inserted = 0
    repertoire_insert_duration = 0.0
    if write_repertoire:
        with conn.cursor() as cur:
            repertoire_query_started = (
                time.perf_counter() if timing is not None else None
            )
            cur.execute(
                "DELETE FROM opponent_repertoire_moves WHERE opponent_game_id = %s",
                (game_id,),
            )
            if repertoire_query_started is not None:
                repertoire_insert_duration += (
                    time.perf_counter() - repertoire_query_started
                )
            for ply_index, move in enumerate(game.mainline_moves()):
                if board.turn == opponent_color:
                    try:
                        move_san = board.san(move)
                    except ValueError:
                        move_san = move.uci()

                    repertoire_query_started = (
                        time.perf_counter() if timing is not None else None
                    )
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
                    if repertoire_query_started is not None:
                        repertoire_insert_duration += (
                            time.perf_counter() - repertoire_query_started
                        )
                    inserted += cur.rowcount
                board.push(move)
    else:
        for move in game.mainline_moves():
            board.push(move)

    if timing is not None and pgn_replay_started is not None:
        timing["repertoire_insert_duration"] = repertoire_insert_duration
        timing["pgn_replay_duration"] = max(
            0.0,
            time.perf_counter() - pgn_replay_started - repertoire_insert_duration,
        )

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


def build_opponent_profile_snapshot(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    avatar_url: Optional[str],
    verified: bool,
) -> Dict[str, Any]:
    """Compute the complete non-Stockfish opponent-prep summary once."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                COUNT(*)::int AS game_count,
                JSONB_AGG(white_player) AS white_players,
                JSONB_AGG(black_player) AS black_players,
                JSONB_AGG(pgn) AS pgns,
                JSONB_AGG(end_time) AS end_times,
                JSONB_AGG(time_class) AS time_classes
            FROM opponent_games
            WHERE requested_by_user_id = %s
              AND provider = %s
              AND LOWER(opponent_username) = LOWER(%s)
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        row = dict(cur.fetchone())

    pgns = row.get("pgns") or []
    end_times = row.get("end_times") or []
    time_classes = row.get("time_classes") or []
    games = [
        {
            "pgn": pgn,
            "end_time": end_time,
            # DB time_class is the primary source for the per-class opening
            # breakdown; `compute_opening_results` falls back to the PGN
            # [TimeControl] header when it's empty, so backfilled and freshly
            # imported snapshots bucket identically.
            "time_class": time_class,
            "opponent_username": opponent_username,
        }
        for pgn, end_time, time_class in zip(pgns, end_times, time_classes)
    ]
    opening_results = compute_opening_results(games) if games else None
    tc_profile = compute_time_control_distribution(games) if games else None
    # Reuse the stored username casing (see _existing_snapshot_username) so
    # a re-import under different casing updates the row instead of forking.
    stored_username = _existing_snapshot_username(
        conn,
        requested_by_user_id=requested_by_user_id,
        provider=provider,
        opponent_username=opponent_username,
    )
    resolved_username = stored_username or opponent_username
    return {
        "requested_by_user_id": requested_by_user_id,
        "provider": provider,
        "opponent_username": resolved_username,
        "game_count": int(row.get("game_count") or 0),
        "rating": _rating_from_player_lists(
            opponent_username=opponent_username,
            white_players=row.get("white_players") or [],
            black_players=row.get("black_players") or [],
        ),
        "ratings_by_time_class": _ratings_by_time_class(
            opponent_username=opponent_username,
            white_players=row.get("white_players") or [],
            black_players=row.get("black_players") or [],
            time_classes=row.get("time_classes") or [],
        ),
        "playing_style": _playing_style_from_sac_freq(
            opening_results.get("weighted_sacrifice_frequency")
            if opening_results
            else None
        ),
        "preferred_time_control": (
            tc_profile["most_common"] if tc_profile else None
        ),
        "time_control_distribution": (
            tc_profile["distribution"] if tc_profile else None
        ),
        "opening_results": (
            opening_results["by_opening"] if opening_results else None
        ),
        "openings_lost_against": _openings_lost_against(
            opening_results["by_opening"]
            if opening_results and opening_results.get("by_opening")
            else None
        ),
        "avatar_url": avatar_url,
        "verified": verified,
    }


def _existing_snapshot_username(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
) -> Optional[str]:
    """Stored username casing for this opponent, if a snapshot exists.

    The snapshots UNIQUE constraint is case-sensitive while every read is
    LOWER()-based, so writing a re-import under different casing ("Hikaru"
    vs "hikaru") would create a second row and split the UI. Reusing the
    stored casing keeps one row per opponent while preserving display case.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT opponent_username
            FROM opponent_profile_snapshots
            WHERE requested_by_user_id = %s
              AND provider = %s
              AND LOWER(opponent_username) = LOWER(%s)
            LIMIT 1
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        row = cur.fetchone()
        if row is None:
            return None
        stored = dict(row).get("opponent_username")
        return stored or None


def upsert_opponent_profile_snapshot(conn, snapshot: Dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO opponent_profile_snapshots (
                requested_by_user_id,
                provider,
                opponent_username,
                game_count,
                rating,
                ratings_by_time_class,
                playing_style,
                preferred_time_control,
                time_control_distribution,
                opening_results,
                openings_lost_against,
                avatar_url,
                verified,
                computed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (requested_by_user_id, provider, opponent_username)
            DO UPDATE SET
                game_count = EXCLUDED.game_count,
                rating = EXCLUDED.rating,
                ratings_by_time_class = EXCLUDED.ratings_by_time_class,
                playing_style = EXCLUDED.playing_style,
                preferred_time_control = EXCLUDED.preferred_time_control,
                time_control_distribution = EXCLUDED.time_control_distribution,
                opening_results = EXCLUDED.opening_results,
                openings_lost_against = EXCLUDED.openings_lost_against,
                avatar_url = EXCLUDED.avatar_url,
                verified = EXCLUDED.verified,
                computed_at = NOW()
            """,
            (
                snapshot["requested_by_user_id"],
                snapshot["provider"],
                snapshot["opponent_username"],
                snapshot["game_count"],
                snapshot["rating"],
                Json(snapshot["ratings_by_time_class"]),
                snapshot["playing_style"],
                snapshot["preferred_time_control"],
                Json(snapshot["time_control_distribution"]),
                Json(snapshot["opening_results"]),
                Json(snapshot["openings_lost_against"]),
                snapshot["avatar_url"],
                snapshot["verified"],
            ),
        )


def list_opponent_profiles(*, requested_by_user_id: str) -> list[Dict[str, Any]]:
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    legacy_recalc_started: Optional[float] = None
    legacy_recalc_duration_ms: Optional[float] = None
    conn = database.connection_pool.getconn()
    try:
        snapshot_read_duration_ms = 0.0
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            snapshot_read_started = time.perf_counter()
            cur.execute(
                """
                SELECT
                    provider,
                    opponent_username,
                    game_count,
                    rating,
                    ratings_by_time_class,
                    playing_style,
                    preferred_time_control,
                    time_control_distribution,
                    opening_results,
                    avatar_url,
                    verified
                FROM opponent_profile_snapshots
                WHERE requested_by_user_id = %s
                ORDER BY computed_at DESC
                """,
                (requested_by_user_id,),
            )
            snapshot_rows = [dict(row) for row in cur.fetchall()]
            snapshot_read_duration_ms = (
                time.perf_counter() - snapshot_read_started
            ) * 1000
            cur.execute(
                """
                SELECT
                    provider,
                    opponent_username,
                    COUNT(*)::int AS game_count,
                    JSONB_AGG(white_player) AS white_players,
                    JSONB_AGG(black_player) AS black_players,
                    -- The time-control signal reads the PGN's [TimeControl]
                    -- header per game (no mainline replay — see
                    -- compute_time_control_distribution). We aggregate the
                    -- raw rows here so the per-opponent time-control
                    -- distribution computes in the same pass that builds
                    -- this profile list, avoiding a second round-trip per
                    -- opponent. JSONB_AGG preserves insertion order so the
                    -- pgns[i] / end_times[i] / time_classes[i] pairing
                    -- stays aligned.
                    JSONB_AGG(pgn) AS pgns,
                    JSONB_AGG(end_time) AS end_times,
                    JSONB_AGG(time_class) AS time_classes
                FROM opponent_games
                WHERE requested_by_user_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM opponent_profile_snapshots s
                      WHERE s.requested_by_user_id = opponent_games.requested_by_user_id
                        AND s.provider = opponent_games.provider
                        AND LOWER(s.opponent_username) = LOWER(opponent_games.opponent_username)
                  )
                GROUP BY provider, opponent_username
                ORDER BY MAX(imported_at) DESC
                """,
                (requested_by_user_id,),
            )
            rows = [dict(row) for row in cur.fetchall()]

        profiles: List[Dict[str, Any]] = []
        snapshots_to_backfill: List[Dict[str, Any]] = []
        snapshot_backfill_started = time.perf_counter()
        snapshot_trap_started = time.perf_counter()
        for row in snapshot_rows:
            profiles.append(
                {
                    "provider": row["provider"],
                    "opponent_username": row["opponent_username"],
                    "game_count": row["game_count"],
                    "rating": row["rating"],
                    "ratings_by_time_class": row.get("ratings_by_time_class"),
                    "playing_style": row.get("playing_style"),
                    "preferred_time_control": row.get("preferred_time_control"),
                    "time_control_distribution": row.get("time_control_distribution"),
                    "opening_results": row.get("opening_results"),
                    # Projected AT READ TIME from the stored raw buckets so
                    # the floor/shrinkage knobs in this module can change
                    # without a re-import. The snapshot's stored
                    # `openings_lost_against` column is no longer read here
                    # (the import/backfill still writes it for compatibility).
                    "openings_lost_against": _openings_lost_against(
                        row.get("opening_results") or None
                    ),
                    "avatar_url": row.get("avatar_url"),
                    "verified": bool(row.get("verified")),
                    "traps": compute_opponent_traps(
                        conn,
                        requested_by_user_id=requested_by_user_id,
                        provider=row["provider"],
                        opponent_username=row["opponent_username"],
                    ),
                }
            )
        log.info(
            "[IMPORT_PROFILE] phase=snapshot_path duration_ms=%.2f snapshots=%d trap_lookups=%d",
            snapshot_read_duration_ms
            + (time.perf_counter() - snapshot_trap_started) * 1000,
            len(snapshot_rows),
            len(snapshot_rows),
        )

        legacy_recalc_started = time.perf_counter() if rows else None
        for row in rows:
            pgns = row.get("pgns") or []
            end_times = row.get("end_times") or []
            time_classes = row.get("time_classes") or []
            opponent_username = row["opponent_username"]
            # Each game row carries the opponent's username so
            # `compute_opening_results` can resolve which side the
            # opponent played (the `_analyze_game` / `_opponent_color`
            # path casefolds-and-matches against the PGN's [White]/
            # [Black] headers; this is the same resolution
            # `compute_opponent_style` does, just surfaced via the row
            # because the listing path doesn't take an opponent_username
            # arg per-row the way compute_opponent_style does).
            games = [
                {
                    "pgn": pgn,
                    "end_time": end_time,
                    # Same per-class opening breakdown source as the snapshot
                    # builder above (DB column primary, PGN header fallback).
                    "time_class": time_class,
                    "opponent_username": opponent_username,
                }
                for pgn, end_time, time_class in zip(pgns, end_times, time_classes)
            ]
            # Time control: gated internally by MIN_STYLE_GAMES; for
            # opponents below the floor the distribution/most_common come
            # back None and the sparring page just doesn't prefill the
            # Time Control field.
            tc_profile = compute_time_control_distribution(games) if games else None
            # Opening W/L/D: NO floor at storage — every bucket with at
            # least one parseable game is kept raw (the Weak Openings panel
            # applies its own projection-time floor/shrinkage later, so it
            # can change without a re-import). by_opening is {} for a row
            # set with no parseable PGNs, which the Opponent Prep page
            # renders as an empty "no openings data" panel.
            opening_results = compute_opening_results(games) if games else None
            # Traps: read/aggregation over opponent_game_blunders.
            # Returns [] when zero groups qualify — the common case for
            # opponents with sparse blunder data or before the analysis
            # job has run. Uses the same conn (no extra pool checkout).
            traps = compute_opponent_traps(
                conn,
                requested_by_user_id=requested_by_user_id,
                provider=row["provider"],
                opponent_username=opponent_username,
            )
            profile = {
                "provider": row["provider"],
                "opponent_username": opponent_username,
                "game_count": row["game_count"],
                "rating": _rating_from_player_lists(
                    opponent_username=opponent_username,
                    white_players=row.get("white_players") or [],
                    black_players=row.get("black_players") or [],
                ),
                "ratings_by_time_class": _ratings_by_time_class(
                    opponent_username=opponent_username,
                    white_players=row.get("white_players") or [],
                    black_players=row.get("black_players") or [],
                    time_classes=time_classes,
                ),
                "playing_style": _playing_style_from_sac_freq(
                    opening_results.get("weighted_sacrifice_frequency")
                    if opening_results
                    else None
                ),
                "preferred_time_control": (
                    tc_profile["most_common"] if tc_profile else None
                ),
                "time_control_distribution": (
                    tc_profile["distribution"] if tc_profile else None
                ),
                # Per-opening buckets are the SAME family labels
                # `opening_family_lean` (in compute_opponent_style)
                # produces — both go through `_analyze_game`'s single
                # `_opening_family(game)` call, so the Opponent Prep
                # page's frequency and results views zip together by
                # key without a remap.
                "opening_results": (
                    opening_results["by_opening"] if opening_results else None
                ),
                "openings_lost_against": _openings_lost_against(
                    opening_results["by_opening"]
                    if opening_results and opening_results.get("by_opening")
                    else None
                ),
                "traps": traps,
                "avatar_url": None,
                "verified": False,
            }
            profiles.append(profile)
            snapshot = dict(profile)
            snapshot["requested_by_user_id"] = requested_by_user_id
            snapshot.pop("traps", None)
            snapshots_to_backfill.append(snapshot)
        if legacy_recalc_started is not None:
            legacy_recalc_duration_ms = (
                time.perf_counter() - legacy_recalc_started
            ) * 1000
        if snapshots_to_backfill:
            try:
                for snapshot in snapshots_to_backfill:
                    upsert_opponent_profile_snapshot(conn, snapshot)
                conn.commit()
                log.info(
                    "[IMPORT_PROFILE] phase=snapshot_backfill snapshots=%d games=%d duration_ms=%.2f",
                    len(snapshots_to_backfill),
                    sum(int(snapshot["game_count"] or 0) for snapshot in snapshots_to_backfill),
                    (time.perf_counter() - snapshot_backfill_started) * 1000,
                )
            except Exception:
                conn.rollback()
                log.exception(
                    "Failed to backfill opponent profile snapshots for user %s",
                    requested_by_user_id,
                )
        return profiles
    finally:
        if legacy_recalc_started is not None:
            if legacy_recalc_duration_ms is None:
                legacy_recalc_duration_ms = (
                    time.perf_counter() - legacy_recalc_started
                ) * 1000
            log.info(
                "[IMPORT_PROFILE] phase=legacy_recalc duration_ms=%.2f opponents=%d games=%d",
                legacy_recalc_duration_ms,
                len(rows),
                sum(int(row.get("game_count") or 0) for row in rows),
            )
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
                    r.move_uci,
                    MIN(r.move_san) AS move_san,
                    COUNT(*)::int AS frequency,
                    -- Per-move recency-weighted frequency. Each repertoire
                    -- row is one move occurrence in one game, so we join to
                    -- opponent_games for that game's end_time and sum the
                    -- exponential decay across all occurrences of the move.
                    -- end_time = 0 (missing) -> neutral weight 1.0 so a bad
                    -- timestamp never zeroes out a real move.
                    SUM(
                        CASE
                            WHEN g.end_time > 0
                                THEN exp(
                                    ( -%s
                                      * EXTRACT(EPOCH FROM (NOW() - to_timestamp(g.end_time)))
                                      / %s
                                    )::double precision
                                )
                            ELSE 1.0
                        END
                    )::double precision AS weighted_frequency
                FROM opponent_repertoire_moves r
                JOIN opponent_games g ON g.id = r.opponent_game_id
                WHERE r.requested_by_user_id = %s
                  AND r.provider = %s
                  AND LOWER(r.opponent_username) = LOWER(%s)
                  AND r.position_key = %s
                GROUP BY r.move_uci
                """,
                (
                    RECENCY_DECAY_LAMBDA_PER_YEAR,
                    _SECONDS_PER_YEAR,
                    requested_by_user_id,
                    provider,
                    opponent_username,
                    _position_key(board),
                ),
            )
            rows = [dict(row) for row in cur.fetchall()]

        if not rows:
            return None

        # Data floor: total raw samples at this position must clear the
        # threshold, otherwise we refuse to sample and let the caller fall
        # through to Maia. Evaluated on raw frequency (how many times we've
        # actually seen the position), never on the decayed weight, so that
        # old-but-voluminous positions still qualify.
        total_samples = sum(int(row["frequency"]) for row in rows)
        if total_samples < MIN_REPERTOIRE_SAMPLES:
            return None

        weighted = [float(row["weighted_frequency"] or 0.0) for row in rows]
        total_weighted = sum(weighted)
        # Defensive fallback: if every game had a wildly old end_time such
        # that exp() underflowed to 0.0 (impossible for real online chess,
        # but cheap to guard), sample on raw frequency so we never feed
        # random.choices an all-zero weight list.
        if total_weighted <= 0.0:
            weighted = [float(int(row["frequency"])) for row in rows]
            total_weighted = sum(weighted)
        if total_weighted <= 0.0:
            return None

        choice = random.choices(rows, weights=weighted, k=1)[0]
        return choice
    finally:
        database.connection_pool.putconn(conn)


def pick_near_repertoire_moves(
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    board: chess.Board,
    ply_window: int = NEAR_BOOK_PLY_WINDOW,
) -> Optional[Dict[str, float]]:
    """Near-book repertoire similarity (feature D) data lookup.

    Returns a recency-weighted ``{move_uci: weight}`` map of the moves the
    opponent has played from repertoire positions NEAR the live position --
    the "near-book" extension of mirror-mode that fires only once
    ``pick_repertoire_move`` has returned no exact book hit.

    DEFINITION OF "NEAR" (v1). A repertoire move is near the live position
    iff BOTH hold:

      (a) SAME COLOR: ``r.played_color`` equals the live side to move's
          color. Repertoire position_keys are always "opponent to move"
          positions (see index_opponent_game), and the live board is the
          bot's turn == the opponent's color, so this keeps the comparison
          inside the opponent's same-color games.

      (b) PLY WINDOW: ``r.ply_index`` is within +/- ``ply_window`` of the
          live position's ``_candidate_ply_index``. This is the "near"
          gate: the opponent reached a position at roughly the same stage
          of the game (same move number) in another game.

    Why a ply window and not a position_key prefix match (the guidance's
    first-listed option): a prefix match would require scanning EVERY
    repertoire position_key and parsing each FEN's piece-placement field
    (O(rows) string work per sparring move, no usable index on "similar"
    keys), and it would substantially overlap the existing
    setup_signatures Jaccard bias (which already measures board-shape
    similarity to historic snapshots) -- D would double-count that axis.
    Shared opening family was rejected because the reranker has already
    documented (opponent_style_reranker decision (1)) that family-lean has
    no per-candidate classifier and so cannot bias candidates; adopting it
    here would require building that classifier (out of scope). The ply
    window is cheap, indexed on the opponent columns, and unambiguous: it
    profiles the opponent's MOVE-ORDER tendency at this game stage (the
    "spirit of the repertoire" beyond the exact position), which is exactly
    what near-book should add on top of exact-book.

    DATA FOUNDATION. Reads the SAME recency-weighted repertoire data as
    pick_repertoire_move: opponent_repertoire_moves JOIN opponent_games,
    with the SAME exponential decay (RECENCY_DECAY_LAMBDA_PER_YEAR=0.5,
    end_time=0 -> neutral 1.0). It does NOT re-query raw unweighted move
    counts, so a near move's weight ages exactly like an exact book move's.
    No time-control weighting, deliberately: openings transfer across TCs
    better than tactical style (same documented choice as the exact book
    path).

    The live position's OWN exact position_key is excluded from the window
    (``position_key <> live``) so this can never re-express a move the
    exact-book path would have owned. That filter is defensive -- by the
    time this runs the exact key has no rows (pick_repertoire_move would
    have returned a hit under MIN_REPERTOIRE_SAMPLES=1) -- but it makes the
    sequencing explicit.

    Returns None (the "no near-book signal" result) when the window yields
    no rows, when the raw-sample floor (MIN_REPERTOIRE_SAMPLES, same
    contract as pick_repertoire_move) is not cleared, or when the decayed
    weights all underflow to zero. Callers must treat None as "fall through
    to today's mirror-mode with no change".

    Raises RuntimeError when the DB pool is not initialized (matches the
    sibling pickers); a transient DB error propagates to the caller, which
    (in the sparring router) catches it and degrades to mirror-mode.
    """
    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    played_color = "white" if board.turn == chess.WHITE else "black"
    current_ply_index = _candidate_ply_index(board)

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    r.move_uci,
                    COUNT(*)::int AS frequency,
                    -- Per-move recency-weighted frequency, IDENTICAL decay
                    -- expression to pick_repertoire_move so near-book and
                    -- exact-book weights age on the same curve.
                    SUM(
                        CASE
                            WHEN g.end_time > 0
                                THEN exp(
                                    ( -%s
                                      * EXTRACT(EPOCH FROM (NOW() - to_timestamp(g.end_time)))
                                      / %s
                                    )::double precision
                                )
                            ELSE 1.0
                        END
                    )::double precision AS weighted_frequency
                FROM opponent_repertoire_moves r
                JOIN opponent_games g ON g.id = r.opponent_game_id
                WHERE r.requested_by_user_id = %s
                  AND r.provider = %s
                  AND LOWER(r.opponent_username) = LOWER(%s)
                  AND r.played_color = %s
                  AND r.ply_index BETWEEN %s AND %s
                  AND r.position_key <> %s
                GROUP BY r.move_uci
                """,
                (
                    RECENCY_DECAY_LAMBDA_PER_YEAR,
                    _SECONDS_PER_YEAR,
                    requested_by_user_id,
                    provider,
                    opponent_username,
                    played_color,
                    current_ply_index - ply_window,
                    current_ply_index + ply_window,
                    _position_key(board),
                ),
            )
            rows = [dict(row) for row in cur.fetchall()]

        if not rows:
            return None

        # Same per-position floor contract as pick_repertoire_move, on RAW
        # frequency (how many near occurrences we've actually seen), so old-
        # but-voluminous near positions still qualify.
        total_samples = sum(int(row["frequency"]) for row in rows)
        if total_samples < MIN_REPERTOIRE_SAMPLES:
            return None

        weights: Dict[str, float] = {}
        for row in rows:
            w = float(row["weighted_frequency"] or 0.0)
            if w > 0.0:
                weights[row["move_uci"]] = weights.get(row["move_uci"], 0.0) + w

        total_weighted = sum(weights.values())
        # Defensive underflow fallback: if every contributing game was so old
        # that exp() underflowed to 0.0, fall back to raw frequency so we
        # never hand the reranker an empty weight map.
        if total_weighted <= 0.0:
            weights = {
                row["move_uci"]: float(int(row["frequency"])) for row in rows
            }
            total_weighted = sum(weights.values())
        if total_weighted <= 0.0:
            return None

        return weights
    finally:
        database.connection_pool.putconn(conn)


def _rating_from_player_lists(
    *,
    opponent_username: str,
    white_players: list[Dict[str, Any]],
    black_players: list[Dict[str, Any]],
) -> Optional[int]:
    """Mean of the opponent's parseable per-game ratings, or None.

    None (not a fabricated number) when the corpus carries no usable rating
    for the opponent, so callers must choose an explicit fallback instead of
    silently sparring/presenting at 1500.
    """
    normalized_opponent = _normalize_username(opponent_username)
    ratings: list[int] = []

    for player in white_players + black_players:
        if _player_username(player or {}) == normalized_opponent:
            rating = _player_rating(player or {})
            if rating is not None:
                ratings.append(rating)

    if not ratings:
        return None

    return round(sum(ratings) / len(ratings))


# Last-resort bot strength when the opponent's corpus carries no parseable
# rating. Used ONLY via resolve_opponent_elo_for_sparring so every use is
# logged and flagged (never a silent 1500).
FALLBACK_OPPONENT_ELO = 1500


def resolve_opponent_elo_for_sparring(
    opponent_rating: Optional[int],
    *,
    context: str,
) -> tuple[int, bool]:
    """Resolve the bot's playing strength plus whether it was estimated.

    Returns (elo, estimated). A real corpus rating is authoritative; otherwise
    the fallback applies loudly (warning log) and the caller must surface
    `estimated` so the UI can disclose it.
    """
    if opponent_rating is not None:
        return opponent_rating, False
    log.warning(
        "opponent rating unavailable (%s); using estimated %d",
        context,
        FALLBACK_OPPONENT_ELO,
    )
    return FALLBACK_OPPONENT_ELO, True


def get_user_playing_elo(
    *,
    requested_by_user_id: str,
) -> tuple[Optional[int], str]:
    """The sparring user's own playing strength for Maia's oppo_elo.

    Prefers users.tactical_rating, then the onboarding skill-band midpoint
    (same rule as GET /api/user/rating). Returns (elo, source) where source
    is "tactical_rating", "skill_band:<level>", or "unknown" when the user
    has neither. Callers fall back to the opponent's elo (legacy symmetric
    behavior) only on "unknown" — and must log that choice.
    """
    # Local import: routers.puzzles never imports services, so no cycle;
    # kept function-local to preserve the services-never-import-routers rule.
    from routers.puzzles import SKILL_RATING_BANDS

    if database.connection_pool is None:
        raise RuntimeError("Database connection pool is not initialized")

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT tactical_rating, skill_level
                FROM users WHERE clerk_id = %s
                """,
                (requested_by_user_id,),
            )
            row = cur.fetchone()
    finally:
        database.connection_pool.putconn(conn)

    if row is None:
        return None, "unknown"
    tactical_rating = row.get("tactical_rating")
    if isinstance(tactical_rating, bool):
        tactical_rating = None
    if isinstance(tactical_rating, int) and tactical_rating > 0:
        return tactical_rating, "tactical_rating"
    skill_level = row.get("skill_level")
    band = SKILL_RATING_BANDS.get(skill_level) if skill_level else None
    if band:
        return (band[0] + band[1]) // 2, f"skill_band:{skill_level}"
    return None, "unknown"


# Thresholds mapping recency-weighted sacrifice frequency to a
# "playing style" pill label. Chosen to roughly match the spec's
# "Passive / Balanced / Aggressive" bins against the v1 sacrifice
# heuristic's typical output range:
#   * < 0.05    -> "Passive"    (below 1 sac per 20 opponent moves;
#                                 defensively patient play)
#   * 0.05-0.15 -> "Balanced"   (1 sac per 7-20 moves; mix of safety
#                                 and tactical resource-giving)
#   * >= 0.15   -> "Aggressive" (1 sac per ~7 moves or more; the
#                                 profile that needs cautious prep)
# These are deliberately coarse — the spec asks for a single-word pill,
# not a calibrated aggression index. Tune the bands only if a real
# opponent's corpus lands off the chart (the existing
# compute_opening_results test fixtures all sit at 0.0 -> Passive).
_SAC_FREQ_BANDS: tuple[tuple[float, str], ...] = (
    (0.05, "Passive"),
    (0.15, "Balanced"),
    # Anything >= 0.15 falls through to the implicit "Aggressive" label
    # below — kept as a single literal so the Linter doesn't flag a
    # tuple-with-no-final-element. The iteration above catches <= 0.15
    # exactly; >= 0.15 returns "Aggressive" via the fall-through return.
)


def _playing_style_from_sac_freq(
    weighted_sacrifice_frequency: Optional[float],
) -> Optional[str]:
    """Map a recency-weighted sacrifice rate to a single-word pill.

    Returns None iff the corpus had zero opponent moves (defensive —
    see compute_opening_results' docstring on `weighted_sacrifice_frequency`).
    Otherwise returns one of "Passive" / "Balanced" / "Aggressive" per the
    bands above. The pill is a SPEC-level UI label, not a calibrated
    psychometric score — the bands are coarse on purpose.
    """
    if weighted_sacrifice_frequency is None:
        return None
    for threshold, label in _SAC_FREQ_BANDS:
        if weighted_sacrifice_frequency < threshold:
            return label
    return "Aggressive"


# Time-class labels we surface on the per-time-class rating row. The
# provider's `time_class` string is normalized to one of these — a
# free-form label like "daily" or "correspondence" is folded into
# "daily" (Lichess uses both spellings; Chess.com uses "daily" only).
_TIME_CLASS_CANONICAL = {
    "bullet": "bullet",
    "blitz": "blitz",
    "rapid": "rapid",
    "classical": "classical",
    "daily": "daily",
    "correspondence": "daily",
}


def _canonical_time_class(raw: Optional[str]) -> Optional[str]:
    """Normalize a provider's time-class string to the canonical row labels.

    Returns None for empty/unknown time classes (the game is excluded from
    the per-time-class average rather than folded into an "Other" bin —
    keeping the row to its four canonical labels only).
    """
    if not raw:
        return None
    key = str(raw).strip().lower()
    return _TIME_CLASS_CANONICAL.get(key)


def _ratings_by_time_class(
    *,
    opponent_username: str,
    white_players: list[Dict[str, Any]],
    black_players: list[Dict[str, Any]],
    time_classes: list[str],
) -> Optional[Dict[str, int]]:
    """Average opponent rating per time-class bucket, indexed by canonical label.

    Iterates the parallel white_players/black_players/time_classes arrays
    (same length — JSONB_AGG preserves order in this query's GROUP BY),
    filters to entries where the opponent played (matched by casefolded
    username against white/black player dict), and accumulates their
    per-game rating into the bucket matching that game's time_class.

    A bucket's value is the rounded MEAN of the opponent's per-game
    ratings in that bucket — same averaging convention as the overall
    `rating` field, just scoped to a time class. A bucket is OMITTED
    from the returned dict when the opponent has zero games at that
    speed (so callers can render "—" vs an integer).

    Returns None iff the opponent had no games with a parseable
    rating AND time-class — distinguishable from "{}", which means
    "had games but none in any canonical bucket" (effectively zero
    games on file, defensive).
    """
    normalized = _normalize_username(opponent_username)
    # length of all three lists must match — the SQL JSONB_AGGs in the
    # listing query align them via row order, so any length mismatch is a
    # backend bug worth surfacing rather than silently hiding.
    if not (len(white_players) == len(black_players) == len(time_classes)):
        log.warning(
            "ratings_by_time_class: list-length mismatch for %s/%s (w=%d b=%d tc=%d) — skipping",
            opponent_username,
            "<unknown provider>",
            len(white_players),
            len(black_players),
            len(time_classes),
        )
        return None

    buckets: Dict[str, list[int]] = {}
    for player, time_class in zip(
        white_players + black_players, time_classes + time_classes
    ):
        if _player_username(player or {}) != normalized:
            continue
        rating = _player_rating(player or {})
        if rating is None:
            continue
        canonical = _canonical_time_class(time_class)
        if canonical is None:
            continue
        buckets.setdefault(canonical, []).append(rating)

    if not buckets:
        return None
    return {label: round(sum(rs) / len(rs)) for label, rs in buckets.items()}


# --- Weak Openings projection knobs ----------------------------------------
#
# These are PROJECTION-time knobs. Storage keeps every bucket raw (counts by
# color and time class — see compute_opening_results), so tuning any of them
# costs no re-import. The floor is deliberately on the RAW game count, never
# on recency-weighted effective samples: a weighted floor silently purges
# small-but-real buckets (the documented style-signal gap we're avoiding).
OPENING_MIN_RAW_GAMES = 5           # floor for a normal (ranked) row
OPENING_LOW_SAMPLE_MIN_GAMES = 3    # backfill floor when too few qualify
OPENING_MIN_QUALIFIED_BUCKETS = 3   # below this, backfill with low-sample rows
OPENING_LOSS_SHRINKAGE_PRIOR = 5.0  # pseudo-games; ~the floor size


def _shrunk_loss_rate(
    losses: int, decided_or_drawn: int, prior_rate: float
) -> float:
    """Empirical-Bayes shrink of a bucket's loss rate toward the opponent's
    overall loss rate (`prior_rate`), with `OPENING_LOSS_SHRINKAGE_PRIOR`
    pseudo-games of prior weight. A 1/1 bucket no longer reads 100% unless
    the opponent loses everything everywhere."""
    return (losses + OPENING_LOSS_SHRINKAGE_PRIOR * prior_rate) / (
        decided_or_drawn + OPENING_LOSS_SHRINKAGE_PRIOR
    )


def _legacy_openings_lost_against(
    by_opening: Dict[str, Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Pre-raw projection for snapshots built before raw counts existed.

    Kept until the backfill script (or the next import) rewrites old
    snapshots. `_unknown` is dropped here too so the panel copy is
    consistent across both paths. Rows carry the same transitional display
    keys (`name`, `loss_percentage`, `games`) plus `legacy=True` so the UI
    can tag them.
    """
    rows: list[Dict[str, Any]] = []
    for name, stats in by_opening.items():
        if name == "_unknown":
            continue
        weighted_wins = float(stats.get("weighted_wins") or 0.0)
        weighted_losses = float(stats.get("weighted_losses") or 0.0)
        weighted_draws = float(stats.get("weighted_draws") or 0.0)
        decided_or_drawn = weighted_wins + weighted_losses + weighted_draws
        if decided_or_drawn <= 0.0:
            continue
        loss_percentage = round(weighted_losses / decided_or_drawn, 4)
        games = int(round(stats.get("weighted_count") or 0))
        rows.append(
            {
                "name": name,
                "family": name,
                "color": None,
                "raw_games": games,
                "raw_wins": None,
                "raw_losses": None,
                "raw_draws": None,
                "loss_rate": loss_percentage,
                "low_sample": False,
                "legacy": True,
                "loss_percentage": loss_percentage,
                "games": games,
            }
        )
    rows.sort(key=lambda row: row["loss_percentage"], reverse=True)
    return rows


def _openings_lost_against(
    by_opening: Optional[Dict[str, Dict[str, Any]]],
) -> list[Dict[str, Any]]:
    """Project opening_results into the "Weak Openings" panel's row shape.

    RAW path (snapshots with the stored per-color raw counts):
      * one row per (family, color) — sides are never merged;
      * floor `OPENING_MIN_RAW_GAMES` on the RAW count (aborted games
        included, matching the stored bucket size);
      * loss rate shrunk toward the opponent's OVERALL loss rate (computed
        over every stored bucket, `_unknown` included, so projection-time
        filters can't bias the prior);
      * `_unknown` family dropped from the rows (still in the prior);
      * when fewer than `OPENING_MIN_QUALIFIED_BUCKETS` rows clear the
        floor, backfill with `OPENING_LOW_SAMPLE_MIN_GAMES`-to-floor rows
        tagged `low_sample=True` so the panel never empties out on a thin
        corpus.

    LEGACY path (snapshot predates raw counts): the original weighted
    projection, so a stale snapshot still renders until backfilled.

    Empty list when by_opening is None (no parseable PGNs at all).
    """
    if not by_opening:
        return []

    # Legacy iff any bucket lacks raw counts. A partially-migrated snapshot
    # cannot occur in practice; treating it as legacy keeps the two paths
    # from mixing units.
    if not all("raw_count" in stats for stats in by_opening.values()):
        return _legacy_openings_lost_against(by_opening)

    total_wins = sum(int(s.get("raw_wins") or 0) for s in by_opening.values())
    total_losses = sum(int(s.get("raw_losses") or 0) for s in by_opening.values())
    total_draws = sum(int(s.get("raw_draws") or 0) for s in by_opening.values())
    overall_decided = total_wins + total_losses + total_draws
    prior_rate = (total_losses / overall_decided) if overall_decided > 0 else 0.5

    qualified: list[Dict[str, Any]] = []
    low_sample: list[Dict[str, Any]] = []

    for family, stats in by_opening.items():
        if family == "_unknown":
            continue
        for color, counts in (stats.get("raw_by_color") or {}).items():
            raw_games = int(counts.get("count") or 0)
            wins = int(counts.get("wins") or 0)
            losses = int(counts.get("losses") or 0)
            draws = int(counts.get("draws") or 0)
            decided = wins + losses + draws
            if decided <= 0:
                # Every game in this side of the family was "*" aborted:
                # no result signal, so no loss rate to show.
                continue
            loss_rate = round(_shrunk_loss_rate(losses, decided, prior_rate), 4)
            row = {
                "name": f"{family} · as {color.capitalize()}",
                "family": family,
                "color": color,
                "raw_games": raw_games,
                "raw_wins": wins,
                "raw_losses": losses,
                "raw_draws": draws,
                "loss_rate": loss_rate,
                "low_sample": raw_games < OPENING_MIN_RAW_GAMES,
                # Transitional keys for the current UI; the UI sub-step
                # switches to the structured fields above.
                "loss_percentage": loss_rate,
                "games": raw_games,
            }
            if raw_games >= OPENING_MIN_RAW_GAMES:
                qualified.append(row)
            elif raw_games >= OPENING_LOW_SAMPLE_MIN_GAMES:
                low_sample.append(row)

    def _sort_key(row: Dict[str, Any]) -> tuple:
        return (-row["loss_rate"], -row["raw_games"], row["name"])

    qualified.sort(key=_sort_key)
    low_sample.sort(key=_sort_key)

    if len(qualified) < OPENING_MIN_QUALIFIED_BUCKETS:
        needed = OPENING_MIN_QUALIFIED_BUCKETS - len(qualified)
        return qualified + low_sample[:needed]
    return qualified


_OPENING_RESULT_RANK = {"loss": 0, "draw": 1, "win": 2}


def _opening_game_sort_key(game: Dict[str, Any]) -> tuple:
    """Losses first, then draws, then wins; newest first inside each group.

    "*" (unresolved/aborted) sinks to the end. The Weak Openings panel is a
    weakness view, so the games the opponent lost lead the list; recency
    orders the games within each result group.
    """
    return (
        _OPENING_RESULT_RANK.get(str(game.get("result") or ""), 3),
        -int(game.get("end_time") or 0),
    )


def _order_opening_games(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(games, key=_opening_game_sort_key)


def _initial_opening_game_id(games: List[Dict[str, Any]]) -> Optional[str]:
    """The game the replay modal opens on.

    `games` is already ordered (losses newest-first), so this is the first
    loss with an opponent-side blunder row — the same game the old
    representative lookup returned — falling back to the newest loss, then
    the newest game.
    """
    for game in games:
        if game.get("result") == "loss" and game.get("first_blunder"):
            return game["game_id"]
    for game in games:
        if game.get("result") == "loss":
            return game["game_id"]
    return games[0]["game_id"] if games else None


def _blunder_precedence(entry: Dict[str, Any]) -> tuple:
    """Sort key for picking the error a game's jump points at.

    Blunders outrank mistakes (a blunder is the more instructive moment),
    and within a class the earliest ply wins. So the jump is the earliest
    blunder in the game, or — when the game has no blunder — the earliest
    mistake.
    """
    return (
        0 if entry.get("classification") == "blunder" else 1,
        int(entry.get("ply") or 0),
    )


def find_opening_games(
    conn,
    *,
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    family: str,
    color: str,
) -> Optional[Dict[str, Any]]:
    """Every stored game in one Weak Openings bucket, ordered for review.

    Resolution is server-side only: the client supplies the bucket identity
    (provider/username/family/color), never a game URL, and the family is
    re-derived from the PGN with the SAME `_analyze_game` binning the Weak
    Openings projection uses — so the list always matches the bucket row it
    was opened from. Summaries come back WITHOUT the PGN (real buckets hold
    100+ games); the UI fetches one game by `game_id` on demand.

    Returns None when the bucket has no parseable game (deleted corpus,
    stale UI row, wrong filter) — the endpoint turns that into a clean 404.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT g.id::text AS game_id, g.pgn, g.end_time, g.time_class,
                   EXISTS (
                       SELECT 1 FROM opponent_game_analysis a
                       WHERE a.game_id = g.id
                   ) AS analyzed
            FROM opponent_games g
            WHERE g.requested_by_user_id = %s
              AND g.provider = %s
              AND LOWER(g.opponent_username) = LOWER(%s)
            ORDER BY g.end_time DESC
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        games = [dict(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT game_id::text AS game_id, fen, position_key, move_number,
                   move_san, classification
            FROM opponent_game_blunders
            WHERE requested_by_user_id = %s
              AND provider = %s
              AND LOWER(opponent_username) = LOWER(%s)
            """,
            (requested_by_user_id, provider, opponent_username),
        )
        blunder_rows = [dict(row) for row in cur.fetchall()]

    # Jump target per game: earliest BLUNDER, else earliest mistake (see
    # `_blunder_precedence`). Analysis already targets only the opponent's
    # color, but the FEN turn is re-checked defensively so the other
    # player's move can never surface as "his" error.
    first_blunder_by_game: Dict[str, Dict[str, Any]] = {}
    for row in blunder_rows:
        fen = row.get("fen") or ""
        parts = fen.split(" ")
        side = "white" if len(parts) > 1 and parts[1] == "w" else "black"
        if side != color:
            continue
        move_number = int(row.get("move_number") or 0)
        entry = {
            "move_number": move_number,
            "ply": _blunder_ply(move_number, side),
            "move_san": row.get("move_san") or "",
            "classification": row.get("classification") or "mistake",
            "position_key": row.get("position_key") or "",
        }
        existing = first_blunder_by_game.get(row["game_id"])
        if existing is None or _blunder_precedence(entry) < _blunder_precedence(existing):
            first_blunder_by_game[row["game_id"]] = entry

    bucket_games: List[Dict[str, Any]] = []
    # _analyze_game expects an already-casefolded name (it compares against
    # casefolded PGN headers); passing the raw request casing silently drops
    # every game for mixed-case usernames.
    normalized_username = _normalize_username(opponent_username)
    for game in games:
        analyzed = _analyze_game(game.get("pgn") or "", normalized_username)
        if analyzed is None:
            continue
        if analyzed["family"] != family or analyzed["opponent_color"] != color:
            continue
        bucket_games.append(
            {
                "game_id": game["game_id"],
                "result": analyzed["result"],
                "end_time": int(game.get("end_time") or 0),
                "time_class": game.get("time_class") or "",
                "analyzed": bool(game.get("analyzed")),
                "first_blunder": first_blunder_by_game.get(game["game_id"]),
            }
        )

    if not bucket_games:
        return None

    ordered = _order_opening_games(bucket_games)
    return {
        "initial_game_id": _initial_opening_game_id(ordered),
        "games": ordered,
    }


def get_opening_game(
    conn,
    *,
    requested_by_user_id: str,
    game_id: str,
) -> Optional[Dict[str, Any]]:
    """One stored game (with its PGN) by id, scoped to its owner.

    The client only ever receives ids from `find_opening_games`; scoping the
    lookup to `requested_by_user_id` keeps another user's corpus unreachable
    even if an id is guessed.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id::text AS game_id, game_url, pgn,
                   white_player, black_player, result, end_time, time_class
            FROM opponent_games
            WHERE id::text = %s AND requested_by_user_id = %s
            """,
            (game_id, requested_by_user_id),
        )
        row = cur.fetchone()

    if row is None:
        return None

    game = dict(row)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT position_key, move_san, classification, move_number, fen
            FROM opponent_game_blunders
            WHERE game_id = %s::uuid
            ORDER BY move_number ASC
            """,
            (game["game_id"],),
        )
        blunder_rows = [dict(r) for r in cur.fetchall()]

    blunders: List[Dict[str, Any]] = []
    for brow in blunder_rows:
        fen = brow.get("fen") or ""
        parts = fen.split(" ")
        side = "white" if len(parts) > 1 and parts[1] == "w" else "black"
        move_number = int(brow.get("move_number") or 0)
        blunders.append(
            {
                "move_number": move_number,
                "ply": _blunder_ply(move_number, side),
                "move_san": brow.get("move_san") or "",
                "classification": brow.get("classification") or "mistake",
                "position_key": brow.get("position_key") or "",
            }
        )

    return {
        "game_id": game["game_id"],
        "game_url": game.get("game_url") or "",
        "pgn": game.get("pgn") or "",
        "white": (game.get("white_player") or {}).get("username") or "",
        "black": (game.get("black_player") or {}).get("username") or "",
        "result": game.get("result") or "",
        "end_time": int(game.get("end_time") or 0),
        "time_class": game.get("time_class") or "",
        "blunders": blunders,
    }
