"""
HTTP end-to-end verification harness for the Endgame Trainer MVP endpoints
(src/routers/endgames.py).

Everything is driven through the ASGI app with real HTTP semantics
(TestClient -> middleware -> routing -> dependencies -> handlers), NOT
direct service calls. The app's heavyweight boot hooks (Maia, Stockfish,
persistent tablebase cache) are intentionally bypassed: init_db() and
run_migrations() are invoked explicitly because the endpoints under test
need only the DB pool, and starting the engines would add minutes with no
bearing on the loop.

What is verified against the REAL 22,378-row sourced import (not the
curated Lucena fixture):

  A. Auth: missing X-Clerk-User-Id -> 400; missing X-Internal-Secret ->
     401 (the app middleware).
  A2. Rating fallback: with endgame_trainer_rating NULL, GET /next serves
     from the skill_level band window (the Puzzles-mirroring fallback).
  B. GET /api/endgames/next: authenticated, response mirrors the six
     PuzzleResponse keys plus the endgame extras; the served position is a
     sourced row (source_puzzle_id set, topic name ends in "(sourced)"),
     its topic difficulty sits inside the user's +/-100 window, and its
     `moves` line is legal from the returned FEN.
  C. SOLVED by full resolution: a served sourced win drill (<=5 men, line
     replayed to a USER-delivered checkmate, >=2 user moves) is played
     move-by-move through POST /api/endgames/move. In-progress moves must
     carry no rating/review payload; the mating move must return
     solved(checkmate), the rating delta for solve, and the persisted
     users.endgame_trainer_rating must equal old + delta.
  D. FAILED: another served sourced win drill is thrown away with a
     locally-probed losing move. The response must be failed with the
     exact transition failure_category, a correctly-computed negative
     rating delta (persisted), and a review_capture block carrying the
     position id, failure reason, timestamp, theme and source_reason --
     i.e. everything the future Woodpecker-style queue will persist.
  D2. Full drill to resolution: a sourced win drill whose stored line ends
     short of mate is played through POST /api/endgames/move. While the
     line lasts the server must NOT generate replies (the client uses the
     stored line); once the line is exhausted the response must carry a
     tablebase-generated opponent_reply whose fen_after drives the next
     user move. The drill must then reach actual checkmate, with the
     solved rating update persisted.
  E. Integrity: fabricated fen_after / illegal move -> 400 and a stale
     non-winning fen_before for the same drill -> 409, all through the
     real endpoint, with the rating left untouched.
  F. "Get solution": the hint route returns one legal, win-preserving,
     deterministic tablebase move and writes nothing; the hinted move then
     grades cleanly through POST /move; and a hint-assisted SOLVE is
     NEUTRAL -- rating change 0, old == new, the DB column byte-identical,
     and an unrated user is not given a first rating by a hinted solve
     (all with hints_used echoed on the responses).

Random selection is pooled-random, so the harness retries GET /next until
the served drill is suitable for the local (Syzygy <=5-man) runner. The
test user's rating (880) targets the 780-980 window, which carries the
densest population of <=5-man mate-ending sourced rows, keeping the retry
count comfortably bounded.

Run with: cd src && ../venv/bin/python routers/endgames_test.py
Requires: DB_* or DATABASE_URL from src/.env / root .env, the seeded
endgame tables (22,378 sourced rows), and data/syzygy/regular (3-4-5).
"""
import os
import sys

from datetime import datetime
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import psycopg2
from fastapi.testclient import TestClient

from services.tablebase import TablebaseUnavailableError, probe_tablebase

# Importing main runs the app's load_dotenv calls (root .env then
# src/.env), builds the FastAPI app, and mounts the routers.
import main as app_module
from core import database
from core.migrations import run_migrations

TEST_CLERK_ID = "endgame-trainer-http-test"
TEST_EMAIL = "endgame-trainer-http-test@example.invalid"
TEST_RATING = 880
TEST_SKILL_LEVEL = "intermediate"
# /next draws within this radius of the user's CURRENT rating. The suite's
# own tests move that rating (D/D2), so fetch_position derives the window at
# call time rather than pinning it to the fixture's initial value.
RATING_WINDOW_RADIUS = 100

