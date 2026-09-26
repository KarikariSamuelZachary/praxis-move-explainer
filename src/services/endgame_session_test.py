"""
Verification harness for the Endgame Trainer session state machine
(src/services/endgame_session.py).

Every user move below is graded through evaluate_endgame_move() exactly as
the (future) route will grade it. Defender/opponent replies in between are
tablebase-optimal (the stateless design means only USER moves are graded;
the trainer's defender-reply generator is a later step).

Sequences:

  A. Winning bridge line, canonical b-file Lucena (win drill, WTM). The
     line comes from the tablebase service's /mainline endpoint (one call),
     so every reply is the defender's longest resistance and the line runs
     through to ACTUAL CHECKMATE. Graded call-by-call: in_progress (win
     preserved) until the final mating move -> solved(checkmate).

  B. Deliberate throw-away on the same position: one user move that turns
     the tablebase win into a tablebase draw -> failed(threw_away_win) on
     that exact call (mid-game, no resolution).

  C. Draw drill held to the end: e-file draw position, user (Black) holds
     with non-zeroing moves; the fifty-move clock expires on the defender's
     reply and arrives as the next fen_before -> solved(fifty_move_rule).

  D. Draw drill, defender blunders: same position, user holds briefly then
     plays a tablebase-losing move -> failed(lost_the_draw).

  E. Degraded promotion: with the external fallback simulated unreachable,
     the promotion move resolves the drill as solved(promotion) instead of
     erroring mid-session.

  F. Client-integrity contract: illegal move, fabricated fen_after, and a
     stale lost fen_before -> ValueError, never a graded verdict.

  G. Rating fields: in_progress never carries a rating change; solved/failed
     reuse core.rating.calculate_rating_change() verbatim.

  H. Common-mistake content: the FAILED results from B/D -- plus locally
     probed fallback moves so all three authored failure types are always
     exercised, even if the external move list offers no throw/blunder this
     run -- are enriched via attach_common_mistake(), exactly the call the
     route will make. Each enriched result must carry the seeded
     endgame_common_mistakes row for its (Lucena, failure type). Degradation
     is checked too: ran_out_of_moves (reachable but unauthored), a topic
     with no content at all, a non-failed result, and a non-UUID topic id.

  I. Threefold repetition + defender-reply endings: shuffling a rook ending
     through a start_fen + history replay produces a draw the FEN alone
     cannot see; the claim is auto-adjudicated (failed ran_out_of_moves for
     a win drill, solved for a draw drill) and bad history is rejected as a
     client-integrity error. Also covers evaluate_defender_reply() catching
     a game the defender's own move ended.

Run with: cd src && ../venv/bin/python services/endgame_session_test.py
(sequence H additionally needs the DB config from src/.env: DB_* vars, or
a real DATABASE_URL; content must be seeded via seed_endgames.py)
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import psycopg2
from dotenv import load_dotenv

from core.rating import calculate_rating_change
from services.endgame_session import (
    EndgameFailureCategory,
    EndgameMoveResult,
    EndgameResolution,
    EndgameStatus,
    _terminal_verdict,
    attach_common_mistake,
    board_from_history,
    evaluate_defender_reply,
    evaluate_endgame_move,
)
from services.tablebase import probe_tablebase

load_dotenv()

# Seeded drill positions (endgame_positions rows 1 and 5; pinned so the
# harness runs standalone too):
B_FILE_LUCENA_W = "1K1k4/1P6/8/8/8/8/r7/5R2 w - - 0 1"   # win drill, WTM
E_FILE_DRAW_B = "4K3/4P1k1/8/8/8/8/r7/5R2 b - - 0 1"     # draw drill, BTM

TOPIC_DIFFICULTY = 1450  # seeded Lucena difficulty_rating
TRAINER_RATING = 1450    # matches the topic for a clean +5/-5

_INVERT = {"win": "loss", "draw": "draw", "loss": "win"}
_MAINLINE = os.getenv(
    "SYZYGY_TABLEBASE_API_BASE", "https://tablebase.lichess.ovh/standard"
) + "/mainline"


def _db_config():
    # Mirrors the sibling harnesses (tablebase_test / endgame_library_test):
    # discrete DB_* vars first, DATABASE_URL fallback.
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
            "sequence H needs a DB: set DB_NAME/DB_USER/DB_PASSWORD (src/.env) "
            "or a real DATABASE_URL"
        )
    return config


def _lucena_topic_id(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM endgame_topics WHERE name = %s", ("Lucena Position",))
        row = cur.fetchone()
    assert row is not None, "Lucena Position topic missing - run src/seed_endgames.py"
    return str(row[0])


def _transition_move(board: chess.Board, wanted: str) -> chess.Move | None:
    """First legal non-resolving move after which the OPPONENT's tablebase
    verdict is `wanted`: "draw" = the user threw the win away, "win" = the
    user's move loses. Probed locally, so sequence H does not depend on the
    external move list. Promotions are skipped on purpose: they leave local
    coverage (6 men), and H's degradations stay fully offline."""
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        if move.promotion is not None:
            continue
        child = board.copy(stack=False)
        child.push(move)
        if child.is_stalemate() or child.is_checkmate():
            continue
        if probe_tablebase(child.fen()).outcome == wanted:
            return move
    return None


