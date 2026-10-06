"""
Test harness for services/opening_book.py.

Covers the pure pieces (no database needed):
  * position_key — transposition-stable first-4-FEN-fields key.
  * is_book_move — cache-backed lookup (cache injected directly).
  * iter_eco_rows — chess-openings TSV parsing.
  * collect_moves_from_lines — SAN + count accumulation over theory lines.
  * collect_moves_from_games — ply cap + Elo filter.

Run: cd src && ../venv/bin/python services/opening_book_test.py
"""
import contextlib
import logging
import os
import sys
import threading
import time
import urllib.parse
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import chess.pgn

import services.opening_book as mod


def _key_after(moves):
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return mod.position_key(board)


def test_position_key_transposition():
    a = _key_after(["e4", "e5", "Nf3", "Nc6"])
    b = _key_after(["Nf3", "Nc6", "e4", "e5"])
    assert a == b, "move-order transposition must produce the same key"
    assert len(a.split(" ")) == 4, f"key must be 4 FEN fields, got {a!r}"
    assert a != _key_after(["e4", "e5", "Nf3", "Nc6", "Bb5"]), (
        "a different position must not share the key"
    )
    print("  [PASS] position_key: transpositions match, positions differ")


def test_is_book_move_with_injected_cache():
    board = chess.Board()
    key = mod.position_key(board)
    mod._book_cache = (time.time(), {key: frozenset({"e2e4"})}, "test-rev")
    try:
        assert mod.is_book_move(board, chess.Move.from_uci("e2e4")) is True
        assert mod.is_book_move(board, chess.Move.from_uci("d2d4")) is False
        after = chess.Board()
        after.push_san("e4")
        assert mod.is_book_move(after, chess.Move.from_uci("e7e5")) is False, (
            "a position absent from the book must return False"
        )
    finally:
        mod.invalidate_cache()
    assert mod._book_cache is None, "invalidate_cache must drop the cache"
    print("  [PASS] is_book_move: cache hit/miss + cache invalidation")


def test_iter_eco_rows():
    tsv = (
        "C20\tKing's Pawn Opening\t1. e4 e5\n"
        "\n"
        "C40\tKing's Knight Opening\t1. e4 e5 2. Nf3\n"
    )
    rows = list(mod.iter_eco_rows(tsv))
    assert rows == [
        ("C20", "King's Pawn Opening", "1. e4 e5"),
        ("C40", "King's Knight Opening", "1. e4 e5 2. Nf3"),
    ], rows
    print("  [PASS] iter_eco_rows: parses eco/name/pgn, skips blank lines")


def test_collect_moves_from_lines():
    book = mod.collect_moves_from_lines(
        ["1. e4 e5 2. Nf3", "1. e4 e5 2. Nc3"]
    )
    start_key = mod.position_key(chess.Board())
    assert book[(start_key, "e2e4")] == (2, "e4"), book.get((start_key, "e2e4"))
    after_e4 = chess.Board()
    after_e4.push_san("e4")
    assert book[(mod.position_key(after_e4), "e7e5")] == (2, "e5")
    after_e4e5 = chess.Board()
    after_e4e5.push_san("e4")
    after_e4e5.push_san("e5")
    assert book[(mod.position_key(after_e4e5), "g1f3")] == (1, "Nf3")
    assert book[(mod.position_key(after_e4e5), "b1c3")] == (1, "Nc3")
    print("  [PASS] collect_moves_from_lines: counts accumulate, SAN recorded")


def test_collect_moves_from_games_elo_and_ply():
    high = chess.pgn.read_game(
        StringIO('[WhiteElo "2400"]\n[BlackElo "2300"]\n\n1. e4 e5 2. Nf3 Nc6 *')
    )
    low = chess.pgn.read_game(
        StringIO('[WhiteElo "1200"]\n[BlackElo "1100"]\n\n1. e4 e5 2. Nf3 Nc6 *')
    )
    book = mod.collect_moves_from_games([high, low], min_elo=2000, max_ply=3)
    start_key = mod.position_key(chess.Board())
    assert book[(start_key, "e2e4")][0] == 1, "low-rated game must be skipped"
    after_e4e5 = chess.Board()
    after_e4e5.push_san("e4")
    after_e4e5.push_san("e5")
    assert (mod.position_key(after_e4e5), "g1f3") in book, "3rd ply is within max_ply"
    after_e4e5.push_san("Nf3")
    assert (mod.position_key(after_e4e5), "b8c6") not in book, (
        "max_ply=3 must stop before the 4th ply"
    )
    print("  [PASS] collect_moves_from_games: Elo filter + ply cap respected")


def _reset_loader_state():
    mod.invalidate_cache()
    return mod._load_book_from_db, mod._BOOK_EMPTY_RETRY_SECONDS


