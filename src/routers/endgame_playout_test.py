"""
HTTP end-to-end verification harness for the "Play it out" continuation
(src/routers/endgame_playout.py).

Everything is driven through the ASGI app with real HTTP semantics, against
the REAL seeded content and the local 3-5-man Syzygy files (offline).

Verified:

  A. Auth + input validation: missing internal secret -> 401; missing Clerk
     id -> 400; unknown position -> 404; malformed fen_after -> 400.
  B. Stored-line quiet: while the position is still on the drill's stored
     line the route returns opponent_reply=None (the client applies the
     stored move itself), exactly like a graded replay.
  C. FAILED drill -> full playout to a real ending. A real win drill is
     thrown away through POST /api/endgames/move first (rating and Woodpecker
     capture written, as always); then the same position is played out
     through POST /api/endgames/playout/reply only. Every defender reply is
     the tablebase generator's; the game reaches a real ending; and after
     the whole continuation the recorded state is unchanged: trainer rating,
     endgame_woodpecker_entries, endgame_woodpecker_attempts and
     woodpecker_entries all hold their post-failure values.
  D. Terminal position: asking about an already-over fen_after returns 200
     with opponent_reply=None -- a normal playout ending, not a 500.
   E. /finish: the fast-forward comes back either as a concrete terminal
      position PLUS the full ordered UCI line that reached it (locally-
      covered material: the client steps that line with no further
      requests, and the harness replays it move-by-move to prove it lands
      exactly on the returned FEN), or as a verdict only (6-7-man material:
      no fen, no line, not steppable by design). Deterministic either way,
      with the rating/queue counts untouched.

Run with: cd src && ../venv/bin/python routers/endgame_playout_test.py
Requires: DATABASE_URL / INTERNAL_SECRET from root .env, the seeded endgame
tables, and data/syzygy/regular (3-4-5).
"""
import os
import sys
import time
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi.testclient import TestClient

# Importing main runs the app's load_dotenv calls, builds the FastAPI app,
# and mounts the routers.
import main as app_module
from core import database
from core.migrations import run_migrations
from services.endgame_reply import terminal_reason
from services.tablebase import TablebaseUnavailableError, probe_tablebase
from routers.endgames_test import (
    _INVERT,
    MAX_DRILL_PLIES,
    _any_locally_runnable,
    _past_line_qualifier,
    fetch_position,
    find_throwing_move,
)

TEST_CLERK_ID = "endgame-playout-http-test"
TEST_EMAIL = "endgame-playout-http-test@example.invalid"
TEST_RATING = 880
MAX_GET_ATTEMPTS_FAILED = 20
MAX_GET_ATTEMPTS_PAST_LINE = 30


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
            cur.execute(
                "DELETE FROM users WHERE clerk_id = %s OR email = %s",
                (TEST_CLERK_ID, TEST_EMAIL),
            )
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)


def _scalar(sql, params=()):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        database.connection_pool.putconn(conn)


def read_rating():
    return _scalar(
        "SELECT endgame_trainer_rating FROM users WHERE clerk_id = %s",
        (TEST_CLERK_ID,),
    )


def count_rows(table):
    return _scalar(
        f"SELECT COUNT(*) FROM {table} WHERE user_id = %s",
        (TEST_CLERK_ID,),
    )


def post_reply(client, headers, position_id, fen_after):
    return client.post(
        "/api/endgames/playout/reply",
        headers=headers,
        json={"position_id": str(position_id), "fen_after": fen_after},
    )


def playout_user_move(board: chess.Board) -> chess.Move:
    """The harness's user move during a playout.

    The user is exploring a settled position, so the move only has to steer
    the continuation to a real ending promptly: an immediate terminal child
    first (mate/stalemate/insufficient material), otherwise the worst
    tablebase outcome -- which hands a winning defender the game and lets it
    convert to mate. Falls back to the first legal move when no child is
    locally probeable.
    """
    best = None
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        child = board.copy(stack=False)
        child.push(move)
        if (
            child.is_checkmate()
            or child.is_stalemate()
            or child.is_insufficient_material()
        ):
            return move
        try:
            child_result = probe_tablebase(child.fen())
        except TablebaseUnavailableError:
            continue
        mover_outcome = _INVERT[child_result.outcome]
        rank = {"loss": 0, "draw": 1, "win": 2}[mover_outcome]
        if best is None or rank < best[0]:
            best = (rank, move)
    if best is not None:
        return best[1]
    return next(iter(board.legal_moves))