def hold_draw_move(board: chess.Board) -> chess.Move:
    """A non-zeroing move that keeps the mover's tablebase outcome a draw
    (neither side may resolve or reset the clock)."""
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        if board.is_zeroing(move):
            continue
        child = board.copy(stack=False)
        child.push(move)
        result = probe_tablebase(child.fen())
        if _INVERT[result.outcome] != "draw":
            continue
        if child.is_stalemate() or child.is_checkmate():
            continue
        return move
    raise AssertionError(f"no non-zeroing drawing move in {board.fen()!r}")


def _mainline(fen: str) -> list[dict]:
    """The tablebase's own DTZ-optimal continuation, one external call."""
    url = f"{_MAINLINE}?fen={urllib.parse.quote(fen)}"
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                payload = json.load(response)
            return payload["mainline"]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3.0 * attempt)
    raise AssertionError("unreachable")


def _moves_list(fen: str) -> list[dict]:
    q = urllib.parse.urlencode({"fen": fen})
    url = os.getenv(
        "SYZYGY_TABLEBASE_API_BASE", "https://tablebase.lichess.ovh/standard"
    )
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=10) as response:
                return json.load(response)["moves"]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3.0 * attempt)
    raise AssertionError("unreachable")


def grade(board: chess.Board, move: chess.Move, drill_is_winning: bool, tag: str):
    """One graded user move, exactly as the trainer would call it."""
    before = board.fen()
    child = board.copy(stack=False)
    child.push(move)
    result = evaluate_endgame_move(
        before,
        move.uci(),
        child.fen(),
        drill_is_winning=drill_is_winning,
        endgame_trainer_rating=TRAINER_RATING,
        topic_difficulty_rating=TOPIC_DIFFICULTY,
    )
    print(
        f"  [{tag:8s}] {chess.Board(before).san(move):7s} | {result.status.value:11s} | "
        f"fail={result.failure_category.value if result.failure_category else '-':17s} | "
        f"res={result.resolution.value if result.resolution else '-':11s} | "
        f"{result.outcome_before}->{result.outcome_after} | "
        f"dtz {result.dtz_before}->{result.dtz_after} | "
        f"rating={result.rating_change}"
    )
    return result, child


def sequence_a_bridge_to_mate():
    print("A. correct bridge line, b-file Lucena (win drill, WTM), via tablebase mainline:")
    moves = _mainline(B_FILE_LUCENA_W)
    assert moves, "expected a non-empty DTZ mainline for a winning position"
    board = chess.Board(B_FILE_LUCENA_W)
    ply = 0
    for entry in moves:
        move = chess.Move.from_uci(entry["uci"])
        if board.turn == chess.WHITE:
            ply += 1
            result, child = grade(board, move, True, f"user#{ply}")
            assert result.status in (
                EndgameStatus.IN_PROGRESS,
                EndgameStatus.SOLVED,
            ), f"win line must never fail: {result}"
            if result.status == EndgameStatus.SOLVED:
                assert result.resolution is not None
                assert result.rating_change == calculate_rating_change(
                    TRAINER_RATING, TOPIC_DIFFICULTY, True
                )
                return result
            assert result.rating_change is None
            assert result.outcome_before == "win" and result.outcome_after == "win"
            board = child
        else:
            board.push(move)  # defender's reply: not graded, just continued
    raise AssertionError("mainline ended without a mating move")


