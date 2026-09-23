"""
Verification harness for services/endgame_recommendation.py.

Sequences:

  A. Pure scoring (no DB): the weighted score's terms and bounds, the
     review-rate volume gate (one failed review must not outrank a failure
     backlog), the minimum-evidence gate, deterministic ranking with its
     tie-breaks, the cold-start fallback (unrated -> fundamentals; rated ->
     closest average difficulty), and the reason strings.

  B. DB integration against the real tables with a throwaway user:
       * no history -> fallback recommendation carrying a sample FEN;
       * failed entries + review attempts in one category -> that category,
         with the sample FEN taken from the user's most recent failed
         position;
       * rated + no history -> the difficulty-matched fallback;
       * an unknown user id degrades to the unrated fallback (the card must
         not 404 a user row that a webhook has not created yet).

Run with: cd src && PYTHONPATH=. ../venv/bin/python services/endgame_recommendation_test.py
Requires: DB_* or DATABASE_URL from the root .env.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

from services.endgame_recommendation import (
    CategoryWeakness,
    load_category_weakness,
    pick_category,
    rate_terms_apply,
    reason_for,
    recommend_category,
    weakness_score,
)

load_dotenv()

TEST_CLERK_ID = "endgame-recommendation-test"
TEST_EMAIL = "endgame-recommendation-test@example.invalid"


def _db_config():
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


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def test_score_terms_and_bounds():
    maxed = CategoryWeakness(
        category="x",
        positions=100,
        active_failures=5,
        lapses=5,
        recent_failures=3,
        attempts=10,
        failed_attempts=10,
        hinted_attempts=10,
    )
    assert _close(weakness_score(maxed), 1.0), weakness_score(maxed)

    empty = CategoryWeakness(category="x", positions=100)
    assert weakness_score(empty) == 0.0

    # Half the failure threshold + all of the rate terms.
    partial = CategoryWeakness(
        category="x",
        positions=100,
        active_failures=5,
        attempts=5,
        failed_attempts=5,
    )
    # 0.40 (failures) + 0.20 (100% review fail rate) = 0.60
    assert _close(weakness_score(partial), 0.60), weakness_score(partial)
    print("  score is bounded to [0, 1] and each term lands where documented")


def test_rate_volume_gate():
    # One failed review (1/1 = 100%) must not count as a rate.
    noisy = CategoryWeakness(
        category="noisy",
        positions=100,
        active_failures=1,
        attempts=1,
        failed_attempts=1,
    )
    assert not rate_terms_apply(noisy)
    # 1/5 of the failure term only.
    assert _close(weakness_score(noisy), 0.08), weakness_score(noisy)

    # A real failure backlog beats the single noisy review.
    backlog = CategoryWeakness(category="backlog", positions=100, active_failures=4)
    assert weakness_score(backlog) > weakness_score(noisy)

    # With enough attempts the rate counts.
    rated = CategoryWeakness(
        category="rated",
        positions=100,
        active_failures=1,
        attempts=5,
        failed_attempts=5,
    )
    assert rate_terms_apply(rated)
    assert _close(weakness_score(rated), 0.08 + 0.20), weakness_score(rated)

    # Hint rate rides the same gate.
    hinted = CategoryWeakness(
        category="hinted",
        positions=100,
        attempts=5,
        hinted_attempts=5,
    )
    assert _close(weakness_score(hinted), 0.10), weakness_score(hinted)
    print("  review/hint rates are gated on enough attempts; backlogs still win")


def test_evidence_gate_and_ranking():
    rows = [
        # One failure, no attempts: below the gate, cannot win.
        CategoryWeakness(category="bishop", positions=100, active_failures=1),
        CategoryWeakness(category="rook", positions=100, active_failures=3),
    ]
    winner, is_fallback = pick_category(rows, None)
    assert (winner.category, is_fallback) == ("rook", False), winner

    # Attempts alone can qualify a category (rate terms then count).
    rows = [
        CategoryWeakness(category="bishop", positions=100, active_failures=1),
        CategoryWeakness(
            category="queen",
            positions=100,
            attempts=6,
            failed_attempts=5,
        ),
    ]
    winner, is_fallback = pick_category(rows, None)
    assert (winner.category, is_fallback) == ("queen", False), winner

    # Equal scores: more active failures wins, then lapses, then name.
    rows = [
        CategoryWeakness(category="knight", positions=100, active_failures=3),
        CategoryWeakness(category="rook", positions=100, active_failures=4),
    ]
    winner, _ = pick_category(rows, None)
    assert winner.category == "rook", winner

    try:
        pick_category([], None)
        raise AssertionError("empty pool must raise")
    except ValueError:
        pass
    print("  evidence gate + deterministic ranking behave as documented")


def test_fallback_selection():
    rows = [
        CategoryWeakness(category="bishop", positions=100, avg_difficulty=1800.0),
        CategoryWeakness(category="pure_pawn", positions=100, avg_difficulty=900.0),
        CategoryWeakness(category="rook", positions=100, avg_difficulty=1200.0),
    ]
    winner, is_fallback = pick_category(rows, None)
    assert is_fallback and winner.category == "pure_pawn", winner

    winner, is_fallback = pick_category(rows, 1850)
    assert is_fallback and winner.category == "bishop", winner

    winner, is_fallback = pick_category(rows, 1250)
    assert is_fallback and winner.category == "rook", winner
    print("  cold start: unrated -> pure_pawn, rated -> closest difficulty")


def test_reasons():
    backlog = CategoryWeakness(
        category="rook", positions=100, active_failures=6, lapses=2
    )
    assert (
        reason_for(backlog, is_fallback=False, rating=None)
        == "6 failed positions not yet mastered · 2 repeat failures"
    ), reason_for(backlog, is_fallback=False, rating=None)

    single = CategoryWeakness(category="rook", positions=100, active_failures=1)
    assert (
        reason_for(single, is_fallback=False, rating=None)
        == "1 failed position not yet mastered"
    )

    rate_only = CategoryWeakness(
        category="queen", positions=100, attempts=6, failed_attempts=3
    )
    assert (
        reason_for(rate_only, is_fallback=False, rating=None)
        == "50% of recent reviews failed"
    )

    assert (
        reason_for(backlog, is_fallback=True, rating=None)
        == "The best place to start your endgame training"
    )
    assert (
        reason_for(backlog, is_fallback=True, rating=1200)
        == "A good next step for your endgame rating"
    )
    print("  reasons name the signals that actually drove the pick")


def _insert_failed_positions(cur, category: str, ages_days):
    """Insert one active failed entry per age (days ago) in `category` and
    return [(position_id, fen, added_at)] in insertion order."""
    cur.execute(
        """
        SELECT p.id, p.fen
        FROM endgame_positions p
        JOIN endgame_topics t ON t.id = p.topic_id
        WHERE t.category = %s
          AND p.source_puzzle_id IS NOT NULL
        ORDER BY p.id
        LIMIT %s
        """,
        (category, len(ages_days)),
    )
    positions = cur.fetchall()
    assert len(positions) == len(ages_days), (category, positions)

    inserted = []
    for (position_id, fen), age in zip(positions, ages_days):
        cur.execute(
            """
            INSERT INTO endgame_woodpecker_entries (
                user_id, position_id, theme, added_at
            )
            VALUES (%s, %s, %s, NOW() - make_interval(days => %s))
            RETURNING id
            """,
            (TEST_CLERK_ID, position_id, category, age),
        )
        inserted.append((cur.fetchone()[0], position_id, fen))
    return inserted


def test_db_recommendation(conn):
    # 1. Cold start: no history at all -> fallback with a sample FEN.
    rec = recommend_category(conn, TEST_CLERK_ID)
    assert rec is not None and rec.is_fallback, rec
    assert rec.sample_fen, rec
    print(f"  cold start -> {rec.category} ({rec.reason})")

    # 2. Failures + attempts in knight -> knight, sample from the newest
    #    failed position (ages chosen so the ordering is unambiguous).
    with conn.cursor() as cur:
        entries = _insert_failed_positions(
            cur, "knight", ages_days=[10, 1, 3]
        )
        newest_entry_id, newest_position_id, newest_fen = entries[1]
        for solved, hints in ((False, 0), (False, 0), (True, 1)):
            cur.execute(
                """
                INSERT INTO endgame_woodpecker_attempts (
                    entry_id, user_id, solved_correctly, time_taken_ms,
                    hints_used, attempted_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW() - interval '5 days')
                """,
                (newest_entry_id, TEST_CLERK_ID, solved, 5000, hints),
            )
    conn.commit()

    rec = recommend_category(conn, TEST_CLERK_ID)
    assert rec is not None and not rec.is_fallback, rec
    assert rec.category == "knight", rec
    assert rec.reason == "3 failed positions not yet mastered", rec.reason
    assert rec.sample_fen == newest_fen, (rec.sample_fen, newest_fen)
    print(f"  failure backlog -> {rec.category} ({rec.reason}), sample from newest failure")

    # 3. Rated + no history -> the difficulty-matched fallback.
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM endgame_woodpecker_entries WHERE user_id = %s",
            (TEST_CLERK_ID,),
        )
        cur.execute(
            "UPDATE users SET endgame_trainer_rating = 2400 WHERE clerk_id = %s",
            (TEST_CLERK_ID,),
        )
    conn.commit()

    rec = recommend_category(conn, TEST_CLERK_ID)
    assert rec is not None and rec.is_fallback, rec
    rows = load_category_weakness(conn, TEST_CLERK_ID)
    expected = min(
        rows,
        key=lambda row: (abs((row.avg_difficulty or 0.0) - 2400), row.category),
    )
    assert rec.category == expected.category, (rec.category, expected.category)
    print(f"  rated cold start (2400) -> {rec.category} (closest difficulty)")

    # 4. Unknown user id: the card must not 404 a user row the Clerk
    #    webhook has not created yet -- degrade to the unrated fallback.
    rec = recommend_category(conn, "no-such-user-" + TEST_CLERK_ID)
    assert rec is not None and rec.is_fallback, rec
    assert rec.category == "pure_pawn", rec
    print("  unknown user id degrades to the unrated starter")


def main():
    conn = psycopg2.connect(**_db_config())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM users WHERE clerk_id = %s OR email = %s",
                (TEST_CLERK_ID, TEST_EMAIL),
            )
            cur.execute(
                "INSERT INTO users (clerk_id, email) VALUES (%s, %s)",
                (TEST_CLERK_ID, TEST_EMAIL),
            )
        conn.commit()

        print("A. pure scoring:")
        test_score_terms_and_bounds()
        test_rate_volume_gate()
        test_evidence_gate_and_ranking()
        test_fallback_selection()
        test_reasons()
        print("B. DB integration:")
        test_db_recommendation(conn)
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM users WHERE clerk_id = %s OR email = %s",
                (TEST_CLERK_ID, TEST_EMAIL),
            )
        conn.commit()
        conn.close()

    print("all endgame recommendation checks passed (test user removed)")


if __name__ == "__main__":
    main()