def test_empty_load_not_cached_and_reload_picks_up_populated():
    real_loader, real_retry = _reset_loader_state()
    mod._BOOK_EMPTY_RETRY_SECONDS = 0
    try:
        mod._load_book_from_db = lambda: ({}, "")
        assert mod._get_book() == {}, "empty load must fail soft to {}"
        assert mod._book_cache is None, "empty load must not populate the cache"
        populated = {"k": frozenset({"e2e4"})}
        mod._load_book_from_db = lambda: (populated, "rev-1")
        assert mod._get_book() == populated, (
            "a later populated load must be picked up on retry"
        )
        assert mod._book_cache is not None
    finally:
        mod._load_book_from_db = real_loader
        mod._BOOK_EMPTY_RETRY_SECONDS = real_retry
        mod.invalidate_cache()
    print("  [PASS] empty load not cached; populated reload picked up")


def test_db_exception_not_cached():
    real_loader, real_retry = _reset_loader_state()
    mod._BOOK_EMPTY_RETRY_SECONDS = 0
    try:
        def boom():
            raise RuntimeError("db down")
        mod._load_book_from_db = boom
        assert mod._get_book() == {}, "exception must fail soft to {}"
        assert mod._book_cache is None, "exception must not populate the cache"
        populated = {"k": frozenset({"e2e4"})}
        mod._load_book_from_db = lambda: (populated, "rev-1")
        assert mod._get_book() == populated, (
            "load after an exception must be retried, not stuck empty"
        )
    finally:
        mod._load_book_from_db = real_loader
        mod._BOOK_EMPTY_RETRY_SECONDS = real_retry
        mod.invalidate_cache()
    print("  [PASS] DB exception not cached; retry recovers")


def test_populated_load_cached_for_ttl():
    _reset_loader_state()
    real_loader = mod._load_book_from_db
    calls = []
    try:
        def counting_loader():
            calls.append(1)
            return {"k": frozenset({"e2e4"})}, "rev-1"
        mod._load_book_from_db = counting_loader
        first = mod._get_book()
        second = mod._get_book()
        assert first == second == {"k": frozenset({"e2e4"})}
        assert mod.get_book_revision() == "rev-1", "revision must come from content"
        assert len(calls) == 1, f"second call within TTL must not reload, got {len(calls)}"
        mod._book_cache = (
            time.time() - mod._BOOK_CACHE_TTL_SECONDS - 1,
            first,
            "rev-1",
        )
        assert mod._get_book() == first
        assert len(calls) == 2, "expired TTL must reload exactly once"
    finally:
        mod._load_book_from_db = real_loader
        mod.invalidate_cache()
    print("  [PASS] populated load cached for TTL; expiry reloads")


def test_ensure_book_revision_loads_before_returning():
    real_loader, real_retry = _reset_loader_state()
    calls = []
    try:
        def counting_loader():
            calls.append(1)
            return {"k": frozenset({"e2e4"})}, "rev-1"

        mod._load_book_from_db = counting_loader
        assert mod.get_book_revision() is None, "revision starts unloaded"
        assert mod.ensure_book_revision() == "rev-1", (
            "ensure_book_revision must load the book before returning"
        )
        assert mod.get_book_revision() == "rev-1"
        assert len(calls) == 1, f"expected exactly one load, got {len(calls)}"
    finally:
        mod._load_book_from_db = real_loader
        mod._BOOK_EMPTY_RETRY_SECONDS = real_retry
        mod.invalidate_cache()
    print("  [PASS] ensure_book_revision loads the book before returning")


def test_startup_warning_on_empty_table():
    real_counter = mod.count_book_rows
    logger = logging.getLogger("services.opening_book")
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    try:
        mod.count_book_rows = lambda: 0
        assert mod.log_book_status() == 0
        warns = [r for r in records if r.levelno >= logging.WARNING]
        assert warns and "EMPTY" in warns[0].getMessage(), (
            "empty table must log a loud warning"
        )
        records.clear()
        mod.count_book_rows = lambda: 8067
        assert mod.log_book_status() == 8067
        assert not [r for r in records if r.levelno >= logging.WARNING], (
            "populated table must not warn"
        )
        records.clear()

        def boom():
            raise RuntimeError("db down")
        mod.count_book_rows = boom
        assert mod.log_book_status() == -1
    finally:
        mod.count_book_rows = real_counter
        logger.removeHandler(handler)
    print("  [PASS] startup warning fires on empty table; -1 on DB error")


_DB_READY = None


def _is_test_dbname(dbname):
    """True for the `test`, `test_*`, `*_test` database names."""
    return (
        dbname == "test"
        or dbname.startswith("test_")
        or dbname.endswith("_test")
    )


