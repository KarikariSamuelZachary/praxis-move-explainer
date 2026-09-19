"""
Verification harness for the Endgame Trainer library selection function
(src/services/endgame_library.py).

Covers, against the REAL seeded data (endgame_topics / endgame_positions):

  A. category="rook" draws only rook positions -- the curated Lucena Position
     topic plus the sourced material topics imported by
     scripts/import_sourced_endgames.py (all category="rook"). Draws are
     pooled-random, so repeated calls must produce more than one distinct
     position.

  B. topic_id=<Lucena id> draws only from that topic, again with variety
     across repeated calls.

  C. Empty scopes are a result, not an error: category="bishop_knight" (no
     seeded content) and a valid-but-empty topic id return status="empty"
     with a reason, and never raise.

  D. Validation: both / neither selectors, a non-CHECK category, and a
     malformed topic_id all raise ValueError before any query runs.

Run with: cd src && ../venv/bin/python services/endgame_library_test.py
"""
import os
import sys
import uuid
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

from services.endgame_library import (
    CATEGORIES,
    EndgameSelection,
    list_categories,
    select_drill_position,
)

load_dotenv()

DRAWS = 40


def _db_config():
    # Connection config mirrors seed_puzzles.py: discrete DB_* vars first
    # (what src/.env provides when running from src/), DATABASE_URL as the
    # fallback (what the root .env provides when running from repo root).
    config = {
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 5432)),
    }
    if not all([config["dbname"], config["user"], config["password"]]):
        database_url = os.getenv("DATABASE_URL")
        if database_url and "://" in database_url:
            return {"dsn": database_url}
        raise SystemExit(
            "no usable DB config: set DB_NAME/DB_USER/DB_PASSWORD (src/.env) "
            "or a real DATABASE_URL"
        )
    return config