def sequence_b_throw_away():
    print("B. deliberate throw-away (same b-file position, win drill):")
    moves = _moves_list(B_FILE_LUCENA_W)
    throws = [m for m in moves if m["category"] == "draw"]
    blunders = [m for m in moves if m["category"] == "win"]
    print(
        f"  scan: {len(throws)} throw-away moves, {len(blunders)} blunder moves"
    )
    board = chess.Board(B_FILE_LUCENA_W)
    chosen = chess.Move.from_uci((throws or blunders)[0]["uci"])
    result, _child = grade(board, chosen, True, "throw")
    assert result.status == EndgameStatus.FAILED
    expected = (
        EndgameFailureCategory.THREW_AWAY_WIN if throws
        else EndgameFailureCategory.BLUNDERED_INTO_LOSS
    )
    assert result.failure_category == expected, (result.failure_category, expected)
    assert result.resolution is None  # failed mid-game, not on a resolution
    assert result.rating_change == calculate_rating_change(
        TRAINER_RATING, TOPIC_DIFFICULTY, False
    )
    throw_result = result
    blunder_result = None
    if blunders:
        result, _child = grade(board, chess.Move.from_uci(blunders[0]["uci"]), True, "blunder")
        assert result.status == EndgameStatus.FAILED
        assert result.failure_category == EndgameFailureCategory.BLUNDERED_INTO_LOSS
        assert result.rating_change == calculate_rating_change(
            TRAINER_RATING, TOPIC_DIFFICULTY, False
        )
        blunder_result = result
    return throw_result, blunder_result


def sequence_c_hold_the_draw():
    print("C. draw drill held to the fifty-move rule (e-file draw, BTM):")
    board = chess.Board(E_FILE_DRAW_B)
    ply = 0
    while True:
        ply += 1
        assert ply <= 60, "hold line must not run past the clock"
        if board.is_fifty_moves():
            result, _child = grade(board, hold_draw_move(board), False, f"user#{ply}")
            break
        result, child = grade(board, hold_draw_move(board), False, f"user#{ply}")
        assert result.status == EndgameStatus.IN_PROGRESS, (
            f"hold line must never fail while holding: {result}"
        )
        assert result.rating_change is None
        board = child
        board.push(hold_draw_move(board))
    assert result.status == EndgameStatus.SOLVED
    assert result.resolution is not None
    assert result.rating_change == calculate_rating_change(
        TRAINER_RATING, TOPIC_DIFFICULTY, True
    )
    return result


def sequence_d_defender_blunder():
    print("D. draw drill, defender blunders (e-file draw, BTM):")
    board = chess.Board(E_FILE_DRAW_B)
    blunder = None
    for entry in _moves_list(E_FILE_DRAW_B):
        if entry["category"] == "win":  # white (to move after it) wins -> lost
            blunder = chess.Move.from_uci(entry["uci"])
            break
    assert blunder is not None, "a drawn position must still contain losing defenses?"
    result, _child = grade(board, blunder, False, "blunder")
    assert result.status == EndgameStatus.FAILED
    assert result.failure_category == EndgameFailureCategory.LOST_THE_DRAW
    assert result.rating_change == calculate_rating_change(
        TRAINER_RATING, TOPIC_DIFFICULTY, False
    )
    return result