def ending_label(board: chess.Board) -> str:
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    if board.is_insufficient_material():
        return "insufficient material"
    if board.is_seventyfive_moves():
        return "seventy-five-move rule"
    if board.is_fivefold_repetition():
        return "fivefold repetition"
    if board.is_fifty_moves():
        return "fifty-move rule"
    return "not over"


def test_auth_and_validation(client, headers, valid_position):
    print("A. auth + validation:")
    missing_secret = client.post(
        "/api/endgames/playout/reply",
        json={
            "position_id": valid_position["id"],
            "fen_after": valid_position["fen"],
        },
    )
    assert missing_secret.status_code == 401, missing_secret.text
    assert missing_secret.json()["detail"] == "Unauthorized"

    no_clerk = {"X-Internal-Secret": headers["X-Internal-Secret"]}
    response = client.post(
        "/api/endgames/playout/reply",
        headers=no_clerk,
        json={
            "position_id": valid_position["id"],
            "fen_after": valid_position["fen"],
        },
    )
    assert response.status_code == 400, response.text
    assert "X-Clerk-User-Id" in response.json()["detail"]

    unknown = post_reply(client, headers, uuid4(), valid_position["fen"])
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"] == "Endgame position not found"

    malformed = post_reply(client, headers, valid_position["id"], "not-a-fen")
    assert malformed.status_code == 400, malformed.text
    print(
        "  missing secret -> 401; missing clerk -> 400; unknown position -> "
        "404; malformed fen -> 400"
    )


def test_stored_line_quiet(client, headers):
    print("B. stored line stays server-quiet:")
    position = fetch_position(
        client,
        headers,
        qualifier=_past_line_qualifier,
        max_attempts=MAX_GET_ATTEMPTS_PAST_LINE,
    )
    board = chess.Board(position["fen"])
    first = chess.Move.from_uci(position["moves"][0])
    assert first in board.legal_moves, (position["moves"], position["fen"])
    board.push(first)

    response = post_reply(client, headers, position["id"], board.fen())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["opponent_reply"] is None, body
    print(
        f"  {position['topic_name']} | first stored move played -> "
        "opponent_reply=None (client owns the line)"
    )
    return position


def test_playout_after_failure(client, headers, valid_position):
    print("C. FAILED drill -> playout to a real ending (nothing changes):")
    position = fetch_position(
        client,
        headers,
        qualifier=_any_locally_runnable,
        max_attempts=MAX_GET_ATTEMPTS_FAILED,
    )
    board = chess.Board(position["fen"])
    user_color = board.turn
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
        },
    )
    assert response.status_code == 200, response.text
    failed = response.json()
    assert failed["status"] == "failed", failed
    assert failed["resolution"] is None, failed
    assert read_rating() != TEST_RATING, "the failed drill must write its rating"

    rating_after_failure = read_rating()
    entries_after_failure = count_rows("endgame_woodpecker_entries")
    attempts_after_failure = count_rows("endgame_woodpecker_attempts")
    puzzle_after_failure = count_rows("woodpecker_entries")
    assert entries_after_failure == 1, "FAILED drill must still capture exactly one card"
    print(
        f"  served {position['topic_name']} | throw={throw.uci()} "
        f"({user_outcome}) -> failed | rating {TEST_RATING} -> "
        f"{rating_after_failure} | capture card +1"
    )

    post_failure_fen = board.fen()

    # The failed move left the DEFENDER to move: the playout starts with a
    # generated reply, not a user move.
    assert board.turn != user_color, board.fen()
    plies = 0
    sources = set()
    user_moves = 0
    while not board.is_game_over():
        assert plies < MAX_DRILL_PLIES, f"playout did not resolve: {board.fen()}"
        reply_body = post_reply(client, headers, position["id"], board.fen())
        assert reply_body.status_code == 200, reply_body.text
        reply = reply_body.json()["opponent_reply"]
        assert reply is not None, reply_body.json()
        sources.add(reply["source"])
        move = chess.Move.from_uci(reply["move_uci"])
        assert move in board.legal_moves, (reply, board.fen())
        board.push(move)
        plies += 1
        if board.is_game_over():
            break
        board.push(playout_user_move(board))
        user_moves += 1
        plies += 1

    assert board.is_game_over(), board.fen()
    assert read_rating() == rating_after_failure, "playout moved the rating"
    assert count_rows("endgame_woodpecker_entries") == entries_after_failure
    assert count_rows("endgame_woodpecker_attempts") == attempts_after_failure
    assert count_rows("woodpecker_entries") == puzzle_after_failure
    print(
        f"  playout: {plies} plies ({user_moves} user) -> "
        f"{ending_label(board)} | defender sources={sorted(sources)} | "
        f"rating {rating_after_failure} and all queue counts unchanged"
    )
    invariants = (
        rating_after_failure,
        entries_after_failure,
        attempts_after_failure,
        puzzle_after_failure,
    )
    return position, post_failure_fen, invariants


