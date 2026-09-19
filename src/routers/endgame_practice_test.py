"""
HTTP end-to-end verification harness for the category-picker practice mode
(src/routers/endgame_practice.py).

Everything is driven through the ASGI app with real HTTP semantics, against
the REAL seeded content (22,378 sourced positions across 7 categories).
The heavyweight boot hooks are bypassed exactly like the other endgame
harnesses; the local 3-5-man Syzygy files keep every probe offline.

Verified:

  A. Auth: missing X-Internal-Secret -> 401; missing X-Clerk-User-Id ->
     400 (categories/next/move); unknown category -> 400; valid category
     with no positions -> 404; missing ?category -> 422.
  B. Category list matches the DB exactly: same rows, same sourced counts,
     ordered by name; the 9,219 rook count excludes the 10 curated Lucena
     rows (9,229 with them); the three unseeded CHECK values are absent.
  C. Category purity + sourced payload: every draw from every listed
     category is that category, sourced (source_puzzle_id set, "(sourced)"
     topic name), and carries a legal stored solution line; repeated draws
     show pooled-random variety.
  D. Full-resolution SOLVED through POST /practice/move: a rook drill
     replayed past its stored line with server-generated opponent replies
     to real checkmate. Every response has rating=None and
     review_capture=None, users.endgame_trainer_rating is untouched, and
     NO endgame_woodpecker_entries row is created.
  E. FAILED through POST /practice/move: a thrown-away win is graded
     failed with the right category, still no rating write and no
     Woodpecker capture.
  F. No users-row dependency: a valid Clerk header for a user with no
     users row still gets categories/next (nothing user-scoped is read).

Run with: cd src && ../venv/bin/python routers/endgame_practice_test.py
Requires: DATABASE_URL / INTERNAL_SECRET from root .env, the seeded
endgame tables, and data/syzygy/regular (3-4-5).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi.testclient import TestClient

# Importing main runs the app's load_dotenv calls, builds the FastAPI app,
# and mounts the routers.
import main as app_module
from core import database
from core.migrations import run_migrations
from routers.endgames_test import (
    MAX_DRILL_PLIES,
    _any_locally_runnable,
    _past_line_qualifier,
    find_throwing_move,
    winning_move,
)

TEST_CLERK_ID = "endgame-practice-http-test"
TEST_EMAIL = "endgame-practice-http-test@example.invalid"
TEST_RATING = 880
GHOST_CLERK_ID = "endgame-practice-ghost-user"
CATEGORY = "rook"
MAX_GET_ATTEMPTS = 30
PURITY_DRAWS = 4


def _delete_test_rows(cur):
    cur.execute(
        "DELETE FROM endgame_woodpecker_entries WHERE user_id = %s", (TEST_CLERK_ID,)
    )
    cur.execute(
        "DELETE FROM users WHERE clerk_id IN (%s, %s) OR email = %s",
        (TEST_CLERK_ID, GHOST_CLERK_ID, TEST_EMAIL),
    )


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
            _delete_test_rows(cur)
            cur.execute(
                """
                INSERT INTO users (
                    clerk_id, email, skill_level,
                    tactical_rating, endgame_trainer_rating
                )
                VALUES (%s, %s, 'intermediate', 1200, %s)
                """,
                (TEST_CLERK_ID, TEST_EMAIL, TEST_RATING),
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
            _delete_test_rows(cur)
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)


def _query(sql, params=()):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        database.connection_pool.putconn(conn)


def _query_all(sql, params=()):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        database.connection_pool.putconn(conn)


def read_trainer_rating():
    row = _query(
        "SELECT endgame_trainer_rating FROM users WHERE clerk_id = %s",
        (TEST_CLERK_ID,),
    )
    assert row is not None, "test user missing"
    return row[0]


def woodpecker_entry_count(position_id):
    row = _query(
        """
        SELECT COUNT(*) FROM endgame_woodpecker_entries
        WHERE user_id = %s AND position_id = %s::uuid
        """,
        (TEST_CLERK_ID, position_id),
    )
    return row[0]


def db_sourced_category_counts():
    return _query_all(
        """
        SELECT t.category, COUNT(*)
        FROM endgame_topics t
        JOIN endgame_positions p ON p.topic_id = t.id
        WHERE p.source_puzzle_id IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """
    )


def fetch_practice_position(client, headers, *, qualifier, max_attempts=MAX_GET_ATTEMPTS):
    """Draw from CATEGORY until a locally-runnable sourced win drill that
    satisfies `qualifier` is served."""
    for _ in range(max_attempts):
        response = client.get(
            "/api/endgames/practice/next",
            headers=headers,
            params={"category": CATEGORY},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        board = chess.Board(data["fen"])
        if data["source_puzzle_id"] is None or not data["is_winning"]:
            continue
        if len(board.piece_map()) > 5:
            continue
        if qualifier(data, board):
            return data
    raise AssertionError(
        f"no suitable sourced {CATEGORY} position in {max_attempts} draws"
    )


def test_auth(client, headers):
    print("A. auth + input validation:")
    secret_only = {"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}
    for path in (
        "/api/endgames/practice/categories",
        f"/api/endgames/practice/next?category={CATEGORY}",
    ):
        response = client.get(path)
        assert response.status_code == 401, (path, response.text)
        response = client.get(path, headers=secret_only)
        assert response.status_code == 400, (path, response.text)
        assert "X-Clerk-User-Id" in response.json()["detail"]

    response = client.post(
        "/api/endgames/practice/move",
        headers=secret_only,
        json={
            "position_id": "00000000-0000-0000-0000-000000000001",
            "fen_before": chess.STARTING_FEN,
            "move": "e2e4",
            "fen_after": chess.STARTING_FEN,
        },
    )
    assert response.status_code == 400, response.text

    response = client.get(
        "/api/endgames/practice/next", headers=headers, params={"category": "pawn"}
    )
    assert response.status_code == 400, response.text
    assert "unknown category" in response.json()["detail"]

    response = client.get(
        "/api/endgames/practice/next",
        headers=headers,
        params={"category": "bishop_knight"},
    )
    assert response.status_code == 404, response.text
    assert "bishop_knight" in response.json()["detail"]

    response = client.get("/api/endgames/practice/next", headers=headers)
    assert response.status_code == 422, response.text
    print(
        "  missing secret -> 401; missing clerk -> 400; unknown category -> "
        "400; unseeded category -> 404; missing ?category -> 422"
    )


def test_categories_match_db(client, headers):
    print("B. category list vs DB:")
    response = client.get("/api/endgames/practice/categories", headers=headers)
    assert response.status_code == 200, response.text
    listed = response.json()
    assert isinstance(listed, list) and listed, listed

    expected = [
        {"category": row[0], "position_count": row[1]}
        for row in db_sourced_category_counts()
    ]
    assert listed == expected, (listed, expected)
    assert [c["category"] for c in listed] == sorted(c["category"] for c in listed)

    sourced_total = sum(c["position_count"] for c in listed)
    rook = next(c for c in listed if c["category"] == "rook")
    rook_all = _query(
        """
        SELECT COUNT(*)
        FROM endgame_topics t
        JOIN endgame_positions p ON p.topic_id = t.id
        WHERE t.category = 'rook'
        """
    )[0]
    rook_curated = _query(
        """
        SELECT COUNT(*)
        FROM endgame_topics t
        JOIN endgame_positions p ON p.topic_id = t.id
        WHERE t.category = 'rook' AND p.source_puzzle_id IS NULL
        """
    )[0]
    assert rook["position_count"] == rook_all - rook_curated, (
        rook,
        rook_all,
        rook_curated,
    )
    assert rook_curated == 10, "curated Lucena rows moved?"
    assert {"bishop_knight", "bishop_rook", "knight_rook"}.isdisjoint(
        c["category"] for c in listed
    ), listed
    assert all(c["position_count"] > 0 for c in listed), listed
    print(
        f"  {len(listed)} categories, {sourced_total} sourced positions; "
        f"rook={rook['position_count']} (excludes the {rook_curated} curated "
        f"Lucena rows); unseeded categories absent; sorted by name"
    )
    return [c["category"] for c in listed]


def test_category_purity(client, headers, categories):
    print("C. category purity + sourced payload:")
    total_draws = 0
    distinct = set()
    varied = False
    for category in categories:
        seen = set()
        for _ in range(PURITY_DRAWS):
            response = client.get(
                "/api/endgames/practice/next",
                headers=headers,
                params={"category": category},
            )
            assert response.status_code == 200, (category, response.text)
            data = response.json()
            assert data["topic_category"] == category, (category, data)
            assert data["source_puzzle_id"] is not None, data
            assert data["topic_name"].endswith("(sourced)"), data
            assert data["gameUrl"] == (
                f"https://lichess.org/training/{data['source_puzzle_id']}"
            ), data
            assert data["topic_category"] in data["themes"], data
            assert data["topic_name"] in data["themes"], data
            assert data["moves"], f"sourced draw without a stored line: {data}"

            line_board = chess.Board(data["fen"])
            for uci in data["moves"]:
                move = chess.Move.from_uci(uci)
                assert move in line_board.legal_moves, (uci, data["fen"])
                line_board.push(move)

            seen.add(data["id"])
            distinct.add(data["id"])
            total_draws += 1
        varied = varied or len(seen) > 1
    assert len(distinct) > 1 or total_draws == 1, "no variety across draws"
    assert varied, "a large category returned the same position every draw"
    print(
        f"  {total_draws} draws across {len(categories)} categories: all in "
        f"category, all sourced, {len(distinct)} distinct positions, every "
        f"stored line legal from its FEN"
    )


def play_practice_drill_to_mate(client, headers, position):
    """Replay a practice drill through POST /practice/move to checkmate,
    asserting every response is write-free (rating/review_capture None)."""
    line = position["moves"]
    board = chess.Board(position["fen"])
    user_color = board.turn
    line_index = 0
    user_moves = 0
    generated = 0
    final_body = None

    for _ in range(MAX_DRILL_PLIES):
        assert not board.is_game_over(), board.fen()
        assert board.turn == user_color, board.fen()

        if line_index < len(line):
            uci = line[line_index]
        else:
            uci = winning_move(board).uci()

        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, (uci, board.fen())
        fen_before = board.fen()
        board.push(move)
        user_moves += 1

        response = client.post(
            "/api/endgames/practice/move",
            headers=headers,
            json={
                "position_id": position["id"],
                "fen_before": fen_before,
                "move": uci,
                "fen_after": board.fen(),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rating"] is None, body
        assert body["review_capture"] is None, body

        if board.is_checkmate():
            final_body = body
            break

        assert body["status"] == "in_progress", body
        reply = body["opponent_reply"]
        if line_index + 1 < len(line):
            assert reply is None, (line_index, reply)
            line_index += 1
            board.push(chess.Move.from_uci(line[line_index]))
            line_index += 1
        else:
            assert reply is not None, body
            board.push(chess.Move.from_uci(reply["move_uci"]))
            generated += 1
            line_index = len(line)

    assert final_body is not None, f"drill did not resolve in {MAX_DRILL_PLIES} plies"
    return final_body, user_moves, generated


def test_solved_drill(client, headers):
    print("D. full-resolution SOLVED through /practice/move:")
    position = fetch_practice_position(
        client, headers, qualifier=_past_line_qualifier
    )
    rating_before = read_trainer_rating()

    final, user_moves, generated = play_practice_drill_to_mate(
        client, headers, position
    )
    assert final["status"] == "solved", final
    assert final["resolution"] == "checkmate", final
    assert final["rating"] is None, final
    assert final["review_capture"] is None, final
    assert user_moves >= 2 and generated >= 1, (user_moves, generated)
    assert read_trainer_rating() == rating_before, "practice moved the rating"
    assert woodpecker_entry_count(position["id"]) == 0, (
        "practice FAILED wired into the review queue?"
    )
    print(
        f"  {position['topic_name']} | {user_moves} user moves "
        f"({generated} generated replies) -> solved(checkmate) | rating "
        f"unchanged at {rating_before} | no Woodpecker entry"
    )
    return position


def test_failed_drill(client, headers):
    print("E. FAILED through /practice/move (no rating, no capture):")
    position = fetch_practice_position(
        client, headers, qualifier=_any_locally_runnable
    )
    board = chess.Board(position["fen"])
    throw, user_outcome = find_throwing_move(board)
    assert throw is not None, "served win drill has no throwing move"
    fen_before = board.fen()
    board.push(throw)
    rating_before = read_trainer_rating()

    response = client.post(
        "/api/endgames/practice/move",
        headers=headers,
        json={
            "position_id": position["id"],
            "fen_before": fen_before,
            "move": throw.uci(),
            "fen_after": board.fen(),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed", body
    assert body["failure_category"] is not None, body
    assert body["rating"] is None, body
    assert body["review_capture"] is None, body
    assert read_trainer_rating() == rating_before, "practice moved the rating"
    assert woodpecker_entry_count(position["id"]) == 0, (
        "practice failure was captured into Woodpecker"
    )
    print(
        f"  {position['topic_name']} | {throw.uci()} -> "
        f"failed({body['failure_category']}, user outcome {user_outcome}) | "
        f"rating unchanged at {rating_before} | no Woodpecker entry"
    )


def test_ghost_user(client, headers):
    print("F. no users-row dependency:")
    ghost_headers = {
        "X-Clerk-User-Id": GHOST_CLERK_ID,
        "X-Internal-Secret": os.environ["INTERNAL_SECRET"],
    }
    assert _query(
        "SELECT 1 FROM users WHERE clerk_id = %s", (GHOST_CLERK_ID,)
    ) is None, "ghost user unexpectedly present"

    response = client.get(
        "/api/endgames/practice/categories", headers=ghost_headers
    )
    assert response.status_code == 200, response.text
    response = client.get(
        "/api/endgames/practice/next",
        headers=ghost_headers,
        params={"category": CATEGORY},
    )
    assert response.status_code == 200, response.text
    print(
        "  valid Clerk id with no users row -> categories/next still 200 "
        "(nothing user-scoped is read)"
    )


def main():
    client, headers = setup()
    try:
        test_auth(client, headers)
        categories = test_categories_match_db(client, headers)
        test_category_purity(client, headers, categories)
        test_solved_drill(client, headers)
        test_failed_drill(client, headers)
        test_ghost_user(client, headers)
    finally:
        teardown()
    print("all Endgame Practice mode checks passed (test user removed)")


if __name__ == "__main__":
    main()