MAX_GET_ATTEMPTS_SOLVED = 60
MAX_GET_ATTEMPTS_FAILED = 20
MAX_GET_ATTEMPTS_PAST_LINE = 30
MAX_DRILL_PLIES = 80
# Minimum solution-line length for the SOLVED drill: >= 3 plies means at
# least two user moves, so the run exercises in_progress before the mating
# move rather than only a one-move "mate in 1".
MIN_SOLVED_PLIES = 3

_INVERT = {"win": "loss", "draw": "draw", "loss": "win"}


def expected_delta(old_rating: int, topic_rating: int, solved: bool) -> int:
    """core.rating.calculate_rating_change's documented band table,
    re-derived here so the assertion is independent of the implementation
    the endpoint calls."""
    difficulty = topic_rating - old_rating
    if solved:
        if difficulty >= 100:
            return 8
        if difficulty <= -100:
            return 3
        return 5
    if difficulty >= 100:
        return -3
    if difficulty <= -100:
        return -8
    return -5


def clamp(rating: int) -> int:
    return max(400, min(3000, rating))


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
                """
                INSERT INTO users (
                    clerk_id, email, skill_level,
                    tactical_rating, endgame_trainer_rating
                )
                VALUES (%s, %s, %s, 1200, %s)
                """,
                (TEST_CLERK_ID, TEST_EMAIL, TEST_SKILL_LEVEL, TEST_RATING),
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
            cur.execute(
                "DELETE FROM users WHERE clerk_id = %s OR email = %s",
                (TEST_CLERK_ID, TEST_EMAIL),
            )
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)


def read_rating(clerk_id: str = TEST_CLERK_ID):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT endgame_trainer_rating FROM users WHERE clerk_id = %s",
                (clerk_id,),
            )
            row = cur.fetchone()
    finally:
        database.connection_pool.putconn(conn)
    assert row is not None, "test user missing"
    return row[0]