def sequence_e_degraded_promotion():
    print("E. degraded promotion (external fallback down at the promotion):")
    import services.tablebase as tablebase_module

    # Walk the mainline with the fallback fully active; when the user's move
    # is the promotion, the external API is simulated unreachable FOR THE
    # POST-PROMOTION POSITION ONLY. Local probing is also forced unavailable
    # for that one position so this sequence exercises the degraded path
    # regardless of which Syzygy files are installed (the deployed image has
    # the full 3-4-5 set, which DOES cover KQRvKR). The state machine must
    # resolve the drill cleanly -- a promotion with the win intact completes
    # the drill -- instead of surfacing an ungradeable error mid-session.
    moves = _mainline(B_FILE_LUCENA_W)
    board = chess.Board(B_FILE_LUCENA_W)
    ply = 0
    original = tablebase_module._fetch_lichess
    original_local = tablebase_module._probe_local
    # Sequence A already cached the post-promotion position in this process;
    # a cache hit would silently mask the simulated outage.
    tablebase_module._fallback_cache.clear()

    promotion_child_key = None
    outage_attempted = {"value": False}

    def failing_fetch(fen):
        key = " ".join(fen.split()[:4])
        if promotion_child_key is not None and key == promotion_child_key:
            outage_attempted["value"] = True
            raise tablebase_module.TablebaseUnavailableError("simulated: network down")
        return original(fen)

    def failing_local(local_board):
        key = " ".join(local_board.fen().split()[:4])
        if promotion_child_key is not None and key == promotion_child_key:
            raise tablebase_module.TablebaseUnavailableError(
                "simulated: local files do not cover the post-promotion position"
            )
        return original_local(local_board)

    tablebase_module._fetch_lichess = failing_fetch
    tablebase_module._probe_local = failing_local
    try:
        for entry in moves:
            move = chess.Move.from_uci(entry["uci"])
            if board.turn == chess.WHITE:
                ply += 1
                if move.promotion is not None:
                    promotion_child = board.copy(stack=False)
                    promotion_child.push(move)
                    promotion_child_key = " ".join(
                        promotion_child.fen().split()[:4]
                    )
                result, child = grade(board, move, True, f"user#{ply}")
                if move.promotion is not None:
                    assert outage_attempted["value"], (
                        "simulated outage was never exercised"
                    )
                    assert result.status == EndgameStatus.SOLVED
                    assert result.resolution.value == "promotion"
                    assert result.rating_change == calculate_rating_change(
                        TRAINER_RATING, TOPIC_DIFFICULTY, True
                    )
                    return result
                assert result.status == EndgameStatus.IN_PROGRESS, f"{result}"
                board = child
            else:
                board.push(move)
    finally:
        tablebase_module._fetch_lichess = original
        tablebase_module._probe_local = original_local
    raise AssertionError("mainline walk did not reach a promotion move")


def sequence_f_contract():
    print("F. client-integrity contract:")
    board = chess.Board(B_FILE_LUCENA_W)
    before = board.fen()
    # 1. Fabricated fen_after: a LEGAL move whose claimed result position
    #    differs from what the move actually produces (rook on g1 vs d1).
    try:
        evaluate_endgame_move(
            before, "f1d1", "1K1k4/1P6/8/8/8/8/r7/6R1 b - - 1 1"
        )
        raise AssertionError("fabricated fen_after must raise ValueError")
    except ValueError as exc:
        assert "does not match" in str(exc), exc
    # 2. Well-formed UCI that is not legal in fen_before.
    try:
        evaluate_endgame_move(before, "a1a2", before)
        raise AssertionError("illegal move must raise ValueError")
    except ValueError as exc:
        assert "illegal move" in str(exc), exc
    # 3. A draw drill receiving a fen_before that is already lost (the state
    #    a buggy client would send after a lost-the-draw blunder).
    draw_board = chess.Board(E_FILE_DRAW_B)
    blunder = None
    for entry in _moves_list(E_FILE_DRAW_B):
        if entry["category"] == "win":
            blunder = chess.Move.from_uci(entry["uci"])
            break
    assert blunder is not None
    lost_child = draw_board.copy(stack=False)
    lost_child.push(blunder)
    # White's reply must KEEP white winning (probe each candidate locally).
    white_keep = None
    for candidate in sorted(lost_child.legal_moves, key=lambda m: m.uci()):
        nxt = lost_child.copy(stack=False)
        nxt.push(candidate)
        if probe_tablebase(nxt.fen()).outcome == "loss":  # black (to move) lost
            white_keep = candidate
            break
    assert white_keep is not None, "no white move keeps the win?"
    lost_child.push(white_keep)
    after_lost = lost_child.copy(stack=False)
    some_move = next(iter(after_lost.legal_moves))
    moved = after_lost.copy(stack=False)
    moved.push(some_move)
    try:
        evaluate_endgame_move(
            after_lost.fen(), some_move.uci(), moved.fen(), drill_is_winning=False
        )
        raise AssertionError("stale lost fen_before must raise ValueError")
    except ValueError as exc:
        assert "must already have failed" in str(exc), exc
    print("  fabricated fen_after / illegal move / stale lost fen_before -> ValueError")


