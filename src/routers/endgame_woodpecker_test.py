"""
HTTP end-to-end verification harness for the Endgame Woodpecker review
queue (src/routers/endgame_woodpecker.py) and its FAILED-drill wiring in
POST /api/endgames/move.

Everything is driven through the ASGI app with real HTTP semantics. The
heavyweight boot hooks (Maia, Stockfish, persistent tablebase cache) are
bypassed exactly like routers/endgames_test.py; the local 3-5-man Syzygy
files keep every probe offline.

What is verified against the REAL seeded content:

  A. Auth: missing X-Internal-Secret -> 401; missing X-Clerk-User-Id ->
     400 on queue/count/attempts (and the new puzzle count).
  B. FAILED wiring: a real sourced win drill is thrown away through
     POST /api/endgames/move. The FAILED response's rating write and the
     review-card insert must both land: count -> 1, the due queue returns
     exactly that card, its drill payload equals GET /next's, and its FSRS
     columns are the fresh-miss defaults (state=1, reps=0, lapses=0).
     A second failure of the same still-active position must NOT duplicate
     the card (count stays 1, same id, reps still 0).
  B2. Separation + badge parity: a puzzle entry added through
     POST /api/woodpecker/entries increments only the puzzle count; each
     queue returns only its own type; both count endpoints mean "due now"
     (unmastered + due <= NOW()), not "any entries".
  C. Full-resolution successful review: the card is replayed move-by-move
     through POST /api/endgames/woodpecker/attempts -- stored line first,
     then server-generated opponent replies -- to REAL checkmate. Every
     in_progress move writes nothing; the mating move writes exactly one
     attempt row (solved_correctly=true) and moves FSRS Learning step 0->1.
     The trainer rating must not move during reviews.
  D. Second success (immediate, due ignored like the puzzle queue):
     Learning step 1 -> Review, reps=2.
  E. Repeated failure on the now-mature card (due reset, state genuine):
     the replay is thrown away on its first move -> failed with a real
     failure_category, Review -> Relearning lapse (lapses=1), attempt row
     solved_correctly=false, and the badge returns to 0.
  F. Integrity: unknown entry -> 404, negative time -> 400, stale
     non-winning fen_before -> 409, none of which touch the FSRS row.

Run with: cd src && ../venv/bin/python routers/endgame_woodpecker_test.py
Requires: DATABASE_URL / INTERNAL_SECRET from root .env, the seeded
endgame tables, and data/syzygy/regular (3-4-5).
"""
import os
import sys

from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi.testclient import TestClient

# Importing main runs the app's load_dotenv calls, builds the FastAPI app,
# and mounts the routers.
import main as app_module
from core import database
from core.migrations import run_migrations
from services.tablebase import TablebaseUnavailableError, probe_tablebase
from routers.endgames_test import (
    MAX_DRILL_PLIES,
    _past_line_qualifier,
    expected_delta,
    fetch_position,
    find_throwing_move,
    winning_move,
)

TEST_CLERK_ID = "endgame-woodpecker-http-test"
TEST_EMAIL = "endgame-woodpecker-http-test@example.invalid"
TEST_RATING = 880
MAX_GET_ATTEMPTS = 30
MS_PER_MOVE = 1500


def _delete_test_rows(cur):
    # woodpecker_entries -> woodpecker_attempts (CASCADE); the endgame
    # tables cascade from users too, but delete explicitly for clarity.
    cur.execute("DELETE FROM woodpecker_entries WHERE user_id = %s", (TEST_CLERK_ID,))
    cur.execute(
        "DELETE FROM endgame_woodpecker_entries WHERE user_id = %s", (TEST_CLERK_ID,)
    )
    cur.execute(
        "DELETE FROM users WHERE clerk_id = %s OR email = %s",
        (TEST_CLERK_ID, TEST_EMAIL),
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
            row = cur.fetchone()
    finally:
        database.connection_pool.putconn(conn)
    return row


def read_trainer_rating():
    row = _query(
        "SELECT endgame_trainer_rating FROM users WHERE clerk_id = %s",
        (TEST_CLERK_ID,),
    )
    assert row is not None, "test user missing"
    return row[0]


def read_entry(entry_id):
    from psycopg2.extras import RealDictCursor

    conn = database.connection_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM endgame_woodpecker_entries WHERE id = %s::uuid",
                (entry_id,),
            )
            return cur.fetchone()
    finally:
        database.connection_pool.putconn(conn)


