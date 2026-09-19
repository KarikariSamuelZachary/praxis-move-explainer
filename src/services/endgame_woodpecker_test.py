"""
Standalone checks for services/endgame_woodpecker.queue_failed_drill --
the FAILED-drill -> endgame Woodpecker card capture.

Verifies against the real seeded endgame_positions table:
  A. Input validation: non-UUID position_id and empty theme raise
     ValueError BEFORE touching the DB.
  B. Insert: a fresh capture creates one active card with the puzzle
     queue's fresh-miss FSRS defaults (state=1, step/stability/difficulty
     NULL, reps=0, lapses=0, due ~= now, source_reason preserved, theme).
  C. Duplicate guard: capturing the same still-active position again
     returns the SAME card (created=False), no second row.
  D. Mastered card: once the card is mastered, the next capture opens a
     FRESH card (created=True, new id) -- mirroring the puzzle queue's
     is_mastered = FALSE duplicate guard.

Run with: cd src && ../venv/bin/python services/endgame_woodpecker_test.py
Requires DATABASE_URL (root .env) and the seeded endgame tables.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: F401 - loads root .env then src/.env
from core import database
from core.migrations import run_migrations
from psycopg2.extras import RealDictCursor
from services.endgame_woodpecker import queue_failed_drill

CLERK_ID = "endgame-wp-capture-test"
EMAIL = "endgame-wp-capture-test@example.invalid"


def _cleanup(cur):
    cur.execute("DELETE FROM endgame_woodpecker_entries WHERE user_id = %s", (CLERK_ID,))
    cur.execute(
        "DELETE FROM users WHERE clerk_id = %s OR email = %s", (CLERK_ID, EMAIL)
    )


def _fetch_entry(conn, entry_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM endgame_woodpecker_entries WHERE id = %s::uuid",
            (entry_id,),
        )
        return cur.fetchone()


def test_validation():
    print("A. validation:")
    for kwargs, why in (
        (dict(position_id="not-a-uuid", theme="rook"), "non-UUID position_id"),
        (dict(position_id="00000000-0000-0000-0000-000000000001", theme=""), "empty theme"),
    ):
        try:
            queue_failed_drill(None, clerk_id=CLERK_ID, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{why} did not raise ValueError")
    print("  non-UUID position_id / empty theme -> ValueError (no DB touched)")


def test_capture(conn):
    print("B. fresh capture:")
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id FROM endgame_positions WHERE source_puzzle_id IS NOT NULL LIMIT 1"
        )
        position_id = str(cur.fetchone()["id"])

    result = queue_failed_drill(
        conn, clerk_id=CLERK_ID, position_id=position_id, theme="rook"
    )
    conn.commit()
    assert result.created is True, result
    entry = _fetch_entry(conn, result.entry_id)
    assert entry["state"] == 1 and entry["step"] is None, entry
    assert entry["stability"] is None and entry["difficulty"] is None, entry
    assert entry["reps"] == 0 and entry["lapses"] == 0, entry
    assert entry["is_mastered"] is False and entry["mastered_at"] is None, entry
    assert entry["last_review"] is None, entry
    assert entry["source_reason"] == "wrong_answer", entry
    assert entry["theme"] == "rook", entry
    assert entry["due"] is not None, entry
    print(
        f"  created card {result.entry_id[:8]}... state=1 step=NULL reps=0 "
        f"lapses=0 source_reason=wrong_answer due={entry['due'].isoformat()}"
    )

    print("C. duplicate guard:")
    again = queue_failed_drill(
        conn, clerk_id=CLERK_ID, position_id=position_id, theme="rook"
    )
    conn.commit()
    assert again.created is False, again
    assert again.entry_id == result.entry_id, again
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM endgame_woodpecker_entries WHERE user_id = %s",
            (CLERK_ID,),
        )
        assert cur.fetchone()[0] == 1, "duplicate capture created a second card"
    print(f"  repeat capture -> same card {again.entry_id[:8]}... (no second row)")

    print("D. mastered card re-opens a fresh one:")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE endgame_woodpecker_entries
            SET is_mastered = TRUE, mastered_at = NOW()
            WHERE id = %s::uuid
            """,
            (result.entry_id,),
        )
    conn.commit()
    fresh = queue_failed_drill(
        conn, clerk_id=CLERK_ID, position_id=position_id, theme="rook"
    )
    conn.commit()
    assert fresh.created is True and fresh.entry_id != result.entry_id, fresh
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM endgame_woodpecker_entries WHERE user_id = %s",
            (CLERK_ID,),
        )
        assert cur.fetchone()[0] == 2, "mastered re-open did not insert a new card"
    print(f"  mastered -> fresh card {fresh.entry_id[:8]}... (two rows total)")


def main():
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set (root .env)")
    database.init_db()
    run_migrations()
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            _cleanup(cur)
            cur.execute(
                """
                INSERT INTO users (clerk_id, email, skill_level)
                VALUES (%s, %s, 'intermediate')
                """,
                (CLERK_ID, EMAIL),
            )
        conn.commit()

        test_validation()
        test_capture(conn)
        print("all endgame woodpecker capture checks passed")
    finally:
        with conn.cursor() as cur:
            _cleanup(cur)
        conn.commit()
        database.connection_pool.putconn(conn)


if __name__ == "__main__":
    main()