def sequence_h_common_mistake_content(throw_result, blunder_result, draw_blunder_result):
    print("H. common-mistake content on FAILED results (grader stays pure):")
    conn = psycopg2.connect(**_db_config())
    try:
        topic_id = _lucena_topic_id(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT failure_type, explanation
                FROM endgame_common_mistakes
                WHERE topic_id = %s
                """,
                (topic_id,),
            )
            seeded = dict(cur.fetchall())
        assert set(seeded) == {
            "threw_away_win",
            "blundered_into_loss",
            "lost_the_draw",
        }, f"expected the three authored Lucena types, got {sorted(seeded)}"

        # Reuse the FAILED results the sequences above already produced; the
        # external move list may not have offered a throw/blunder this run,
        # so probe locally for any missing type (same failure, sourced from
        # the harness instead of the API).
        failures = {throw_result.failure_category: throw_result}
        if blunder_result is not None:
            failures[blunder_result.failure_category] = blunder_result
        failures[draw_blunder_result.failure_category] = draw_blunder_result

        if EndgameFailureCategory.THREW_AWAY_WIN not in failures:
            move = _transition_move(chess.Board(B_FILE_LUCENA_W), "draw")
            assert move is not None, "a winning position must contain a throwing move"
            result, _child = grade(chess.Board(B_FILE_LUCENA_W), move, True, "throw")
            assert result.failure_category == EndgameFailureCategory.THREW_AWAY_WIN
            failures[result.failure_category] = result
        if EndgameFailureCategory.BLUNDERED_INTO_LOSS not in failures:
            move = _transition_move(chess.Board(B_FILE_LUCENA_W), "win")
            assert move is not None, "a winning position must contain a losing move"
            result, _child = grade(chess.Board(B_FILE_LUCENA_W), move, True, "blunder")
            assert result.failure_category == EndgameFailureCategory.BLUNDERED_INTO_LOSS
            failures[result.failure_category] = result

        for category, failed in failures.items():
            assert failed.status == EndgameStatus.FAILED
            assert failed.common_mistake is None, "the grader must never touch content"
            enriched = attach_common_mistake(conn, failed, topic_id)
            expected = seeded[category.value]
            assert enriched.common_mistake == expected, category
            assert enriched.failure_category == category
            print(
                f"  {category.value:18s} -> {len(expected):4d}-char explanation "
                "matches the seeded row"
            )

        # Content absence degrades, never errors:
        # (a) a reachable-but-unauthored failure type on a real topic;
        # (b) a failed result from a topic with no content at all;
        # (c) a non-failed result (no lookup happens at all).
        synthetic = EndgameMoveResult(
            status=EndgameStatus.FAILED,
            failure_category=EndgameFailureCategory.RAN_OUT_OF_MOVES,
        )
        assert attach_common_mistake(conn, synthetic, topic_id).common_mistake is None
        unseeded_topic = str(uuid.uuid4())
        any_throw = failures[EndgameFailureCategory.THREW_AWAY_WIN]
        unchanged = attach_common_mistake(conn, any_throw, unseeded_topic)
        assert unchanged.common_mistake is None
        assert unchanged == any_throw
        in_progress = EndgameMoveResult(status=EndgameStatus.IN_PROGRESS)
        assert attach_common_mistake(conn, in_progress, topic_id).common_mistake is None
        print("  ran_out_of_moves / unseeded topic / non-failed -> None, no error")

        try:
            attach_common_mistake(conn, any_throw, "not-a-uuid")
            raise AssertionError("non-UUID topic_id must raise ValueError")
        except ValueError as exc:
            assert "not a valid UUID" in str(exc), exc
        print("  non-UUID topic_id -> ValueError")
    finally:
        conn.close()


def sequence_i_repetition_and_history():
    print("I. threefold repetition via start_fen + history:")
    # A quiet rook ending shuffled through two full cycles: the start
    # position occurs three times, so the third occurrence is a draw.
    start = "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
    cycle = ["a1a2", "e8d8", "a2a1", "d8e8"]
    history = cycle * 2
    board = chess.Board(start)
    for uci in history:
        board.push(chess.Move.from_uci(uci))
    fen_before = board.fen()

    # The FEN alone cannot express repetition; the replay can.
    assert not chess.Board(fen_before).is_repetition(3)
    rebuilt = board_from_history(start, history, fen_before)
    assert rebuilt.is_repetition(3)
    assert _terminal_verdict(rebuilt, True) == (
        EndgameStatus.FAILED,
        EndgameFailureCategory.RAN_OUT_OF_MOVES,
        EndgameResolution.THREEFOLD_REPETITION,
    )
    assert _terminal_verdict(rebuilt, False) == (
        EndgameStatus.SOLVED,
        None,
        EndgameResolution.THREEFOLD_REPETITION,
    )

    # End to end through the grader: the claim is adjudicated before any
    # transition classification (the win drill fails on the repetition).
    user_move = "a1a2"
    next_board = chess.Board(fen_before)
    next_board.push(chess.Move.from_uci(user_move))
    graded = evaluate_endgame_move(
        fen_before,
        user_move,
        next_board.fen(),
        drill_is_winning=True,
        start_fen=start,
        history=history,
    )
    assert graded.status == EndgameStatus.FAILED, graded
    assert graded.resolution == EndgameResolution.THREEFOLD_REPETITION, graded
    assert graded.failure_category == EndgameFailureCategory.RAN_OUT_OF_MOVES, graded

    # Untrusted history: short replay, illegal move, malformed move, and
    # history without a start position are all client-integrity errors.
    for bad_history, phrase in [
        # One ply only: a different position from fen_before (the short
        # cycle alone replays to the SAME position, counters ignored).
        (["a1a2"], "does not lead"),
        (["e1e2", "e8d8", "a2a1", "d8e8"], "illegal history move"),
        (["nonsense"], "malformed history move"),
    ]:
        try:
            board_from_history(start, bad_history, fen_before)
            raise AssertionError(f"{bad_history} must raise ValueError")
        except ValueError as exc:
            assert phrase in str(exc), exc
    try:
        evaluate_endgame_move(
            fen_before,
            user_move,
            next_board.fen(),
            drill_is_winning=True,
            history=history,
        )
        raise AssertionError("history without start_fen must raise ValueError")
    except ValueError as exc:
        assert "without the drill's start position" in str(exc), exc
    print("  replay adjudicates threefold; bad history -> ValueError")

    # Defender-reply endings: a defender move that is itself terminal.
    stalemate_fen = "7k/5Q2/7K/8/8/8/8/8 b - - 0 1"
    win_reply = evaluate_defender_reply(stalemate_fen, drill_is_winning=True)
    draw_reply = evaluate_defender_reply(stalemate_fen, drill_is_winning=False)
    assert win_reply is not None
    assert win_reply.status == EndgameStatus.FAILED
    assert win_reply.resolution == EndgameResolution.STALEMATE
    assert draw_reply is not None
    assert draw_reply.status == EndgameStatus.SOLVED
    assert draw_reply.resolution == EndgameResolution.STALEMATE
    assert evaluate_defender_reply(
        "4k3/8/8/8/8/8/8/R3K3 b - - 3 2", drill_is_winning=True
    ) is None
    try:
        evaluate_defender_reply("not a fen", drill_is_winning=True)
        raise AssertionError("malformed defender reply FEN must raise ValueError")
    except ValueError as exc:
        assert "malformed FEN (defender reply)" in str(exc), exc
    print("  defender-reply terminal verdicts (stalemate/live/malformed)")


def main():
    sequence_a_bridge_to_mate()
    throw_result, blunder_result = sequence_b_throw_away()
    sequence_c_hold_the_draw()
    draw_blunder_result = sequence_d_defender_blunder()
    sequence_e_degraded_promotion()
    sequence_f_contract()
    sequence_i_repetition_and_history()
    sequence_h_common_mistake_content(throw_result, blunder_result, draw_blunder_result)
    print("all sequences judged call-by-call; the bridge line reached actual checkmate")


if __name__ == "__main__":
    main()