def test_terminal_position(client, headers, position):
    print("D. terminal position -> opponent_reply=None:")
    mate_fen = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
    assert chess.Board(mate_fen).is_checkmate(), "harness mate FEN is not mate"

    response = post_reply(client, headers, position["id"], mate_fen)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["opponent_reply"] is None, body
    print("  already-over fen_after -> 200 with opponent_reply=None")



def post_finish(client, headers, position_id, fen):
    return client.post(
        "/api/endgames/playout/finish",
        headers=headers,
        json={"position_id": str(position_id), "fen": fen},
    )


def assert_line_reaches(start_fen, line, plies):
    """The stepper contract: `line` is legal from the request FEN, one move
    per ply, and applying all of it reproduces the returned final position.
    The client replays exactly this with no further requests."""
    assert isinstance(line, list), line
    assert line, "a concrete ending must carry a steppable line"
    assert len(line) == plies, (len(line), plies)
    board = chess.Board(start_fen)
    for uci in line:
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, (uci, board.fen())
        board.push(move)
    return board


def assert_terminal_match(fen, ending):
    """The returned position must really be over, for the stated reason."""
    reason = terminal_reason(fen)
    assert reason is not None, f"finish returned a live position: {fen}"
    expected = {
        "checkmate": "checkmate",
        "stalemate": "stalemate",
        "insufficient material": "insufficient_material",
        "fifty-move rule": "fifty_move_rule",
        "seventy-five-move rule": "seventy_five_move_rule",
        "fivefold repetition": "fivefold_repetition",
    }[reason]
    assert expected == ending, (reason, ending, fen)