def _effective_dbname(url):
    """Database name libpq would actually use: the URL path, falling back
    to PGDATABASE when the URL names none (explicit URL parts win over
    the environment, mirroring libpq). Host is intentionally ignored: a
    localhost tunnel to prod must NOT pass on host alone."""
    try:
        path_db = (urllib.parse.urlparse(url).path or "").lstrip("/").split("?")[0]
    except Exception:
        path_db = ""
    return path_db or os.getenv("PGDATABASE") or ""


def _db_allowlisted(url):
    """(allowed, reason). Destructive tests wipe opening_book_moves, so they
    run only against a test-named database with explicit opt-in. Host plays
    no part in the decision."""
    dbname = _effective_dbname(url)
    opt_in = os.getenv("PRAXIS_ALLOW_DESTRUCTIVE_DB_TESTS") == "1"
    if _is_test_dbname(dbname) and opt_in:
        return True, (
            f"test database {dbname!r} with PRAXIS_ALLOW_DESTRUCTIVE_DB_TESTS=1"
        )
    reasons = []
    if not _is_test_dbname(dbname):
        reasons.append(
            f"dbname={dbname!r} is not test/test_*/ *_test "
            "(URL path, else PGDATABASE fallback)"
        )
    if not opt_in:
        reasons.append("PRAXIS_ALLOW_DESTRUCTIVE_DB_TESTS != 1")
    return False, (
        "; ".join(reasons)
        + " -- refusing to wipe opening_book_moves"
    )


def _require_db():
    """Init the real database pool once; refuse unless test-named + opt-in.

    The allowlist is checked before opening any connection, so a refused
    run never touches the database.
    """
    global _DB_READY
    if _DB_READY is not None:
        return _DB_READY
    url = os.getenv("DATABASE_URL")
    if not url:
        print("  [SKIP] no DATABASE_URL; skipping live-DB book seeding tests")
        _DB_READY = False
        return False
    allowed, reason = _db_allowlisted(url)
    if not allowed:
        print(f"  [REFUSE] {reason}")
        _DB_READY = False
        return False
    from core import database
    database.init_db()
    _DB_READY = True
    return True


def _expected_eco_rows():
    """Row count the vendored TSVs should produce (no DB needed)."""
    lines = []
    for filename in mod.ECO_TSV_FILES:
        with open(os.path.join(mod.ECO_TSV_DIR, filename), encoding="utf-8") as handle:
            lines.extend(pgn for _eco, _name, pgn in mod.iter_eco_rows(handle.read()))
    return len(mod.collect_moves_from_lines(lines))


@contextlib.contextmanager
def _emptied_book_table():
    """Snapshot the full book table, wipe it, yield; restore on exit."""
    from core import database
    from psycopg2.extras import execute_values
    conn = database.connection_pool.getconn()
    snapshot = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT position_key, move_uci, move_san, count, source "
                "FROM opening_book_moves;"
            )
            snapshot = cur.fetchall()
            cur.execute("DELETE FROM opening_book_moves;")
        conn.commit()
        yield snapshot
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM opening_book_moves;")
                if snapshot:
                    execute_values(
                        cur,
                        "INSERT INTO opening_book_moves "
                        "(position_key, move_uci, move_san, count, source) "
                        "VALUES %s "
                        "ON CONFLICT (position_key, move_uci, source) DO NOTHING",
                        snapshot,
                    )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM opening_book_moves;")
                restored = cur.fetchone()[0]
            assert restored == len(snapshot), (
                f"restore failed: {restored} rows vs {len(snapshot)} snapshotted"
            )
        finally:
            database.connection_pool.putconn(conn)


@contextlib.contextmanager
def _captured_logs():
    logger = logging.getLogger("services.opening_book")
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _table_count():
    from core import database
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM opening_book_moves;")
            return cur.fetchone()[0]
    finally:
        database.connection_pool.putconn(conn)


def test_seed_if_empty_seeds_and_noop_when_populated():
    if not _require_db():
        return
    expected = _expected_eco_rows()
    with _emptied_book_table():
        assert _table_count() == 0
        inserted = mod.seed_eco_book_if_empty()
        assert inserted == expected, f"seeded {inserted}, expected {expected}"
        assert _table_count() == expected
        assert mod.seed_eco_book_if_empty() == 0, "second run must be a no-op"
        assert _table_count() == expected, "second run must not change rows"
    print("  [PASS] seed-if-empty seeds once, no-op when populated")


