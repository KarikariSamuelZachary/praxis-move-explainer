"""
Verification harness for services/endgame_reply.py (opponent reply
generator) -- standalone, before the /api/endgames/move wiring.

Sequences:

  A. Tablebase policy (local Syzygy, no network needed):
     A1. Losing defender: the chosen move must preserve the loss AND
         maximize the child's DTZ (longest resistance) across every legal
         move -- recomputed independently here.
     A2. Drawn position: the chosen move must keep the tablebase draw.
     A3. Winning side with mate in 1: the chosen move must deliver mate.
     A4. Winning side without an immediate mate, root DTZ > 1: the chosen
         move must preserve the win and minimize |child DTZ| (the child
         closest to the phase's zeroing; child DTZ is the loser's, so this
         is the least-negative one).
     A5. Winning side with root DTZ == 1: the optimal move is a ZEROING
         move (capture/pawn push). |child DTZ| is phase-relative and picks
         the wrong move here: a KR vs KR position with the enemy rook en
         prise offers a waiting rook move (child dtz -3) and the immediate
         capture (KR vs K child dtz -18); the capture is correct and the
         small-|dtz| move repeats the game.
     Every case also checks legality, SAN, the derived fen_after, the
     reported root outcome/DTZ and source="tablebase".

  B. Determinism: two calls on the same FEN return the same move.

  C. Terminal positions: checkmate / stalemate / insufficient material all
     raise TerminalPositionError; a malformed FEN raises ValueError.

  D. Stored-line detection against a REAL sourced row: walking the puzzle
     line position by position, stored_line_reply() must return exactly the
     next stored opponent move while the line continues, and None once the
     line is exhausted or the side to move would be the user again.

  E. Stockfish fallback: with the Lichess tablebase API disabled, a
     >7-man position (out of local coverage) must be answered by
     source="stockfish" with a legal move, through the long-lived
     singleton (no fresh process per call).

Run with: cd src && ../venv/bin/python services/endgame_reply_test.py
Requires: data/syzygy/regular (3-4-5) for A/C/D, a Stockfish binary for E,
and (for D only) the DB config from src/.env or the root .env.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import psycopg2
from dotenv import load_dotenv

from services.endgame_reply import (
    TerminalPositionError,
    generate_opponent_reply,
    stored_line_reply,
)
from services.tablebase import TablebaseUnavailableError, probe_tablebase

load_dotenv()

_INVERT = {"win": "loss", "draw": "draw", "loss": "win"}

# Lost defender to move (white rook on d8, black rook d1, kings d6/f5).
LOST_DEFENDER = "3R4/8/3k4/5K2/8/8/8/3r4 b - - 23 72"
# Rook's-pawn anti-Lucena: tablebase draw for the side to move.
DRAW_POSITION = "K1k5/P7/8/8/8/8/1r6/7R w - - 0 1"
# White Qf7/Kf6 vs black Kh8: Qg7# is mate in one.
MATE_IN_ONE = "7k/5Q2/5K2/8/8/8/8/8 w - - 0 1"
# KR vs K, no immediate mate.
KR_VS_K = "8/8/8/4k3/8/8/8/R3K3 w - - 0 1"
# KR vs KR win for the side to move with root DTZ == 1 (enemy rook en
# prise), but also a slower non-zeroing winning move. Taken from the real
# sourced drill that exposed the fivefold-repetition bug.
KRKR_ZEROING_NOW = "8/8/8/8/1k6/2R3r1/2K5/8 b - - 42 71"

TERMINAL_FENS = [
    ("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", "checkmate"),
    ("7k/5Q2/7K/8/8/8/8/8 b - - 0 1", "stalemate"),
    ("8/8/8/8/5k2/8/8/1B2K3 w - - 0 1", "insufficient material"),
]


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
            "sequence D needs a DB: set DB_NAME/DB_USER/DB_PASSWORD (src/.env) "
            "or a real DATABASE_URL"
        )
    return config


def _check_common(reply, fen):
    board = chess.Board(fen)
    move = chess.Move.from_uci(reply.move_uci)
    assert move in board.legal_moves, (reply.move_uci, fen)
    assert reply.move_san == board.san(move), (reply.move_san, board.san(move))
    derived = board.copy(stack=False)
    derived.push(move)
    assert " ".join(reply.fen_after.split()[:4]) == " ".join(
        derived.fen().split()[:4]
    )
    return move, derived


def test_losing_defender():
    print("A1. lost defender -> maximum-DTZ resistance:")
    board = chess.Board(LOST_DEFENDER)
    root = probe_tablebase(LOST_DEFENDER)
    assert root.outcome == "loss", root
    reply = generate_opponent_reply(LOST_DEFENDER)
    assert reply.source == "tablebase"
    assert reply.outcome == root.outcome and reply.dtz == root.dtz
    _check_common(reply, LOST_DEFENDER)

    # Independent re-derivation: every move that keeps the loss, ranked by
    # the winner's DTZ in the child (maximized), tie-broken by UCI order.
    ranked = []
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        child = board.copy(stack=False)
        child.push(move)
        outcome = "win" if child.is_checkmate() else _INVERT[
            probe_tablebase(child.fen()).outcome
        ]
        if outcome != "loss":
            continue
        dtz = probe_tablebase(child.fen()).dtz
        ranked.append((-(dtz or 0), move.uci()))
    ranked.sort()
    expected = ranked[0][1]
    assert reply.move_uci == expected, (reply.move_uci, expected)
    print(
        f"  chose {reply.move_uci} (child dtz {ranked[0][0] * -1}), "
        f"{len(ranked)} loss-preserving moves ranked"
    )


def test_drawn_position():
    print("A2. drawn position -> keep the draw:")
    reply = generate_opponent_reply(DRAW_POSITION)
    assert reply.source == "tablebase"
    assert reply.outcome == "draw" and reply.dtz == 0, reply
    _check_common(reply, DRAW_POSITION)
    # The chosen move must keep the draw from the mover's perspective.
    child = chess.Board(reply.fen_after)
    child_outcome = _INVERT[probe_tablebase(child.fen()).outcome]
    assert child_outcome == "draw", (reply.move_uci, child_outcome)
    print(f"  chose {reply.move_uci} ({reply.move_san}), child still drawn")


def test_winning_side():
    print("A3/A4. winning side -> mate first, else fastest conversion:")
    mate_reply = generate_opponent_reply(MATE_IN_ONE)
    assert mate_reply.source == "tablebase"
    mate_child = chess.Board(mate_reply.fen_after)
    assert mate_child.is_checkmate(), mate_reply
    print(f"  mate in 1: {mate_reply.move_uci}")

    board = chess.Board(KR_VS_K)
    root = probe_tablebase(KR_VS_K)
    assert root.outcome == "win" and root.dtz and root.dtz > 1, root
    reply = generate_opponent_reply(KR_VS_K)
    _check_common(reply, KR_VS_K)

    # Independent ranking (root DTZ > 1): win-preserving moves, mate first,
    # then minimal |child DTZ| (the least-negative loser DTZ, i.e. the
    # child closest to the phase's zeroing), tie-broken by UCI order.
    ranked = []
    for move in sorted(board.legal_moves, key=lambda m: m.uci()):
        child = board.copy(stack=False)
        child.push(move)
        if child.is_checkmate():
            ranked.append((0, 0, move.uci()))
            continue
        child_result = probe_tablebase(child.fen())
        if child_result.outcome != "loss":
            continue
        ranked.append((1, abs(child_result.dtz or 0), move.uci()))
    ranked.sort()
    assert reply.move_uci == ranked[0][2], (reply.move_uci, ranked[0])
    print(
        f"  chosen {reply.move_uci} keeps the win "
        f"(root dtz {root.dtz}, {len(ranked)} win-preserving moves ranked)"
    )

    print("A5. root DTZ == 1 -> zeroing move preferred over small |DTZ|:")
    zeroing_board = chess.Board(KRKR_ZEROING_NOW)
    zeroing_root = probe_tablebase(KRKR_ZEROING_NOW)
    assert zeroing_root.outcome == "win" and zeroing_root.dtz == 1, zeroing_root
    zeroing_reply = generate_opponent_reply(KRKR_ZEROING_NOW)
    _check_common(zeroing_reply, KRKR_ZEROING_NOW)
    chosen = chess.Move.from_uci(zeroing_reply.move_uci)
    assert zeroing_board.is_capture(chosen), zeroing_reply
    # The naive min-|child DTZ| rule would have picked the non-zeroing
    # waiting move (child dtz -3 vs the capture's -18) and repeated.
    naive = None
    for move in sorted(zeroing_board.legal_moves, key=lambda m: m.uci()):
        child = zeroing_board.copy(stack=False)
        child.push(move)
        child_result = probe_tablebase(child.fen())
        if child_result.outcome != "loss":
            continue
        key = abs(child_result.dtz or 0)
        if naive is None or key < naive[0]:
            naive = (key, move.uci())
    assert naive is not None
    assert zeroing_reply.move_uci != naive[1], (
        "min-|dtz| regression: chose the non-zeroing waiting move",
        zeroing_reply.move_uci,
        naive,
    )
    print(
        f"  chose the zeroing capture {zeroing_reply.move_uci} "
        f"(min-|dtz| would have chosen {naive[1]}, child dtz {-naive[0]})"
    )


def test_determinism():
    print("B. determinism:")
    first = generate_opponent_reply(LOST_DEFENDER).move_uci
    second = generate_opponent_reply(LOST_DEFENDER).move_uci
    assert first == second, (first, second)
    print(f"  two calls -> {first}")


def test_terminal():
    print("C. terminal positions:")
    for fen, reason in TERMINAL_FENS:
        try:
            generate_opponent_reply(fen)
            raise AssertionError(f"{fen} must raise TerminalPositionError")
        except TerminalPositionError as exc:
            assert reason in str(exc), (reason, str(exc))
    try:
        generate_opponent_reply("not-a-fen")
        raise AssertionError("malformed FEN must raise ValueError")
    except ValueError as exc:
        assert "malformed FEN" in str(exc)
    print(
        f"  {len(TERMINAL_FENS)} over positions -> TerminalPositionError; "
        "malformed FEN -> ValueError"
    )


def test_stored_line_real_data():
    print("D. stored-line detection on a real sourced row:")
    conn = psycopg2.connect(**_db_config())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.fen, z.moves, t.name
                FROM endgame_positions p
                JOIN endgame_topics t ON t.id = p.topic_id
                JOIN puzzles z ON z.id = p.source_puzzle_id
                WHERE t.name LIKE '%(sourced)'
                  AND p.is_winning = TRUE
                  AND z.moves IS NOT NULL
                ORDER BY random()
                LIMIT 1
                """
            )
            stored_fen, moves_text, topic = cur.fetchone()
    finally:
        conn.close()

    puzzle_moves = moves_text.split()
    line = puzzle_moves[1:]
    assert line, "source puzzle must have at least one drill move"
    print(f"  {topic}: {len(line)} stored drill plies")

    board = chess.Board(stored_fen)
    for index, uci in enumerate(line):
        move = chess.Move.from_uci(uci)
        board.push(move)
        plies_played = index + 1
        expected = None
        if plies_played < len(line) and plies_played % 2 == 1:
            expected = line[plies_played]
        result = stored_line_reply(stored_fen, puzzle_moves, board.fen())
        assert result == expected, (plies_played, result, expected)
    assert stored_line_reply(stored_fen, puzzle_moves, board.fen()) is None

    # A deviation (an alternative legal first user move) is generator
    # territory too.
    alternative = chess.Board(stored_fen)
    deviation = None
    for candidate in sorted(alternative.legal_moves, key=lambda m: m.uci()):
        if candidate.uci() != line[0]:
            deviation = candidate
            break
    assert deviation is not None
    alternative.push(deviation)
    assert stored_line_reply(stored_fen, puzzle_moves, alternative.fen()) is None
    print("  on-line opponent replies returned; exhaustion + deviation -> None")


