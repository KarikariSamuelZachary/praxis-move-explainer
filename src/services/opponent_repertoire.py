import logging
import random
from io import StringIO
from typing import Any, Dict, List, Optional

import chess
import chess.pgn
from psycopg2.extras import RealDictCursor

from core import database
from services.opponent_style import (
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
                GROUP BY provider, opponent_username
                ORDER BY MAX(imported_at) DESC
                """,
                (requested_by_user_id,),
            )
            rows = [dict(row) for row in cur.fetchall()]

        profiles: List[Dict[str, Any]] = []
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
                    "opponent_username": opponent_username,
                }
                for pgn, end_time in zip(pgns, end_times)
            ]
            # Time control: gated internally by MIN_STYLE_GAMES; for
            # opponents below the floor the distribution/most_common come
            # back None and the sparring page just doesn't prefill the
            # Time Control field.
            tc_profile = compute_time_control_distribution(games) if games else None
            # Opening W/L/D: NO floor here (the spec for "Openings He Lost
            # Against" is deliberately floor-less — every bucket with at
            # least one game is shown, however small). by_opening is {} for
            # a row set with no parseable PGNs, which the Opponent Prep
            # page renders as an empty "no openings data" panel.
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
            profiles.append(
                {
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
                }
            )
        return profiles
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


def _openings_lost_against(
    by_opening: Optional[Dict[str, Dict[str, Any]]],
) -> list[Dict[str, Any]]:
    """Project opening_results into the "Lost Against" panel's row shape.

    For each bucket, computes `loss_percentage =
    weighted_losses / (weighted_wins + weighted_losses + weighted_draws)`
    over the same recency-weighted W/L/D counts opening_results exposes.
    Buckets whose decided-or-drawn total is 0 (every game was "*"
    aborted, OR the bucket is empty) are excluded — same contract
    `win_rate`'s not-None case signals, so the percentage is always
    meaningful when shown.

    Sorted by descending loss_percentage so the Opponent Prep panel's
    "most-lost-against-first" ordering is preserved at the API layer
    (the frontend doesn't need to re-sort). Empty list when by_opening
    is None (no parseable PGNs at all).
    """
    if not by_opening:
        return []
    rows: list[Dict[str, Any]] = []
    for name, stats in by_opening.items():
        weighted_wins = float(stats.get("weighted_wins") or 0.0)
        weighted_losses = float(stats.get("weighted_losses") or 0.0)
        weighted_draws = float(stats.get("weighted_draws") or 0.0)
        decided_or_drawn = weighted_wins + weighted_losses + weighted_draws
        if decided_or_drawn <= 0.0:
            continue
        rows.append(
            {
                "name": name,
                "loss_percentage": round(weighted_losses / decided_or_drawn, 4),
                "games": int(round(stats.get("weighted_count") or 0)),
            }
        )
    rows.sort(key=lambda row: row["loss_percentage"], reverse=True)
    return rows
