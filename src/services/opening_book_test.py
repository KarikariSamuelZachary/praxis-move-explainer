"""
Test harness for services/opening_book.py.

Covers the pure pieces (no database needed):
  * position_key — transposition-stable first-4-FEN-fields key.
  * is_book_move — cache-backed lookup (cache injected directly).
  * iter_eco_rows — chess-openings TSV parsing.
  * collect_moves_from_lines — SAN + count accumulation over theory lines.
  * collect_moves_from_games — ply cap + Elo filter.

Run: cd src && ../venv/bin/python services/opening_book_test.py
"""
import os
import sys
import time
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import chess.pgn

import services.opening_book as mod


def _key_after(moves):
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return mod.position_key(board)


def test_position_key_transposition():
    a = _key_after(["e4", "e5", "Nf3", "Nc6"])
    b = _key_after(["Nf3", "Nc6", "e4", "e5"])
    assert a == b, "move-order transposition must produce the same key"
    assert len(a.split(" ")) == 4, f"key must be 4 FEN fields, got {a!r}"
    assert a != _key_after(["e4", "e5", "Nf3", "Nc6", "Bb5"]), (
        "a different position must not share the key"
    )
    print("  [PASS] position_key: transpositions match, positions differ")


def test_is_book_move_with_injected_cache():
    board = chess.Board()
    key = mod.position_key(board)
    mod._book_cache = (time.time(), {key: frozenset({"e2e4"})})
    try:
        assert mod.is_book_move(board, chess.Move.from_uci("e2e4")) is True
        assert mod.is_book_move(board, chess.Move.from_uci("d2d4")) is False
        after = chess.Board()
        after.push_san("e4")
        assert mod.is_book_move(after, chess.Move.from_uci("e7e5")) is False, (
            "a position absent from the book must return False"
        )
    finally:
        mod.invalidate_cache()
    assert mod._book_cache is None, "invalidate_cache must drop the cache"
    print("  [PASS] is_book_move: cache hit/miss + cache invalidation")


def test_iter_eco_rows():
    tsv = (
        "C20\tKing's Pawn Opening\t1. e4 e5\n"
        "\n"
        "C40\tKing's Knight Opening\t1. e4 e5 2. Nf3\n"
    )
    rows = list(mod.iter_eco_rows(tsv))
    assert rows == [
        ("C20", "King's Pawn Opening", "1. e4 e5"),
        ("C40", "King's Knight Opening", "1. e4 e5 2. Nf3"),
    ], rows
    print("  [PASS] iter_eco_rows: parses eco/name/pgn, skips blank lines")


def test_collect_moves_from_lines():
    book = mod.collect_moves_from_lines(
        ["1. e4 e5 2. Nf3", "1. e4 e5 2. Nc3"]
    )
    start_key = mod.position_key(chess.Board())
    assert book[(start_key, "e2e4")] == (2, "e4"), book.get((start_key, "e2e4"))
    after_e4 = chess.Board()
    after_e4.push_san("e4")
    assert book[(mod.position_key(after_e4), "e7e5")] == (2, "e5")
    after_e4e5 = chess.Board()
    after_e4e5.push_san("e4")
    after_e4e5.push_san("e5")
    assert book[(mod.position_key(after_e4e5), "g1f3")] == (1, "Nf3")
    assert book[(mod.position_key(after_e4e5), "b1c3")] == (1, "Nc3")
    print("  [PASS] collect_moves_from_lines: counts accumulate, SAN recorded")


def test_collect_moves_from_games_elo_and_ply():
    high = chess.pgn.read_game(
        StringIO('[WhiteElo "2400"]\n[BlackElo "2300"]\n\n1. e4 e5 2. Nf3 Nc6 *')
    )
    low = chess.pgn.read_game(
        StringIO('[WhiteElo "1200"]\n[BlackElo "1100"]\n\n1. e4 e5 2. Nf3 Nc6 *')
    )
    book = mod.collect_moves_from_games([high, low], min_elo=2000, max_ply=3)
    start_key = mod.position_key(chess.Board())
    assert book[(start_key, "e2e4")][0] == 1, "low-rated game must be skipped"
    after_e4e5 = chess.Board()
    after_e4e5.push_san("e4")
    after_e4e5.push_san("e5")
    assert (mod.position_key(after_e4e5), "g1f3") in book, "3rd ply is within max_ply"
    after_e4e5.push_san("Nf3")
    assert (mod.position_key(after_e4e5), "b8c6") not in book, (
        "max_ply=3 must stop before the 4th ply"
    )
    print("  [PASS] collect_moves_from_games: Elo filter + ply cap respected")


def main() -> int:
    print("=== Running opening_book tests ===")
    tests = [
        test_position_key_transposition,
        test_is_book_move_with_injected_cache,
        test_iter_eco_rows,
        test_collect_moves_from_lines,
        test_collect_moves_from_games_elo_and_ply,
    ]
    for test in tests:
        try:
            test()
        except (AssertionError, ValueError) as exc:
            print(f"\n  [FAIL] {test.__name__}: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
            return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