def count_attempts(entry_id):
    row = _query(
        """
        SELECT COUNT(*) FROM endgame_woodpecker_attempts
        WHERE entry_id = %s::uuid
        """,
        (entry_id,),
    )
    return row[0]


def set_due_past(entry_id):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE endgame_woodpecker_entries
                SET due = NOW() - INTERVAL '1 minute'
                WHERE id = %s::uuid
                """,
                (entry_id,),
            )
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)


def get_queue(client, headers):
    response = client.get("/api/endgames/woodpecker/queue", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def get_count(client, headers):
    response = client.get("/api/endgames/woodpecker/count", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["due_count"]


def submit_move(client, headers, position, move):
    board = chess.Board(position["fen"])
    fen_before = board.fen()
    board.push(move)
    response = client.post(
        "/api/endgames/move",
        headers=headers,
        json={
            "position_id": position["id"],
            "fen_before": fen_before,
            "move": move.uci(),
            "fen_after": board.fen(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def submit_review_move(client, headers, entry, fen_before, move_uci, fen_after, ms):
    response = client.post(
        "/api/endgames/woodpecker/attempts",
        headers=headers,
        json={
            "entry_id": entry["id"],
            "fen_before": fen_before,
            "move": move_uci,
            "fen_after": fen_after,
            "time_taken_ms": ms,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def replay_review_to_resolution(client, headers, entry):
    """Replay the card through the review endpoint to its real resolution.

    Within the stored line the client plays the line (server replies None);
    past it the server generates the opponent move. Returns the resolving
    response plus counters.
    """
    line = entry["position"]["moves"]
    board = chess.Board(entry["position"]["fen"])
    user_color = board.turn
    line_index = 0
    user_moves = 0
    within_line = 0
    generated = 0
    last_sent_ms = 0
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
        last_sent_ms = user_moves * MS_PER_MOVE
        body = submit_review_move(
            client, headers, entry, fen_before, uci, board.fen(), last_sent_ms
        )

        if board.is_checkmate():
            final_body = body
            break

        assert body["status"] == "in_progress", body
        # An intermediate replay move must write NOTHING.
        assert body["attempt"] is None and body["scheduling"] is None, body
        assert body["failure_category"] is None, body

        reply = body["opponent_reply"]
        if line_index + 1 < len(line):
            within_line += 1
            assert reply is None, (line_index, reply)
            line_index += 1
            board.push(chess.Move.from_uci(line[line_index]))
            line_index += 1
        else:
            assert reply is not None, body
            board.push(chess.Move.from_uci(reply["move_uci"]))
            assert " ".join(board.fen().split()[:4]) == " ".join(
                reply["fen_after"].split()[:4]
            ), reply
            generated += 1
            line_index = len(line)

    assert final_body is not None, f"review did not resolve in {MAX_DRILL_PLIES} plies"
    return final_body, user_moves, within_line, generated, last_sent_ms


def test_auth(client, headers):
    print("A. auth:")
    for path in (
        "/api/endgames/woodpecker/queue",
        "/api/endgames/woodpecker/count",
        "/api/woodpecker/count",
    ):
        response = client.get(path)
        assert response.status_code == 401, (path, response.text)
        response = client.get(
            path, headers={"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}
        )
        assert response.status_code == 400, (path, response.text)
        assert "X-Clerk-User-Id" in response.json()["detail"]
    response = client.post(
        "/api/endgames/woodpecker/attempts",
        headers={"X-Internal-Secret": os.environ["INTERNAL_SECRET"]},
        json={
            "entry_id": str(uuid4()),
            "fen_before": chess.STARTING_FEN,
            "move": "e2e4",
            "fen_after": chess.STARTING_FEN,
            "time_taken_ms": 1,
        },
    )
    assert response.status_code == 400, response.text
    print("  missing secret -> 401; missing clerk id -> 400 (queue/count/attempts)")


def test_failed_drill_queues_card(client, headers):
    print("B. FAILED trainer drill -> endgame queue card:")

    position = fetch_position(
        client, headers, qualifier=_past_line_qualifier, max_attempts=MAX_GET_ATTEMPTS
    )
    board = chess.Board(position["fen"])
    throw, user_outcome = find_throwing_move(board)
    assert throw is not None, "served win drill has no throwing move"

    rating_before = read_trainer_rating()
    body = submit_move(client, headers, position, throw)
    assert body["status"] == "failed", body
    capture = body["review_capture"]
    assert capture is not None, body
    rating = body["rating"]
    expected = expected_delta(rating_before, position["rating"], False)
    assert rating["change"] == expected, (rating, expected)
    assert read_trainer_rating() == rating["new_rating"], "rating not persisted"
    print(
        f"  served {position['topic_name']} | rating={position['rating']} | "
        f"throwing move={throw.uci()} ({capture['failure_category']}) -> "
        f"rating {rating_before} -> {rating['new_rating']}"
    )

    assert get_count(client, headers) == 1, "FAILED drill did not queue a card"
    queue = get_queue(client, headers)
    assert len(queue) == 1, queue
    entry = queue[0]

    # The card is the capture: same position, theme, reason, timestamp.
    assert entry["position_id"] == capture["position_id"], entry
    assert entry["theme"] == capture["theme"], entry
    assert entry["source_reason"] == capture["source_reason"], entry
    assert entry["is_mastered"] is False and entry["mastered_at"] is None, entry
    assert entry["state"] == 1 and entry["step"] is None, entry
    assert entry["stability"] is None and entry["difficulty"] is None, entry
    assert entry["reps"] == 0 and entry["lapses"] == 0, entry
    assert entry["last_review"] is None, entry
    assert datetime.fromisoformat(entry["added_at"]).tzinfo is not None, entry
    assert datetime.fromisoformat(entry["due"]) <= datetime.now(timezone.utc), entry

    # The nested drill payload must equal GET /next's response field-for-field.
    served_position = entry["position"]
    for key in (
        "id",
        "fen",
        "moves",
        "rating",
        "themes",
        "gameUrl",
        "is_winning",
        "topic_id",
        "topic_name",
        "topic_category",
        "source_puzzle_id",
    ):
        assert served_position[key] == position[key], (key, served_position, position)
    print(
        f"  card {entry['id'][:8]}... | state=1 due<=now reps=0 lapses=0 | "
        f"payload matches GET /next ({len(served_position['moves'])} stored plies)"
    )

    # Repeat failure of the same still-active drill: same card, no duplicate.
    added_at = entry["added_at"]
    again = submit_move(client, headers, position, throw)
    assert again["status"] == "failed", again
    assert get_count(client, headers) == 1, "repeat failure duplicated the card"
    queue = get_queue(client, headers)
    assert len(queue) == 1 and queue[0]["id"] == entry["id"], queue
    assert queue[0]["added_at"] == added_at, "duplicate capture re-inserted"
    assert queue[0]["reps"] == 0 and queue[0]["lapses"] == 0, queue[0]
    print("  repeat failure -> same card, count still 1, reps still 0")

    return entry


def test_separation_and_badge_parity(client, headers, entry):
    print("B2. separation from the puzzle queue + badge parity:")

    # Only an endgame card exists so far: the puzzle badge is 0.
    response = client.get("/api/woodpecker/count", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["due_count"] == 0, response.json()
    response = client.get("/api/woodpecker/queue", headers=headers)
    assert response.status_code == 200 and response.json() == [], response.text

    response = client.post(
        "/api/woodpecker/entries",
        headers=headers,
        json={"puzzle_id": "wp-badge-parity-test", "theme": "fork"},
    )
    assert response.status_code == 200, response.text
    puzzle_entry = response.json()
    assert puzzle_entry.get("skipped") is not True, puzzle_entry

    puzzle_count = client.get("/api/woodpecker/count", headers=headers).json()[
        "due_count"
    ]
    endgame_count = get_count(client, headers)
    assert puzzle_count == 1, puzzle_count
    assert endgame_count == 1, endgame_count

    puzzle_queue = client.get("/api/woodpecker/queue", headers=headers).json()
    assert len(puzzle_queue) == 1 and puzzle_queue[0]["puzzle_id"] == "wp-badge-parity-test"
    endgame_queue = get_queue(client, headers)
    assert len(endgame_queue) == 1 and endgame_queue[0]["id"] == entry["id"]
    print(
        "  puzzle entry dents only the puzzle badge (1|1); each due queue "
        "returns only its own card type"
    )


def test_successful_review(client, headers, entry):
    print("C. full-resolution successful review through the review endpoint:")

    rating_before = read_trainer_rating()
    final, user_moves, within_line, generated, last_ms = replay_review_to_resolution(
        client, headers, entry
    )

    assert final["status"] == "solved", final
    assert final["resolution"] == "checkmate", final
    assert final["failure_category"] is None, final
    assert user_moves >= 2, user_moves
    assert within_line >= 1, "stored-line phase never exercised"
    assert generated >= 1, "server never generated an opponent reply"

    attempt = final["attempt"]
    scheduling = final["scheduling"]
    assert attempt is not None and scheduling is not None, final
    assert attempt["solved_correctly"] is True, attempt
    assert attempt["time_taken_ms"] == last_ms, attempt
    assert scheduling["prior_state"] == 1 and scheduling["rating"] == 3, scheduling
    assert scheduling["new_state"] == 1 and scheduling["step"] == 1, scheduling
    assert scheduling["reps"] == 1 and scheduling["lapses"] == 0, scheduling
    assert scheduling["is_mastered"] is False, scheduling
    assert datetime.fromisoformat(scheduling["due"]) > datetime.now(timezone.utc), scheduling

    row = read_entry(entry["id"])
    assert row["state"] == 1 and row["step"] == 1, row
    assert row["reps"] == 1 and row["lapses"] == 0, row
    assert row["is_mastered"] is False, row
    assert row["last_review"] is not None, "last_review not persisted"
    assert row["due"] == datetime.fromisoformat(scheduling["due"]), row
    assert count_attempts(entry["id"]) == 1, "expected exactly one attempt row"
    assert read_trainer_rating() == rating_before, "review moved the trainer rating"

    # The card is now unmastered but NOT due: the badge means due-now.
    assert get_count(client, headers) == 0, "future-due card counted as due"
    assert get_queue(client, headers) == [], "future-due card served"
    print(
        f"  {user_moves} user moves ({within_line} on the stored line, "
        f"{generated} generated replies) -> solved(checkmate) | FSRS "
        f"Learning 0->1, due {scheduling['due']}, reps=1 | badge 0 "
        f"(card exists, not due) | trainer rating untouched"
    )


def test_second_success_graduates(client, headers, entry):
    print("D. second successful replay (due ignored, like the puzzle queue):")

    final, user_moves, _, _, _ = replay_review_to_resolution(client, headers, entry)
    assert final["status"] == "solved" and final["resolution"] == "checkmate", final
    scheduling = final["scheduling"]
    assert scheduling["prior_state"] == 1 and scheduling["rating"] == 3, scheduling
    assert scheduling["new_state"] == 2 and scheduling["step"] is None, scheduling
    assert scheduling["reps"] == 2 and scheduling["lapses"] == 0, scheduling
    assert scheduling["is_mastered"] is False, scheduling

    row = read_entry(entry["id"])
    assert row["state"] == 2 and row["step"] is None, row
    assert row["reps"] == 2 and row["lapses"] == 0, row
    assert count_attempts(entry["id"]) == 2, "expected two attempt rows"
    print(
        f"  {user_moves} user moves -> solved | FSRS Learning 1 -> Review, "
        f"reps=2, lapses=0"
    )


def test_repeated_failure(client, headers, entry):
    print("E. repeated failure on the now-mature card:")

    rating_before = read_trainer_rating()
    set_due_past(entry["id"])
    assert get_count(client, headers) == 1, "due-reset card not served"
    queue = get_queue(client, headers)
    assert len(queue) == 1 and queue[0]["id"] == entry["id"], queue
    assert queue[0]["state"] == 2, queue[0]

    board = chess.Board(entry["position"]["fen"])
    throw, user_outcome = find_throwing_move(board)
    assert throw is not None
    fen_before = board.fen()
    board.push(throw)
    response = client.post(
        "/api/endgames/woodpecker/attempts",
        headers=headers,
        json={
            "entry_id": entry["id"],
            "fen_before": fen_before,
            "move": throw.uci(),
            "fen_after": board.fen(),
            "time_taken_ms": MS_PER_MOVE,
        },
    )
    assert response.status_code == 200, response.text
    final = response.json()
    assert final["status"] == "failed", final
    assert final["failure_category"] is not None, final
    assert final["resolution"] is None, final  # transition failure, not board end

    attempt = final["attempt"]
    scheduling = final["scheduling"]
    assert attempt is not None and scheduling is not None, final
    assert attempt["solved_correctly"] is False, attempt
    assert attempt["failure_category"] == final["failure_category"], attempt
    assert scheduling["prior_state"] == 2 and scheduling["rating"] == 1, scheduling
    assert scheduling["new_state"] == 3 and scheduling["lapses"] == 1, scheduling
    assert scheduling["reps"] == 3, scheduling
    assert scheduling["is_mastered"] is False, scheduling

    row = read_entry(entry["id"])
    assert row["state"] == 3 and row["lapses"] == 1 and row["reps"] == 3, row
    assert count_attempts(entry["id"]) == 3, "expected three attempt rows"
    assert read_trainer_rating() == rating_before, "review moved the trainer rating"
    assert get_count(client, headers) == 0, "Relearning card counted as due"
    assert get_queue(client, headers) == [], "Relearning card served"
    print(
        f"  first move {throw.uci()} -> failed({final['failure_category']}) | "
        f"FSRS Review -> Relearning, lapses=1, reps=3 | badge back to 0 | "
        f"trainer rating untouched"
    )


def test_integrity(client, headers, entry):
    print("F. review integrity (no FSRS write on rejection):")
    row_before = read_entry(entry["id"])
    attempts_before = count_attempts(entry["id"])

    response = client.post(
        "/api/endgames/woodpecker/attempts",
        headers=headers,
        json={
            "entry_id": str(uuid4()),
            "fen_before": chess.STARTING_FEN,
            "move": "e2e4",
            "fen_after": chess.STARTING_FEN,
            "time_taken_ms": 10,
        },
    )
    assert response.status_code == 404, response.text

    response = client.post(
        "/api/endgames/woodpecker/attempts",
        headers=headers,
        json={
            "entry_id": entry["id"],
            "fen_before": entry["position"]["fen"],
            "move": "0000",
            "fen_after": entry["position"]["fen"],
            "time_taken_ms": -1,
        },
    )
    assert response.status_code == 400, response.text
    assert "time_taken_ms" in response.json()["detail"]

    # Stale drill state: the grader is stateless and always treats
    # fen_before's side to move as the user, so the probe must first reach
    # a USER-to-move position that is no longer a win, then submit a legal
    # move -> 409 non-winning fen_before (same construction as the trainer
    # harness's stale-state probe).
    board = chess.Board(entry["position"]["fen"])
    throw, _ = find_throwing_move(board)
    board.push(throw)  # opponent to move; the user is no longer winning
    stale_fen = None
    for reply in sorted(board.legal_moves, key=lambda m: m.uci()):
        child = board.copy(stack=False)
        child.push(reply)
        if child.is_checkmate() or child.is_stalemate():
            continue
        if len(child.piece_map()) < 3:
            continue
        try:
            outcome = probe_tablebase(child.fen()).outcome
        except TablebaseUnavailableError:
            continue
        if outcome != "win":
            stale_fen = child.fen()
            break
    assert stale_fen is not None, "no non-winning user-to-move follow-up"

    stale = chess.Board(stale_fen)
    move = next(iter(stale.legal_moves))
    derived = stale.copy(stack=False)
    derived.push(move)
    response = client.post(
        "/api/endgames/woodpecker/attempts",
        headers=headers,
        json={
            "entry_id": entry["id"],
            "fen_before": stale_fen,
            "move": move.uci(),
            "fen_after": derived.fen(),
            "time_taken_ms": 10,
        },
    )
    assert response.status_code == 409, response.text
    assert "non-winning fen_before" in response.json()["detail"]

    row_after = read_entry(entry["id"])
    assert row_after == row_before, "rejected review changed the FSRS row"
    assert count_attempts(entry["id"]) == attempts_before
    print(
        "  unknown entry -> 404; negative time -> 400; stale non-winning "
        "fen_before -> 409; FSRS row and attempts untouched"
    )


def main():
    client, headers = setup()
    try:
        test_auth(client, headers)
        entry = test_failed_drill_queues_card(client, headers)
        test_separation_and_badge_parity(client, headers, entry)
        test_successful_review(client, headers, entry)
        test_second_success_graduates(client, headers, entry)
        test_repeated_failure(client, headers, entry)
        test_integrity(client, headers, entry)
    finally:
        teardown()
    print("all Endgame Woodpecker queue checks passed (test user removed)")


if __name__ == "__main__":
    main()
