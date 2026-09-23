"""
HTTP end-to-end verification harness for the repertoire training SCORE
(src/routers/repertoire.py: POST /sessions/start, POST /sessions/{id}/complete
and GET /api/repertoires' last_score_percent).

Everything is driven through the ASGI app with real HTTP semantics
(TestClient -> middleware -> routing -> handlers). The app's heavyweight
boot hooks are bypassed exactly like routers/endgames_test.py does:
init_db() and run_migrations() are invoked explicitly (the endpoints under
test need only the DB pool).

What is verified:

  A. positions_total counts QUIZ positions (owner-side rows), not every
     persisted row. Train mode returns opponent plies too (the client
     auto-plays their stored replies), but the session total must match
     what the client quizzes -- otherwise the score denominator is
     inflated (a 5-line white repertoire used to store 10 and read 50%
     for a perfect session).

  B. last_score_percent is ACCURACY from the latest completed session:
     positions_correct / attempts_total, the same number the Train page
     shows. 2 solved with 3 attempts -> 66.7%.

  C. attempts_total validation: smaller than positions_correct -> 400
     before any write (the DB CHECK stays as the backstop).

  D. Fallback: a session completed WITHOUT attempts_total (an older
     client) scores positions_correct / positions_total.

  E. Backfill: the migration reconstructs attempts_total for completed
     sessions that predate the column (a completed session solved every
     quiz position, so positions_correct is the best lower bound).

Run with: cd src && ../venv/bin/python routers/repertoire_test.py
Requires: DATABASE_URL + INTERNAL_SECRET from the root .env.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

# Importing main runs the app's load_dotenv calls (root .env then
# src/.env), builds the FastAPI app, and mounts the routers.
import main as app_module
from core import database
from core.migrations import run_migrations

TEST_CLERK_ID = "repertoire-score-http-test"
TEST_EMAIL = "repertoire-score-http-test@example.invalid"


def setup():
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set (root .env)")
    if not os.environ.get("INTERNAL_SECRET"):
        raise SystemExit("INTERNAL_SECRET is not set (root .env)")

    database.init_db()
    run_migrations()

    conn = database.connection_pool.getconn()
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
    finally:
        database.connection_pool.putconn(conn)

    headers = {
        "X-Clerk-User-Id": TEST_CLERK_ID,
        "X-Internal-Secret": os.environ["INTERNAL_SECRET"],
    }
    return TestClient(app_module.app), headers


def teardown():
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            # Repertoires cascade to positions + sessions; remove them
            # before the user row (the FK has no cascade to users).
            cur.execute("DELETE FROM repertoires WHERE user_id = %s", (TEST_CLERK_ID,))
            cur.execute(
                "DELETE FROM users WHERE clerk_id = %s OR email = %s",
                (TEST_CLERK_ID, TEST_EMAIL),
            )
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)


def _list_item(client, headers, repertoire_id):
    response = client.get("/api/repertoires", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()
    for item in items:
        if item["id"] == repertoire_id:
            return item
    raise AssertionError(f"repertoire {repertoire_id} missing from GET /api/repertoires")


def _create_repertoire(client, headers):
    response = client.post(
        "/api/repertoires",
        headers=headers,
        json={"name": "Score Test", "color": "white"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _start_session(client, headers, repertoire_id, mode="train"):
    response = client.post(
        f"/api/repertoires/{repertoire_id}/sessions/start",
        headers=headers,
        json={"mode": mode},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _complete_session(client, headers, session_id, body):
    return client.post(
        f"/api/repertoires/sessions/{session_id}/complete",
        headers=headers,
        json=body,
    )


def test_totals_and_accuracy(client, headers):
    rid = _create_repertoire(client, headers)

    # 1.e4 e5 2.Nf3 -> three persisted rows (white, black, white); only
    # the two white-to-move rows are quiz items for a white repertoire.
    response = client.post(
        f"/api/repertoires/{rid}/positions",
        headers=headers,
        json={"uci_moves": ["e2e4", "e7e5", "g1f3"]},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3, response.json()

    started = _start_session(client, headers, rid)
    session = started["session"]
    assert session["positions_total"] == 2, session
    assert len(started["positions"]) == 3, started["positions"]
    assert session["attempts_total"] is None, session

    # One position needed a retry/hint: 2 solved out of 3 attempts.
    response = _complete_session(
        client,
        headers,
        session["id"],
        {"positions_correct": 2, "attempts_total": 3},
    )
    assert response.status_code == 200, response.text
    assert response.json()["attempts_total"] == 3, response.json()

    item = _list_item(client, headers, rid)
    assert item["times_trained"] == 1, item
    assert item["last_trained_at"] is not None, item
    assert item["last_score_percent"] is not None, item
    assert abs(item["last_score_percent"] - (2 * 100.0 / 3)) < 0.01, item
    print(
        "  quiz-only positions_total (2 of 3 rows) + accuracy score "
        f"{item['last_score_percent']:.1f}% (2 solved / 3 attempts)"
    )

    # A clean pass is 100%, not a denominator inflated by opponent rows.
    started = _start_session(client, headers, rid)
    session = started["session"]
    response = _complete_session(
        client,
        headers,
        session["id"],
        {"positions_correct": 2, "attempts_total": 2},
    )
    assert response.status_code == 200, response.text
    item = _list_item(client, headers, rid)
    assert abs(item["last_score_percent"] - 100.0) < 0.01, item
    assert item["times_trained"] == 2, item
    print("  clean pass reads 100.0% (not 50% from opponent rows)")


def test_attempts_validation(client, headers):
    rid = _create_repertoire(client, headers)
    response = client.post(
        f"/api/repertoires/{rid}/positions",
        headers=headers,
        json={"uci_moves": ["d2d4", "d7d5"]},
    )
    assert response.status_code == 200, response.text
    started = _start_session(client, headers, rid)
    session = started["session"]
    assert session["positions_total"] == 1, session

    response = _complete_session(
        client,
        headers,
        session["id"],
        {"positions_correct": 1, "attempts_total": 0},
    )
    assert response.status_code == 400, response.text
    assert "attempts_total" in response.json()["detail"], response.json()

    # The rejected call wrote nothing: the session is still open.
    response = _complete_session(
        client,
        headers,
        session["id"],
        {"positions_correct": 1, "attempts_total": 1},
    )
    assert response.status_code == 200, response.text
    print("  attempts_total < positions_correct -> 400, nothing written")


def test_legacy_fallback_and_backfill(client, headers):
    rid = _create_repertoire(client, headers)
    response = client.post(
        f"/api/repertoires/{rid}/positions",
        headers=headers,
        json={"uci_moves": ["c2c4", "e7e5"]},
    )
    assert response.status_code == 200, response.text

    # A session completed by a client that predates attempts_total: the
    # score falls back to positions_correct / positions_total.
    started = _start_session(client, headers, rid)
    session = started["session"]
    assert session["positions_total"] == 1, session
    response = _complete_session(
        client,
        headers,
        session["id"],
        {"positions_correct": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["attempts_total"] is None, response.json()
    item = _list_item(client, headers, rid)
    assert abs(item["last_score_percent"] - 100.0) < 0.01, item

    # A legacy row with the old inflated total (10 rows for 5 quiz items).
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repertoire_training_sessions (
                    repertoire_id, mode, positions_total, positions_correct,
                    attempts_total, started_at, completed_at
                )
                VALUES (
                    %s, 'train', 10, 5, NULL,
                    NOW() - interval '2 minutes', NOW()
                )
                RETURNING id
                """,
                (rid,),
            )
            legacy_id = cur.fetchone()[0]
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)

    # Before the boot migration re-runs: the raw fallback ratio.
    item = _list_item(client, headers, rid)
    assert abs(item["last_score_percent"] - 50.0) < 0.01, item

    # The migration backfills attempts_total with the solved count, so the
    # reconstructed score is the clean run it must have been.
    run_migrations()

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT attempts_total FROM repertoire_training_sessions WHERE id = %s",
                (legacy_id,),
            )
            backfilled = cur.fetchone()[0]
    finally:
        database.connection_pool.putconn(conn)
    assert backfilled == 5, backfilled

    item = _list_item(client, headers, rid)
    assert abs(item["last_score_percent"] - 100.0) < 0.01, item
    print(
        "  legacy session: fallback ratio, then migration backfill -> "
        "attempts_total=5 and 100.0%"
    )


def main():
    client, headers = setup()
    try:
        test_totals_and_accuracy(client, headers)
        test_attempts_validation(client, headers)
        test_legacy_fallback_and_backfill(client, headers)
    finally:
        teardown()
    print("all repertoire score checks passed (test user removed)")


if __name__ == "__main__":
    main()