def test_finish(client, headers, position, post_failure_fen, invariants):
    print("E. /finish: fast-forward to the final position or the verdict:")
    # Validation first.
    missing = client.post(
        "/api/endgames/playout/finish",
        json={"position_id": position["id"], "fen": post_failure_fen},
    )
    assert missing.status_code == 401, missing.text
    unknown = post_finish(client, headers, uuid4(), post_failure_fen)
    assert unknown.status_code == 404, unknown.text
    malformed = post_finish(client, headers, position["id"], "not-a-fen")
    assert malformed.status_code == 400, malformed.text

    # The post-failure position is locally covered (<=5 men by the drill
    # filter), so a concrete terminal position must come back -- fast.
    t0 = time.monotonic()
    body = post_finish(client, headers, position["id"], post_failure_fen).json()
    elapsed = time.monotonic() - t0
    assert body["fen"], body
    assert body["plies"] > 0, body
    assert body["outcome"] in ("win", "draw", "loss"), body
    assert_terminal_match(body["fen"], body["ending"])
    replayed = assert_line_reaches(post_failure_fen, body["line"], body["plies"])
    assert " ".join(replayed.fen().split()[:4]) == " ".join(
        body["fen"].split()[:4]
    ), (replayed.fen(), body["fen"])
    assert terminal_reason(replayed.fen()) is not None, replayed.fen()

    # Deterministic: the same request fast-forwards the same line.
    again = post_finish(client, headers, position["id"], post_failure_fen).json()
    assert (again["fen"], again["plies"], again["ending"]) == (
        body["fen"],
        body["plies"],
        body["ending"],
    ), (again, body)
    assert again["line"] == body["line"], (again["line"], body["line"])
    print(
        f"  failed drill -> {body['ending']} in {body['plies']} plies "
        f"({elapsed:.2f}s) | outcome={body['outcome']} | line replays to "
        f"the final FEN | deterministic"
    )

    # A known drawn 4-man position: the fast-forward must reach a real draw.
    drawn_fen = "6r1/1k6/8/8/8/8/1K6/R7 w - - 0 1"
    drawn = post_finish(client, headers, position["id"], drawn_fen).json()
    assert drawn["outcome"] == "draw", drawn
    assert drawn["fen"], drawn
    assert drawn["ending"] in (
        "insufficient_material",
        "fivefold_repetition",
        "fifty_move_rule",
        "seventy_five_move_rule",
        "stalemate",
    ), drawn
    assert_terminal_match(drawn["fen"], drawn["ending"])
    drawn_replay = assert_line_reaches(drawn_fen, drawn["line"], drawn["plies"])
    assert " ".join(drawn_replay.fen().split()[:4]) == " ".join(
        drawn["fen"].split()[:4]
    ), (drawn_replay.fen(), drawn["fen"])
    print(
        f"  known drawn position -> {drawn['ending']} in {drawn['plies']} "
        f"plies ({len(drawn['line'])}-move line replays to the final FEN) | "
        f"outcome=draw"
    )

    # Already terminal: the input is the final position, zero plies played.
    mate_fen = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
    mate = post_finish(client, headers, position["id"], mate_fen).json()
    assert mate["ending"] == "checkmate", mate
    assert mate["plies"] == 0, mate
    assert mate["line"] == [], mate
    assert chess.Board(mate["fen"]).is_checkmate(), mate

    # Beyond the local files (6 men): no cheap line, so the verdict is the
    # result. This one pays a single Lichess fallback probe, by design.
    six_man_fen = "8/5k2/2KP4/8/5B2/7p/8/5b2 w - - 7 57"
    verdict = post_finish(client, headers, position["id"], six_man_fen).json()
    assert verdict["fen"] is None and verdict["ending"] is None, verdict
    assert verdict["line"] == [], verdict
    assert verdict["outcome"] in ("win", "draw", "loss"), verdict
    print(
        f"  6-man position -> verdict only (outcome={verdict['outcome']}, "
        "no line, not steppable by design)"
    )

    # None of it wrote anything.
    rating, entries, attempts, puzzles = invariants
    assert read_rating() == rating, "finish moved the rating"
    assert count_rows("endgame_woodpecker_entries") == entries
    assert count_rows("endgame_woodpecker_attempts") == attempts
    assert count_rows("woodpecker_entries") == puzzles
    print("  rating and all queue counts unchanged across every finish call")


def main():
    client, headers = setup()
    try:
        valid_position = fetch_position(
            client,
            headers,
            qualifier=_any_locally_runnable,
            max_attempts=MAX_GET_ATTEMPTS_FAILED,
        )
        test_auth_and_validation(client, headers, valid_position)
        test_stored_line_quiet(client, headers)
        failed_position, post_failure_fen, invariants = test_playout_after_failure(
            client, headers, valid_position
        )
        test_terminal_position(client, headers, failed_position)
        test_finish(
            client, headers, failed_position, post_failure_fen, invariants
        )
    finally:
        teardown()
    print("all Endgame playout checks passed (test user removed)")


if __name__ == "__main__":
    main()