def test_concurrent_seeders_no_duplicates():
    if not _require_db():
        return
    expected = _expected_eco_rows()
    with _emptied_book_table():
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def work():
            try:
                barrier.wait(timeout=60)
                results.append(mod.seed_eco_book_if_empty())
            except Exception as exc:  # noqa: BLE001 -- record, don't fail the thread
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(180)
        assert not errors, f"seeder threads raised: {errors}"
        assert len(results) == 2, "both racing seeders must return without error"
        from core import database
        conn = database.connection_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM opening_book_moves WHERE source='eco';"
                )
                total = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM (SELECT position_key, move_uci, source "
                    "FROM opening_book_moves GROUP BY 1, 2, 3 HAVING COUNT(*) > 1) d;"
                )
                dupes = cur.fetchone()[0]
        finally:
            database.connection_pool.putconn(conn)
        assert total == expected, f"concurrent seed gave {total}, expected {expected}"
        assert dupes == 0, f"concurrent seed produced {dupes} duplicate key groups"
    print("  [PASS] two racing seeders both return; eco count exact, no duplicates")


def test_seed_with_non_eco_row_present_still_seeds_eco():
    if not _require_db():
        return
    from core import database
    expected = _expected_eco_rows()
    with _emptied_book_table():
        conn = database.connection_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO opening_book_moves "
                    "(position_key, move_uci, move_san, count, source) "
                    "VALUES ('__probe__', '__probe__', 'x', 1, 'pgn')"
                )
            conn.commit()
        finally:
            database.connection_pool.putconn(conn)
        inserted = mod.seed_eco_book_if_empty()
        assert inserted == expected, f"seeded {inserted}, expected {expected}"
        conn = database.connection_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM opening_book_moves WHERE source='eco';"
                )
                assert cur.fetchone()[0] == expected
                cur.execute(
                    "SELECT COUNT(*) FROM opening_book_moves "
                    "WHERE position_key='__probe__';"
                )
                assert cur.fetchone()[0] == 1, "non-eco rows must be untouched"
                cur.execute("DELETE FROM opening_book_moves WHERE position_key='__probe__';")
            conn.commit()
        finally:
            database.connection_pool.putconn(conn)
    print("  [PASS] non-eco row present: eco still seeds, other rows untouched")


def test_seed_failure_mid_insert_leaves_zero_eco_rows():
    if not _require_db():
        return
    real_collect = mod.collect_moves_from_lines

    def poisoned(lines):
        book = real_collect(lines)
        book[("__probe_key__", "__probe_uci__")] = (None, "x")
        return book

    with _emptied_book_table():
        mod.collect_moves_from_lines = poisoned
        try:
            with _captured_logs() as records:
                assert mod.seed_eco_book_if_empty() == 0
            errors = [r for r in records if r.levelno >= logging.ERROR]
            assert errors, "mid-insert failure must log at ERROR"
        finally:
            mod.collect_moves_from_lines = real_collect
        from core import database
        conn = database.connection_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM opening_book_moves WHERE source='eco';"
                )
                assert cur.fetchone()[0] == 0, (
                    "aborted insert must leave zero eco rows"
                )
        finally:
            database.connection_pool.putconn(conn)
    print("  [PASS] mid-insert failure rolls back to zero eco rows")


def test_seed_failure_logs_error_without_crash():
    from core import database
    real_pool = database.connection_pool
    database.connection_pool = None
    try:
        with _captured_logs() as records:
            assert mod.seed_eco_book_if_empty() == 0
        errors = [r for r in records if r.levelno >= logging.ERROR]
        assert errors, "seed failure must log at ERROR"
    finally:
        database.connection_pool = real_pool
    if not _require_db():
        return
    with _emptied_book_table():
        with _captured_logs() as records:
            assert mod.seed_eco_book_if_empty(eco_dir="/nonexistent-xyz") == 0
        errors = [r for r in records if r.levelno >= logging.ERROR]
        assert errors, "missing TSV dir must log at ERROR"
        assert _table_count() == 0, "failed seed must not write partial rows"
    print("  [PASS] seed failure logs at ERROR and never raises")


def main() -> int:
    print("=== Running opening_book tests ===")
    tests = [
        test_position_key_transposition,
        test_is_book_move_with_injected_cache,
        test_iter_eco_rows,
        test_collect_moves_from_lines,
        test_collect_moves_from_games_elo_and_ply,
        test_empty_load_not_cached_and_reload_picks_up_populated,
        test_db_exception_not_cached,
        test_populated_load_cached_for_ttl,
        test_ensure_book_revision_loads_before_returning,
        test_startup_warning_on_empty_table,
        test_seed_if_empty_seeds_and_noop_when_populated,
        test_concurrent_seeders_no_duplicates,
        test_seed_with_non_eco_row_present_still_seeds_eco,
        test_seed_failure_mid_insert_leaves_zero_eco_rows,
        test_seed_failure_logs_error_without_crash,
    ]
    for test in tests:
        try:
            test()
        except (AssertionError, ValueError) as exc:
            print(f"\n  [FAIL] {test.__name__}: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
            return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
