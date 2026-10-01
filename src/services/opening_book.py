"""
Opening-book lookup for Game Review move classification.

Chess.com classifies a move as "Book" when it is a conventional opening
move, and their 2023 Game Review update explicitly overhauled the book to
align with well-known opening theory. Their corpus is proprietary, so this
module approximates it with an open, local book:

  * theory lines from the CC0 `lichess-org/chess-openings` dataset, and/or
  * frequency-counted moves from a masters/high-rated PGN dump.

Both are written to the `opening_book_moves` table (position_key -> move)
by `scripts/build_opening_book.py`, keyed by the first 4 FEN fields
(board, side to move, castling, en passant) so transpositions match. The
lookup is an in-process cache with a TTL, so a rebuilt book is picked up
within `_BOOK_CACHE_TTL_SECONDS` without restarting the backend.

Contiguity ("once a game leaves the book it never re-enters") is enforced
by the caller (`core.game_analyzer.GameAnalyzer`), not here.
"""
import logging
import os
import threading
import time
from io import StringIO
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Tuple

import chess
import chess.pgn
from psycopg2.extras import execute_values

from core import database

log = logging.getLogger(__name__)

# A rebuilt book (new source rows) becomes visible after this TTL without a
# backend restart; the build script runs in its own process and cannot clear
# this process's cache directly.
_BOOK_CACHE_TTL_SECONDS = 600

_book_cache: Optional[Tuple[float, Dict[str, FrozenSet[str]]]] = None
# Timestamp of the last empty/failed load. Empty and failed loads are never
# cached (so a later rebuild is picked up); this only rate-limits retries to
# avoid hammering the database on every lookup while the table is missing.
_BOOK_EMPTY_RETRY_SECONDS = 15
_book_empty_since: Optional[float] = None
_book_lock = threading.Lock()


def position_key(board: chess.Board) -> str:
    """First 4 FEN fields — the transposition key shared by the repo.

    Same convention as the repertoire sampler's position key and the
    `opponent_game_blunders.position_key` column: board, side to move,
    castling rights, en-passant target. Move counters are excluded.
    """
    return " ".join(board.fen().split(" ")[:4])


# ---------------------------------------------------------------------------
# Lookup (hot path)
# ---------------------------------------------------------------------------

def _load_book_from_db() -> Dict[str, FrozenSet[str]]:
    if database.connection_pool is None:
        return {}
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT position_key, move_uci FROM opening_book_moves")
            rows = cur.fetchall()
    finally:
        database.connection_pool.putconn(conn)

    book: Dict[str, set] = {}
    for key, move_uci in rows:
        book.setdefault(key, set()).add(move_uci)
    return {key: frozenset(moves) for key, moves in book.items()}


def _get_book() -> Dict[str, FrozenSet[str]]:
    """Cached opening book with fail-soft empty/error loads.

    Only successful non-empty loads populate the cache (600s TTL). Empty or
    failed loads return {} for that call only and are retried after a short
    backoff, so a rebuilt table is picked up without waiting out the TTL.
    """
    global _book_cache, _book_empty_since
    now = time.time()
    cached = _book_cache
    if cached is not None and now - cached[0] < _BOOK_CACHE_TTL_SECONDS:
        return cached[1]
    if (
        _book_empty_since is not None
        and now - _book_empty_since < _BOOK_EMPTY_RETRY_SECONDS
    ):
        return {}

    with _book_lock:
        cached = _book_cache
        now = time.time()
        if cached is not None and now - cached[0] < _BOOK_CACHE_TTL_SECONDS:
            return cached[1]
        if (
            _book_empty_since is not None
            and now - _book_empty_since < _BOOK_EMPTY_RETRY_SECONDS
        ):
            return {}
        try:
            book = _load_book_from_db()
        except Exception:  # noqa: BLE001 — review must survive a missing table
            log.exception(
                "Opening book load failed (0 rows loaded); "
                "fail-soft empty book for this call"
            )
            _book_empty_since = now
            return {}
        total = sum(len(moves) for moves in book.values())
        if total == 0:
            log.error(
                "Opening book load returned 0 rows; "
                "fail-soft empty book for this call "
                "(run scripts/build_opening_book.py in this environment)"
            )
            _book_empty_since = now
            return {}
        _book_empty_since = None
        _book_cache = (now, book)
        return book


def is_book_move(board: chess.Board, move: chess.Move) -> bool:
    """True when the move is present in the book for this position.

    Presence means book: the builders already apply their own thresholds
    (ECO lines are theory by definition; PGN corpora are filtered by
    `--min-count`), so the hot path is a set lookup. Fails soft to False
    when the book is unavailable.
    """
    moves = _get_book().get(position_key(board))
    return moves is not None and move.uci() in moves


def invalidate_cache() -> None:
    """Drop the cached book and any empty-load backoff (tests / rebuilds)."""
    global _book_cache, _book_empty_since
    _book_cache = None
    _book_empty_since = None


def count_book_rows() -> int:
    """SELECT COUNT(*) FROM opening_book_moves. Raises on DB error."""
    if database.connection_pool is None:
        raise RuntimeError("no database connection pool")
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM opening_book_moves")
            row = cur.fetchone()
    finally:
        database.connection_pool.putconn(conn)
    return int(row[0]) if row else 0


def log_book_status() -> int:
    """Startup check: loud warning when the book table is empty.

    Returns the row count, or -1 when the table cannot be read. Never
    raises; lookups already fail soft to non-book in those cases.
    """
    try:
        total = count_book_rows()
    except Exception:  # noqa: BLE001 — status check must never break boot
        log.exception(
            "Opening book status check failed; "
            "book lookups will fail soft to non-book"
        )
        return -1
    if total == 0:
        log.warning(
            "OPENING BOOK EMPTY (0 rows in opening_book_moves): Game Review "
            "will label opening moves by engine eval instead of Book -- run "
            "scripts/build_opening_book.py in this environment"
        )
    else:
        log.info("Opening book ready: %d rows", total)
    return total