def count_review_cards():
    """The endgame review queue rows for the test user: the hint route is
    read-only, so this must not move."""
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM endgame_woodpecker_entries
                WHERE user_id = %s
                """,
                (TEST_CLERK_ID,),
            )
            return cur.fetchone()[0]
    finally:
        database.connection_pool.putconn(conn)


def write_rating(value):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET endgame_trainer_rating = %s WHERE clerk_id = %s",
                (value, TEST_CLERK_ID),
            )
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)


def replay(fen: str, moves) -> chess.Board:
    board = chess.Board(fen)
    for uci in moves:
        board.push(chess.Move.from_uci(uci))
    return board


def _mate_line_qualifier(data, board):
    """Stored line ends in a checkmate delivered by the user."""
    line_board = replay(data["fen"], data["moves"])
    user_color = board.turn
    return (
        line_board.is_checkmate()
        and line_board.turn != user_color
        and len(data["moves"]) >= MIN_SOLVED_PLIES
    )


def _any_locally_runnable(data, board):
    return True


def _past_line_qualifier(data, board):
    """Stored line ends short of mate, giving the generator real work."""
    # Keep every child inside local 3-5-man coverage: a pawn can promote
    # (adding a 6th man) and leave the local Syzygy set.
    if any(p.piece_type == chess.PAWN for p in board.piece_map().values()):
        return False
    line = data["moves"]
    # >=3 plies guarantees at least one within-line opponent reply to test
    # the negative trigger; <=9 keeps the HTTP run short.
    if not (3 <= len(line) <= 9):
        return False
    try:
        end = replay(data["fen"], line)
    except ValueError:
        return False
    return not (end.is_checkmate() or end.is_stalemate())


def fetch_position(client, headers, *, qualifier, max_attempts: int):
    """GET /next until a locally-runnable sourced win drill matching
    `qualifier` is served."""
    # The server's band is centered on the rating the user has RIGHT NOW;
    # earlier tests move it, so a fixed fixture window would drift. The
    # user is the one the caller's headers authenticate as -- other suites
    # import this helper with their own test user.
    current_rating = read_rating(headers["X-Clerk-User-Id"]) or TEST_RATING
    assert_min = current_rating - RATING_WINDOW_RADIUS
    assert_max = current_rating + RATING_WINDOW_RADIUS
    seen = {"attempts": 0, "sourced": 0, "solvable": 0}
    for _ in range(max_attempts):
        seen["attempts"] += 1
        response = client.get("/api/endgames/next", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()

        for key in ("id", "fen", "moves", "rating", "themes", "gameUrl",
                    "is_winning", "topic_id", "topic_name", "topic_category",
                    "source_puzzle_id"):
            assert key in data, f"missing response key {key!r}"

        assert assert_min <= data["rating"] <= assert_max, (
            f"served rating {data['rating']} outside window "
            f"{assert_min}-{assert_max}"
        )
        assert data["topic_category"] in data["themes"], data["themes"]
        assert data["topic_name"] in data["themes"], data["themes"]
        board = chess.Board(data["fen"])

        if data["source_puzzle_id"] is None:
            continue
        seen["sourced"] += 1
        assert data["topic_name"].endswith("(sourced)"), data["topic_name"]
        assert data["gameUrl"] == (
            f"https://lichess.org/training/{data['source_puzzle_id']}"
        )
        if not data["is_winning"] or len(board.piece_map()) > 5:
            continue
        seen["solvable"] += 1

        if not qualifier(data, board):
            continue
        return data

    raise AssertionError(
        f"no suitable sourced position served in {max_attempts} draws "
        f"(sourced={seen['sourced']}, locally-solvable={seen['solvable']})"
    )


def winning_move(board: chess.Board) -> chess.Move:
    """The USER's move in a winning position: immediate mate, else the
    DTZ-optimal conversion, mirroring the generator's win policy
    (services/endgame_reply._pick_candidate):
      * root DTZ == 1 -> play a win-preserving ZEROING move (capture or
        pawn push). |child DTZ| is meaningless across a phase reset: in a
        KR vs KR win with the enemy rook en prise, a waiting rook move
        shows child dtz -3 vs the immediate capture's KR vs K child -18,
        yet the capture is correct -- preferring the smaller |dtz| walks
        the drill back into a stored-line position and repeats.
      * root DTZ > 1 -> no winning zeroing move exists; the optimal child
        (loser's DTZ = -(root_dtz - 1)) is the closest to the phase's
        zeroing, i.e. minimize |child DTZ|.
    Local Syzygy only, so the harness stays offline."""
    root_dtz = probe_tablebase(board.fen()).dtz
    candidates = []  # (is_zeroing, abs(child dtz), move)
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        child = board.copy(stack=False)
        child.push(move)
        if child.is_checkmate():
            return move
        if child.is_stalemate() or child.is_insufficient_material():
            continue
        try:
            result = probe_tablebase(child.fen())
        except TablebaseUnavailableError:
            continue
        if result.outcome != "loss":
            continue
        candidates.append(
            (
                board.is_capture(move) or move.promotion is not None,
                abs(result.dtz or 0),
                move,
            )
        )
    assert candidates, f"no winning move found in {board.fen()}"
    pool = candidates
    if root_dtz == 1:
        zeroing = [c for c in candidates if c[0]]
        if zeroing:
            pool = zeroing
    pool.sort(key=lambda c: (c[1], c[2].uci()))
    return pool[0][2]


def find_throwing_move(board: chess.Board):
    """A legal move after which the USER's tablebase outcome is no longer a
    win (draw or loss), skipping moves that end the game on the board."""
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        child = board.copy(stack=False)
        child.push(move)
        if child.is_checkmate() or child.is_stalemate():
            continue
        # Two bare kings (and anything below 3 men) has no local Syzygy
        # file; skip so both this probe and the endpoint's grading stay on
        # the local (offline) path.
        if len(child.piece_map()) < 3:
            continue
        try:
            result = probe_tablebase(child.fen())
        except TablebaseUnavailableError:
            continue
        if _INVERT[result.outcome] in ("draw", "loss"):
            return move, _INVERT[result.outcome]
    return None, None


def test_auth(client, headers):
    print("A. auth:")
    response = client.get("/api/endgames/next")
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Unauthorized"

    no_clerk = {"X-Internal-Secret": headers["X-Internal-Secret"]}
    response = client.get("/api/endgames/next", headers=no_clerk)
    assert response.status_code == 400, response.text
    assert "X-Clerk-User-Id" in response.json()["detail"]
    print("  missing internal secret -> 401; missing Clerk id -> 400")


def test_rating_fallback(client, headers):
    print("A2. rating fallback when endgame_trainer_rating is NULL:")
    write_rating(None)
    try:
        response = client.get("/api/endgames/next", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        # TEST_SKILL_LEVEL='intermediate' -> SKILL_RATING_BANDS 1300-1600.
        assert 1300 <= data["rating"] <= 1600, data["rating"]
        print(
            f"  NULL rating -> skill band window honored "
            f"(served rating={data['rating']})"
        )
    finally:
        write_rating(TEST_RATING)
        assert read_rating() == TEST_RATING


def test_solved(client, headers):
    print("B/C. GET /next + full-resolution SOLVED through the endpoints:")
    position = fetch_position(
        client,
        headers,
        qualifier=_mate_line_qualifier,
        max_attempts=MAX_GET_ATTEMPTS_SOLVED,
    )
    print(
        f"  served sourced drill: {position['topic_name']} | "
        f"rating={position['rating']} | is_winning={position['is_winning']} | "
        f"puzzle={position['source_puzzle_id']} | moves={len(position['moves'])}"
    )

    board = chess.Board(position["fen"])
    user_color = board.turn
    old_rating = read_rating()
    expected = expected_delta(old_rating, position["rating"], True)

    final_body = None
    graded_user_moves = 0
    in_progress_moves = 0
    for uci in position["moves"]:
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, (uci, board.fen())
        if board.turn != user_color:
            board.push(move)  # opponent reply, not graded
            continue

        fen_before = board.fen()
        board.push(move)
        fen_after = board.fen()
        response = client.post(
            "/api/endgames/move",
            headers=headers,
            json={
                "position_id": position["id"],
                "fen_before": fen_before,
                "move": uci,
                "fen_after": fen_after,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        graded_user_moves += 1

        if board.is_checkmate():
            final_body = body
            break
        assert body["status"] == "in_progress", body
        assert body["failure_category"] is None
        assert body["resolution"] is None
        assert body["rating"] is None, "in_progress must not write the rating"
        assert body["review_capture"] is None
        assert body["outcome_before"] == "win" and body["outcome_after"] == "win"
        in_progress_moves += 1

    assert final_body is not None, "the verified mate line never resolved"
    assert final_body["status"] == "solved", final_body
    assert final_body["resolution"] == "checkmate", final_body
    assert final_body["failure_category"] is None
    assert final_body["common_mistake"] is None
    assert final_body["review_capture"] is None
    assert in_progress_moves >= 1, (
        "solved drill never exercised the in_progress transition"
    )

    rating = final_body["rating"]
    assert rating["old_rating"] == old_rating, rating
    assert rating["change"] == expected, (rating, expected)
    assert rating["new_rating"] == clamp(old_rating + expected), rating
    assert read_rating() == rating["new_rating"], "DB rating was not updated"
    print(
        f"  {graded_user_moves} graded user moves "
        f"({in_progress_moves} in_progress) -> solved(checkmate) | "
        f"rating {rating['old_rating']} -> {rating['new_rating']} "
        f"({rating['change']:+d}) | DB row matches"
    )
    return position


def test_failed(client, headers):
    print("D. FAILED through the endpoints + review-capture payload:")
    position = fetch_position(
        client,
        headers,
        qualifier=_any_locally_runnable,
        max_attempts=MAX_GET_ATTEMPTS_FAILED,
    )
    board = chess.Board(position["fen"])
    throw, user_outcome = find_throwing_move(board)
    assert throw is not None, "served win drill has no throwing move?"
    print(
        f"  served sourced drill: {position['topic_name']} | "
        f"rating={position['rating']} | throwing move={throw.uci()} "
        f"(user outcome -> {user_outcome})"
    )

    old_rating = read_rating()
    expected = expected_delta(old_rating, position["rating"], False)
    expected_category = (
        "threw_away_win" if user_outcome == "draw" else "blundered_into_loss"
    )

    fen_before = board.fen()
    board.push(throw)
    response = client.post(
        "/api/endgames/move",
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
    assert body["failure_category"] == expected_category, body
    assert body["resolution"] is None, "mid-game failure, not a board resolution"

    rating = body["rating"]
    assert rating["old_rating"] == old_rating, rating
    assert rating["change"] == expected, (rating, expected)
    assert rating["new_rating"] == clamp(old_rating + expected), rating
    assert read_rating() == rating["new_rating"], "DB rating was not updated"

    capture = body["review_capture"]
    assert capture is not None, "FAILED response missing review_capture"
    assert capture["position_id"] == position["id"]
    assert capture["failure_category"] == expected_category
    assert capture["source_reason"] == "wrong_answer"
    assert capture["theme"] == position["topic_category"]
    assert capture["topic_id"] == position["topic_id"]
    assert capture["fen"] == position["fen"]
    assert capture["is_winning"] is True
    assert capture["source_puzzle_id"] == position["source_puzzle_id"]
    parsed = datetime.fromisoformat(capture["failed_at"])
    assert parsed.tzinfo is not None, capture["failed_at"]
    print(
        f"  failed({expected_category}) | rating {rating['old_rating']} -> "
        f"{rating['new_rating']} ({rating['change']:+d}) | DB row matches | "
        f"review_capture: position_id={capture['position_id'][:8]}... "
        f"failed_at={capture['failed_at']}"
    )
    # Fen after the throwing move: a lost/drawn position for the user, used
    # below as the stale-drill-state probe.
    return position, board.fen()


def test_reply_to_resolution(client, headers):
    print("D2. stored line -> generated replies -> actual checkmate over HTTP:")
    position = fetch_position(
        client,
        headers,
        qualifier=_past_line_qualifier,
        max_attempts=MAX_GET_ATTEMPTS_PAST_LINE,
    )
    line = position["moves"]
    board = chess.Board(position["fen"])
    user_color = board.turn
    old_rating = read_rating()
    expected = expected_delta(old_rating, position["rating"], True)
    print(
        f"  served sourced drill: {position['topic_name']} | "
        f"rating={position['rating']} | stored line={len(line)} plies "
        f"({len(line[::2])} user moves before generation)"
    )

    line_index = 0
    generated_replies = []
    within_line_responses = 0
    user_moves = 0
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

        response = client.post(
            "/api/endgames/move",
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
        user_moves += 1

        if board.is_checkmate():
            final_body = body
            break

        assert body["status"] == "in_progress", body
        reply = body["opponent_reply"]

        if line_index + 1 < len(line):
            # The user's move was still on the stored line and an opponent
            # stored ply follows: the server must NOT generate.
            within_line_responses += 1
            assert reply is None, (line_index, reply)
            line_index += 1
            board.push(chess.Move.from_uci(line[line_index]))
            line_index += 1
        else:
            # Line exhausted: the generator must answer.
            assert reply is not None, (line_index, body)
            assert reply["source"] == "tablebase", reply
            reply_move = chess.Move.from_uci(reply["move_uci"])
            assert reply_move in board.legal_moves, reply
            board.push(reply_move)
            assert " ".join(board.fen().split()[:4]) == " ".join(
                reply["fen_after"].split()[:4]
            ), reply
            assert board.turn == user_color, board.fen()
            generated_replies.append(reply)
            line_index = len(line)

    assert final_body is not None, (
        f"drill did not resolve within {MAX_DRILL_PLIES} plies"
    )
    assert final_body["status"] == "solved", final_body
    assert final_body["resolution"] == "checkmate", final_body
    assert final_body["opponent_reply"] is None
    assert within_line_responses >= 1, "negative trigger never exercised"
    assert generated_replies, "generator never fired"

    rating = final_body["rating"]
    assert rating["old_rating"] == old_rating, rating
    assert rating["change"] == expected, (rating, expected)
    assert rating["new_rating"] == clamp(old_rating + expected), rating
    assert read_rating() == rating["new_rating"], "DB rating was not updated"
    print(
        f"  {user_moves} user moves ({within_line_responses} within-line "
        f"stored replies, {len(generated_replies)} generated) -> "
        f"solved(checkmate) | rating {rating['old_rating']} -> "
        f"{rating['new_rating']} ({rating['change']:+d}) | DB row matches"
    )


def test_integrity(client, headers, position, failed_position, lost_fen):
    print("E. integrity through the endpoint (rating untouched):")
    before = read_rating()
    board = chess.Board(position["fen"])
    legal = next(iter(board.legal_moves))
    # Claim the PRE-move position as the result: a legal move always
    # changes the board, so this can never match the derived position.
    response = client.post(
        "/api/endgames/move",
        headers=headers,
        json={
            "position_id": position["id"],
            "fen_before": position["fen"],
            "move": legal.uci(),
            "fen_after": position["fen"],
        },
    )
    assert response.status_code == 400, response.text

    response = client.post(
        "/api/endgames/move",
        headers=headers,
        json={
            "position_id": position["id"],
            "fen_before": position["fen"],
            "move": "0000",  # the UCI null move is never legal
            "fen_after": position["fen"],
        },
    )
    assert response.status_code == 400, response.text
    assert "illegal move" in response.json()["detail"]

    # Stale drill state: play the failed drill one opponent reply further
    # until it is the USER's turn in a non-winning position, then submit a
    # move for the same position id. The win-drill guard must reject it as
    # a 409 state conflict and must not move the rating. (The grader is
    # stateless and always treats the fen_before side to move as the user,
    # so the probe has to reach the user's turn first.)
    stale_board = chess.Board(lost_fen)
    stale_fen = None
    for reply in sorted(stale_board.legal_moves, key=lambda m: m.uci()):
        child = stale_board.copy(stack=False)
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

    stale_turn = chess.Board(stale_fen)
    move = next(iter(stale_turn.legal_moves))
    derived = stale_turn.copy(stack=False)
    derived.push(move)
    response = client.post(
        "/api/endgames/move",
        headers=headers,
        json={
            "position_id": failed_position["id"],
            "fen_before": stale_fen,
            "move": move.uci(),
            "fen_after": derived.fen(),
        },
    )
    assert response.status_code == 409, response.text
    assert "non-winning fen_before" in response.json()["detail"]
    assert read_rating() == before
    print(
        "  fabricated fen_after -> 400; illegal move -> 400; stale "
        "non-winning fen_before -> 409; rating unchanged"
    )



def _fen_key(fen: str) -> str:
    """First 4 FEN fields: the normalization the endgame routes use."""
    return " ".join(fen.split()[:4])


def post_hint(client, headers, position_id, fen):
    return client.post(
        "/api/endgames/hint",
        headers=headers,
        json={"position_id": str(position_id), "fen": fen},
    )


def replay_line_with_hints(client, headers, position, hints_used, retry=False):
    """Replay the stored mate line through the rated endpoint with a hint
    count attached, returning the resolving response."""
    board = chess.Board(position["fen"])
    user_color = board.turn
    final_body = None
    for uci in position["moves"]:
        move = chess.Move.from_uci(uci)
        if board.turn != user_color:
            board.push(move)  # opponent reply, not graded
            continue
        fen_before = board.fen()
        board.push(move)
        response = client.post(
            "/api/endgames/move",
            headers=headers,
            json={
                "position_id": position["id"],
                "fen_before": fen_before,
                "move": uci,
                "fen_after": board.fen(),
                "hints_used": hints_used,
                "retry": retry,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["hints_used"] == hints_used, body
        if board.is_checkmate():
            final_body = body
            break
        assert body["status"] == "in_progress", body
        assert body["rating"] is None, "in_progress must not write the rating"
    assert final_body is not None, "stored mate line never resolved"
    return final_body


def test_hint_and_neutral_solve(client, headers):
    print("F. 'Get solution' hint + hint-assisted solves are NEUTRAL:")
    cards_before = count_review_cards()
    position = fetch_position(
        client,
        headers,
        qualifier=_mate_line_qualifier,
        max_attempts=MAX_GET_ATTEMPTS_SOLVED,
    )
    board = chess.Board(position["fen"])
    rating_before = read_rating()
    print(
        f"  served sourced drill: {position['topic_name']} | "
        f"rating={position['rating']} | "
        f"stored line={len(position['moves'])} plies"
    )

    # --- route validation -------------------------------------------------
    no_clerk = {"X-Internal-Secret": headers["X-Internal-Secret"]}
    response = post_hint(client, no_clerk, position["id"], position["fen"])
    assert response.status_code == 400, response.text
    response = post_hint(client, headers, uuid4(), position["fen"])
    assert response.status_code == 404, response.text
    response = post_hint(client, headers, position["id"], "not-a-fen")
    assert response.status_code == 400, response.text

    # Defender to move (the user's first stored move, played locally): a
    # hint there would answer for the defender, so it must be rejected.
    after_first = board.copy(stack=False)
    after_first.push(chess.Move.from_uci(position["moves"][0]))
    response = post_hint(client, headers, position["id"], after_first.fen())
    assert response.status_code == 409, response.text
    assert "not your turn" in response.json()["detail"]

    # A negative count is a client bug, rejected before anything else runs.
    response = client.post(
        "/api/endgames/move",
        headers=headers,
        json={
            "position_id": position["id"],
            "fen_before": position["fen"],
            "move": position["moves"][0],
            "fen_after": after_first.fen(),
            "hints_used": -1,
        },
    )
    assert response.status_code == 400, response.text
    assert "hints_used" in response.json()["detail"]

    # --- the hint itself --------------------------------------------------
    response = post_hint(client, headers, position["id"], position["fen"])
    assert response.status_code == 200, response.text
    hint = response.json()
    assert hint["position_id"] == position["id"], hint
    assert hint["source"] == "tablebase", hint  # <=5 men: local files answer
    hint_move = chess.Move.from_uci(hint["move_uci"])
    assert hint_move in board.legal_moves, hint
    derived = board.copy(stack=False)
    derived.push(hint_move)
    assert _fen_key(derived.fen()) == _fen_key(hint["fen_after"]), hint
    if derived.is_checkmate():
        user_outcome = "win"
    elif derived.is_stalemate() or derived.is_insufficient_material():
        user_outcome = "draw"
    else:
        user_outcome = _INVERT[probe_tablebase(derived.fen()).outcome]
    assert user_outcome == "win", (hint, user_outcome)
    again = post_hint(client, headers, position["id"], position["fen"]).json()
    assert (
        again["move_uci"],
        again["move_san"],
        again["fen_after"],
    ) == (hint["move_uci"], hint["move_san"], hint["fen_after"]), (again, hint)
    assert read_rating() == rating_before, "hint route moved the rating"
    assert count_review_cards() == cards_before, "hint route queued a card"
    print(
        f"  hint {hint['move_san']} ({hint['move_uci']}, {hint['source']}) | "
        "legal, win-preserving, deterministic | 400/404/409 on bad input | "
        "rating + queue untouched"
    )

    # --- the hinted move grades through the rated endpoint -----------------
    response = client.post(
        "/api/endgames/move",
        headers=headers,
        json={
            "position_id": position["id"],
            "fen_before": position["fen"],
            "move": hint["move_uci"],
            "fen_after": hint["fen_after"],
            "hints_used": 1,
        },
    )
    assert response.status_code == 200, response.text
    hinted = response.json()
    assert hinted["hints_used"] == 1, hinted
    assert hinted["status"] in ("in_progress", "solved"), hinted
    if hinted["status"] == "solved":
        # The hint happened to be mate-in-1: the neutral rule already applies.
        assert hinted["rating"]["change"] == 0, hinted
        assert read_rating() == rating_before
    else:
        assert hinted["rating"] is None, hinted
        assert hinted["outcome_before"] == "win", hinted
        assert hinted["outcome_after"] == "win", hinted
    assert read_rating() == rating_before, "a hinted move moved the rating"
    print("  the hinted move grades cleanly (nothing written on the way)")

    # --- hint-assisted SOLVE: no rating write, either direction ------------
    final = replay_line_with_hints(client, headers, position, hints_used=2)
    assert final["status"] == "solved" and final["resolution"] == "checkmate", final
    rating = final["rating"]
    assert rating is not None, "a rated user should still see a rating block"
    assert rating["change"] == 0, rating
    assert rating["old_rating"] == rating_before, rating
    assert rating["new_rating"] == rating_before, rating
    assert read_rating() == rating_before, "hint-assisted solve changed the DB rating"
    print(
        f"  hint-assisted solve ({final['hints_used']} hints) -> "
        f"solved(checkmate) | rating {rating['old_rating']} -> "
        f"{rating['new_rating']} ({rating['change']:+d}) | DB untouched"
    )

    # --- an UNRATED user is not established by a hinted solve --------------
    write_rating(None)
    try:
        unrated = replay_line_with_hints(client, headers, position, hints_used=1)
        assert unrated["status"] == "solved", unrated
        assert unrated["rating"] is None, unrated
        assert read_rating() is None, "hinted solve established a first rating"
    finally:
        write_rating(rating_before)
    print(
        "  unrated user stays unrated after a hinted solve "
        "(no first-rating write)"
    )


def test_retry_is_unrated(client, headers):
    print("G. 'Retry' replays are UNRATED (verdict yes, writes no):")
    cards_before = count_review_cards()
    rating_before = read_rating()

    # --- a retried FAILURE writes nothing ---------------------------------
    position = fetch_position(
        client,
        headers,
        qualifier=_any_locally_runnable,
        max_attempts=MAX_GET_ATTEMPTS_FAILED,
    )
    board = chess.Board(position["fen"])
    throw, user_outcome = find_throwing_move(board)
    assert throw is not None, "served win drill has no throwing move?"
    fen_before = board.fen()
    board.push(throw)
    response = client.post(
        "/api/endgames/move",
        headers=headers,
        json={
            "position_id": position["id"],
            "fen_before": fen_before,
            "move": throw.uci(),
            "fen_after": board.fen(),
            "retry": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed", body
    assert body["review_capture"] is None, body
    rating = body["rating"]
    assert rating is not None, "a rated user should still see a rating block"
    assert rating["change"] == 0, rating
    assert rating["old_rating"] == rating_before, rating
    assert rating["new_rating"] == rating_before, rating
    assert read_rating() == rating_before, "a retried failure moved the rating"
    assert count_review_cards() == cards_before, "a retried failure queued a card"
    print(
        f"  retried failure ({user_outcome}) -> verdict kept | rating "
        f"{rating['old_rating']} -> {rating['new_rating']} "
        f"({rating['change']:+d}) | no capture, DB untouched"
    )

    # --- a retried SOLVE writes nothing -----------------------------------
    solved_position = fetch_position(
        client,
        headers,
        qualifier=_mate_line_qualifier,
        max_attempts=MAX_GET_ATTEMPTS_SOLVED,
    )
    final = replay_line_with_hints(
        client, headers, solved_position, hints_used=0, retry=True
    )
    assert final["status"] == "solved", final
    assert final["resolution"] == "checkmate", final
    rating = final["rating"]
    assert rating is not None, "a rated user should still see a rating block"
    assert rating["change"] == 0, rating
    assert rating["old_rating"] == rating_before, rating
    assert rating["new_rating"] == rating_before, rating
    assert read_rating() == rating_before, "a retried solve moved the rating"
    assert count_review_cards() == cards_before, "a retried solve queued a card"
    print(
        f"  retried solve (checkmate) -> verdict kept | rating "
        f"{rating['old_rating']} -> {rating['new_rating']} "
        f"({rating['change']:+d}) | DB untouched"
    )


def main():
    client, headers = setup()
    try:
        test_auth(client, headers)
        test_rating_fallback(client, headers)
        solved_position = test_solved(client, headers)
        failed_position, lost_fen = test_failed(client, headers)
        test_reply_to_resolution(client, headers)
        test_integrity(
            client, headers, solved_position, failed_position, lost_fen
        )
        test_hint_and_neutral_solve(client, headers)
        test_retry_is_unrated(client, headers)
    finally:
        teardown()
    print("all Endgame Trainer HTTP flow checks passed (test user removed)")


if __name__ == "__main__":
    main()