def _lucena_topic_id(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM endgame_topics WHERE name = %s", ("Lucena Position",))
        row = cur.fetchone()
    assert row is not None, "Lucena Position topic missing - run src/seed_endgames.py"
    return str(row[0])


def test_rook_category(conn):
    draws = [select_drill_position(conn, category="rook") for _ in range(DRAWS)]

    assert all(d.status == "ok" for d in draws), "rook category must be seeded"
    positions = [d.position for d in draws]
    assert all(p.topic_category == "rook" for p in positions)

    distinct = {p.position_id for p in positions}
    assert len(distinct) > 1, (
        f"expected pooled-random variety across {DRAWS} draws, got "
        f"{len(distinct)} distinct position(s)"
    )
    print(f"  {DRAWS} draws: {len(distinct)} distinct positions, "
          f"{len({p.fen for p in positions})} distinct FENs, "
          f"topics={sorted({p.topic_name for p in positions})}")
    sample = draws[0].position
    print(f"  sample: {sample.position_id} | {sample.fen} | "
          f"is_winning={sample.is_winning} | diff={sample.topic_difficulty_rating}")
    return draws


def test_lucena_topic(conn, lucena_id):
    draws = [select_drill_position(conn, topic_id=lucena_id) for _ in range(DRAWS)]

    assert all(d.status == "ok" for d in draws)
    positions = [d.position for d in draws]
    assert all(str(p.topic_id) == lucena_id for p in positions)
    assert all(p.topic_name == "Lucena Position" for p in positions)

    distinct = {p.position_id for p in positions}
    assert len(distinct) > 1, (
        f"expected pooled-random variety across {DRAWS} draws, got "
        f"{len(distinct)} distinct position(s)"
    )
    print(f"  {DRAWS} draws, all topic_id={lucena_id[:8]}... : "
          f"{len(distinct)} distinct positions")


def test_empty_scopes(conn):
    result = select_drill_position(conn, category="bishop_knight")
    assert isinstance(result, EndgameSelection)
    assert result.status == "empty", result
    assert result.position is None
    assert "bishop_knight" in (result.reason or "")
    print(f"  category='bishop_knight' -> status={result.status}, reason={result.reason!r}")

    empty_topic = str(uuid.uuid4())
    result = select_drill_position(conn, topic_id=empty_topic)
    assert result.status == "empty"
    assert result.position is None
    assert empty_topic in (result.reason or "")
    print(f"  random topic_id  -> status={result.status}, reason={result.reason!r}")


def test_validation(conn):
    cases = [
        (dict(category="rook", topic_id=str(uuid.uuid4())), "exactly one"),
        (dict(), "exactly one"),
        (dict(category="pawn"), "unknown category"),  # not a CHECK value
        (dict(topic_id="not-a-uuid"), "not a valid UUID"),
    ]
    for kwargs, fragment in cases:
        try:
            select_drill_position(conn, **kwargs)
            raise AssertionError(f"{kwargs} must raise ValueError")
        except ValueError as exc:
            assert fragment in str(exc), (kwargs, str(exc))
    print(f"  {len(cases)} invalid requests -> ValueError (valid categories: "
          f"{len(CATEGORIES)})")


def test_sourced_pool(conn):
    draws = [
        select_drill_position(conn, category="rook", sourced_only=True)
        for _ in range(DRAWS)
    ]
    assert all(d.status == "ok" for d in draws), "sourced rook pool must be seeded"
    positions = [d.position for d in draws]
    assert all(p.topic_category == "rook" for p in positions)
    assert all(p.source_puzzle_id for p in positions), (
        "curated row leaked into a sourced_only draw"
    )
    assert all(p.solution_moves for p in positions), (
        "sourced row is missing its stored solution line"
    )

    # Deterministic proof the filter excludes curated rows: the Lucena topic
    # has 10 rows and every one is curated (source_puzzle_id IS NULL).
    lucena_id = _lucena_topic_id(conn)
    filtered = select_drill_position(conn, topic_id=lucena_id, sourced_only=True)
    assert filtered.status == "empty", filtered
    assert "sourced pool only" in (filtered.reason or ""), filtered
    unfiltered = select_drill_position(conn, topic_id=lucena_id)
    assert unfiltered.status == "ok", unfiltered
    print(
        f"  {DRAWS} sourced_only rook draws: all sourced, all carry "
        f"solution_moves; Lucena topic -> empty with flag, ok without it"
    )


def test_list_categories(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.category, COUNT(*)
            FROM endgame_topics t
            JOIN endgame_positions p ON p.topic_id = t.id
            WHERE p.source_puzzle_id IS NOT NULL
            GROUP BY 1 ORDER BY 1
            """
        )
        expected_sourced = cur.fetchall()
        cur.execute(
            """
            SELECT t.category, COUNT(*)
            FROM endgame_topics t
            JOIN endgame_positions p ON p.topic_id = t.id
            GROUP BY 1 ORDER BY 1
            """
        )
        expected_all = cur.fetchall()

    got_sourced = [
        (c.category, c.position_count) for c in list_categories(conn, sourced_only=True)
    ]
    got_all = [(c.category, c.position_count) for c in list_categories(conn)]
    assert got_sourced == expected_sourced, (got_sourced, expected_sourced)
    assert got_all == expected_all, (got_all, expected_all)

    # The curated-vs-sourced difference must land exactly on rook (the
    # Lucena topic): it is the only category with curated rows.
    sourced_map = dict(got_sourced)
    all_map = dict(got_all)
    assert set(all_map) == set(sourced_map) | {"rook"}, (all_map, sourced_map)
    assert all_map["rook"] - sourced_map["rook"] == 10, (all_map, sourced_map)
    assert sum(sourced_map.values()) + 10 == sum(all_map.values())
    assert "bishop_knight" not in sourced_map, (
        "category with no positions must not be listed"
    )
    print(
        f"  sourced list: {len(got_sourced)} categories, "
        f"{sum(sourced_map.values())} positions (rook={sourced_map['rook']}); "
        f"all list adds only Lucena's 10 to rook"
    )


def main():
    conn = psycopg2.connect(**_db_config())
    try:
        print("A. category='rook' pooled-random draws:")
        test_rook_category(conn)
        print("B. topic_id=<Lucena> pooled-random draws:")
        test_lucena_topic(conn, _lucena_topic_id(conn))
        print("C. empty scopes return a clear result:")
        test_empty_scopes(conn)
        print("D. validation:")
        test_validation(conn)
        print("E. sourced-only pool filter + payload:")
        test_sourced_pool(conn)
        print("F. list_categories matches the DB:")
        test_list_categories(conn)
    finally:
        conn.close()
    print("all library-selection checks passed")


if __name__ == "__main__":
    main()