def test_stockfish_fallback():
    print("E. beyond tablebase reach -> Stockfish singleton:")
    previous = os.environ.get("SYZYGY_TABLEBASE_API_DISABLED")
    os.environ["SYZYGY_TABLEBASE_API_DISABLED"] = "1"
    try:
        try:
            probe_tablebase(chess.STARTING_FEN)
            raise AssertionError("starting position must be beyond tablebase reach")
        except TablebaseUnavailableError:
            pass

        from engines.stockfish_engine import (
            close_endgame_stockfish,
            get_endgame_stockfish,
        )

        try:
            reply = generate_opponent_reply(chess.STARTING_FEN)
            assert reply.source == "stockfish", reply
            assert reply.outcome is None and reply.dtz is None, reply
            _check_common(reply, chess.STARTING_FEN)
            engine_a = get_endgame_stockfish()
            engine_b = get_endgame_stockfish()
            assert engine_a is engine_b, "reply path must reuse the singleton"
            # A second call must not spawn a new process either.
            second = generate_opponent_reply(chess.STARTING_FEN)
            assert second.source == "stockfish"
            print(
                f"  Stockfish reply {reply.move_uci} ({reply.move_san}); "
                "same process reused across calls"
            )
        finally:
            close_endgame_stockfish()
    finally:
        if previous is None:
            os.environ.pop("SYZYGY_TABLEBASE_API_DISABLED", None)
        else:
            os.environ["SYZYGY_TABLEBASE_API_DISABLED"] = previous


def main():
    test_losing_defender()
    test_drawn_position()
    test_winning_side()
    test_determinism()
    test_terminal()
    test_stored_line_real_data()
    test_stockfish_fallback()
    print("all opponent-reply generator checks passed")


if __name__ == "__main__":
    main()