# Vendored CC0 theory lines (see opening_book_data/NOTICE). Read by the
# startup seeder below so fresh databases need no network access.
ECO_TSV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "opening_book_data")
ECO_TSV_FILES = ("a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv")


def seed_eco_book_if_empty(eco_dir: Optional[str] = None) -> int:
    """Insert vendored ECO theory rows, but only when no eco rows exist.

    Offline (reads the vendored TSVs, no network). Idempotent: existing
    eco rows (other sources' rows are never touched) are left alone.
    Race-safe across replicas: concurrent seeders converge via the
    (position_key, move_uci, source) primary key plus ON CONFLICT DO
    NOTHING, so no duplicates are possible. The insert is a single
    statement in a single transaction: any mid-insert failure rolls back
    to zero eco rows. Returns rows inserted (0 when eco already present).
    Never raises: failures log at ERROR and the caller proceeds without
    a book.
    """
    directory = eco_dir or ECO_TSV_DIR
    try:
        if database.connection_pool is None:
            raise RuntimeError("no database connection pool")
        conn = database.connection_pool.getconn()
    except Exception:  # noqa: BLE001 — seeding must never break boot
        log.exception("Opening book seeding failed; continuing without a book")
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM opening_book_moves WHERE source='eco'"
            )
            if int(cur.fetchone()[0]) > 0:
                return 0
        lines: List[str] = []
        for filename in ECO_TSV_FILES:
            with open(os.path.join(directory, filename), encoding="utf-8") as handle:
                lines.extend(
                    pgn for _eco, _name, pgn in iter_eco_rows(handle.read())
                )
        book = collect_moves_from_lines(lines)
        data = [
            (key, uci, san, count, "eco")
            for (key, uci), (count, san) in book.items()
        ]
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO opening_book_moves
                    (position_key, move_uci, move_san, count, source)
                VALUES %s
                ON CONFLICT (position_key, move_uci, source) DO NOTHING
                """,
                data,
            )
        conn.commit()
        log.info("Opening book seeded: %d rows (source=eco)", len(data))
        return len(data)
    except Exception:  # noqa: BLE001 — seeding must never break boot
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — best effort on a broken connection
            pass
        log.exception("Opening book seeding failed; continuing without a book")
        return 0
    finally:
        database.connection_pool.putconn(conn)


# ---------------------------------------------------------------------------
# Build helpers (used by scripts/build_opening_book.py)
# ---------------------------------------------------------------------------

def iter_eco_rows(tsv_text: str) -> Iterator[Tuple[str, str, str]]:
    """Yield (eco, name, pgn) rows from a chess-openings TSV file."""
    for line in tsv_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        yield parts[0].strip(), parts[1].strip(), parts[2].strip()


def _record_move(
    book: Dict[Tuple[str, str], Tuple[int, str]],
    board: chess.Board,
    move: chess.Move,
) -> None:
    key = (position_key(board), move.uci())
    san = board.san(move)
    existing = book.get(key)
    if existing is None:
        book[key] = (1, san)
    else:
        book[key] = (existing[0] + 1, existing[1])


def collect_moves_from_lines(
    pgn_lines: Iterable[str],
) -> Dict[Tuple[str, str], Tuple[int, str]]:
    """Count (position_key, move_uci) -> (occurrences, san) over PGN lines."""
    book: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for pgn_line in pgn_lines:
        game = chess.pgn.read_game(StringIO(pgn_line))
        if game is None:
            continue
        board = game.board()
        for move in game.mainline_moves():
            _record_move(book, board, move)
            board.push(move)
    return book


def _game_meets_elo(game: chess.pgn.Game, min_elo: int) -> bool:
    if min_elo <= 0:
        return True
    for header in ("WhiteElo", "BlackElo"):
        raw = (game.headers.get(header) or "").strip()
        try:
            if int(raw) < min_elo:
                return False
        except ValueError:
            return False
    return True


def collect_moves_from_games(
    games: Iterable[chess.pgn.Game],
    *,
    max_ply: int = 30,
    min_elo: int = 0,
) -> Dict[Tuple[str, str], Tuple[int, str]]:
    """Count book-move candidates over a game stream.

    Only the first `max_ply` plies are walked (the book ends long before
    move 30 in practice; the cap bounds table size on huge corpora), and
    games below `min_elo` on either side are skipped when `min_elo > 0`.
    """
    book: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for game in games:
        if not _game_meets_elo(game, min_elo):
            continue
        board = game.board()
        for ply, move in enumerate(game.mainline_moves()):
            if ply >= max_ply:
                break
            _record_move(book, board, move)
            board.push(move)
    return book


def replace_book_rows(
    conn,
    *,
    source: str,
    rows: Iterable[Tuple[str, str, str, int]],
) -> int:
    """Replace all rows for `source` with `rows` (position, uci, san, count).

    Delete-then-insert makes the build idempotent: re-running a source
    never duplicates rows and always reflects the current input.
    """
    data: List[Tuple[str, str, str, int, str]] = [
        (key, uci, san, count, source) for key, uci, san, count in rows
    ]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM opening_book_moves WHERE source = %s", (source,))
        if data:
            execute_values(
                cur,
                """
                INSERT INTO opening_book_moves
                    (position_key, move_uci, move_san, count, source)
                VALUES %s
                """,
                data,
            )
    conn.commit()
    return len(data)
