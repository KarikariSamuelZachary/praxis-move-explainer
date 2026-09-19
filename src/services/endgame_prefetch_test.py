"""
Verification harness for services/endgame_prefetch.py.

Covers the pure parts without any network or database:
  A. solution_line_fens() replays a real puzzle line and starts at the
     stored drill FEN; malformed/illegal lines and replay mismatches fall
     back to just the stored FEN.
  B. The selection hook is a no-op unless the app registered the persistent
     tablebase cache (no cache -> available() False, enqueue returns False),
     so tests and seed scripts never fire background network probes.

Run with: cd src && ../venv/bin/python services/endgame_prefetch_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import endgame_prefetch
from services.tablebase import set_persistent_cache

# Puzzle 000Vc from the sourced pool (pawn endgame, solver = black).
PUZZLE_FEN = "8/8/4k1p1/2KpP2p/5PP1/8/8/8 w - - 0 53"
MOVES = ["g4h5", "g6h5", "f4f5", "e6e5", "f5f6", "e5f6"]
STORED_DRILL_FEN = "8/8/4k1p1/2KpP2P/5P2/8/8/8 b - - 0 53"


def _key(fen):
    return " ".join(fen.split()[:4])


def test_solution_line_fens():
    fens = endgame_prefetch.solution_line_fens(PUZZLE_FEN, MOVES, STORED_DRILL_FEN)
    assert len(fens) == len(MOVES), fens
    assert _key(fens[0]) == _key(STORED_DRILL_FEN), fens[0]

    # A stored FEN the replay does not reproduce -> just that FEN.
    other = "8/8/8/8/8/8/8/8 w - - 0 1"
    assert endgame_prefetch.solution_line_fens(PUZZLE_FEN, MOVES, other) == [other]

    # Illegal/malformed moves -> just the stored FEN.
    assert endgame_prefetch.solution_line_fens(PUZZLE_FEN, ["e2e4"], STORED_DRILL_FEN) == [
        STORED_DRILL_FEN
    ]
    assert endgame_prefetch.solution_line_fens(PUZZLE_FEN, ["zz99"], STORED_DRILL_FEN) == [
        STORED_DRILL_FEN
    ]
    print("  line replay, mismatch fallback, illegal-move fallback OK")


def test_noop_without_persistent_cache():
    set_persistent_cache(None)
    assert not endgame_prefetch.available()
    assert endgame_prefetch.enqueue_drill_line("00000000-0000-0000-0000-000000000000") is False
    assert endgame_prefetch.prefetch_drill_line(
        "00000000-0000-0000-0000-000000000000"
    ) == 0
    assert endgame_prefetch.enqueue_fens([STORED_DRILL_FEN]) == 0
    print("  prefetch is a no-op without the persistent cache")


def main():
    print("A. solution-line replay:")
    test_solution_line_fens()
    print("B. gating:")
    test_noop_without_persistent_cache()
    print("all endgame prefetch checks passed")


if __name__ == "__main__":
    main()
