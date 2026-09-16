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

Run with: cd src && ../venv/bin/python services/endgame_session_test.py
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from dotenv import load_dotenv

from core.rating import calculate_rating_change
from services.endgame_session import (
    EndgameFailureCategory,
    EndgameStatus,
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
    if blunders:
        result, _child = grade(board, chess.Move.from_uci(blunders[0]["uci"]), True, "blunder")
        assert result.status == EndgameStatus.FAILED
        assert result.failure_category == EndgameFailureCategory.BLUNDERED_INTO_LOSS
        assert result.rating_change == calculate_rating_change(
            TRAINER_RATING, TOPIC_DIFFICULTY, False
        )


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
    # POST-PROMOTION POSITION ONLY (the material local files cannot cover).
    # The state machine must resolve the drill cleanly -- a promotion with
    # the win intact completes the drill -- instead of surfacing an
    # ungradeable error mid-session.
    moves = _mainline(B_FILE_LUCENA_W)
    board = chess.Board(B_FILE_LUCENA_W)
    ply = 0
    original = tablebase_module._fetch_lichess
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

    tablebase_module._fetch_lichess = failing_fetch
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


def main():
    sequence_a_bridge_to_mate()
    sequence_b_throw_away()
    sequence_c_hold_the_draw()
    sequence_d_defender_blunder()
    sequence_e_degraded_promotion()
    sequence_f_contract()
    print("all sequences judged call-by-call; the bridge line reached actual checkmate")


if __name__ == "__main__":
    main()
