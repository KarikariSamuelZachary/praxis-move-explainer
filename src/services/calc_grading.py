"""Exercise identity + server-side grading for the Blind Calculation trainer.

An exercise is (segment_id, offset, n): skip the first `offset` solver
moves of the segment, then calculate `n` solver moves (0 <= offset,
offset + n <= L where L is the segment's solver-move count). The exercise
line is 2n-1 plies (solver, defender, ..., solver), taken from the stored
segment line starting at interleaved ply 2*offset.

Grading is exact-UCI match (promotion normalized to lowercase) up to the
FIRST mismatch only: everything after the divergence ply is void.
"""

from __future__ import annotations

import random

import chess


def choose_offset(segment_len: int, n: int,
                  rng: random.Random | None = None) -> int:
    """The ONE place offset policy lives (default: uniform in [0, L-n])."""
    if n < 1 or segment_len < n:
        raise ValueError(f"need 1 <= n <= L, got n={n} L={segment_len}")
    rng = rng if rng is not None else random
    return rng.randint(0, segment_len - n)


def exercise_line_tokens(line_uci: str, offset: int, n: int) -> list[str]:
    """Expected 2n-1 exercise plies from the stored line (0-based offset)."""
    toks = line_uci.split()
    start = 2 * offset
    end = start + 2 * n - 1
    if offset < 0 or end > len(toks):
        raise ValueError(f"offset={offset} n={n} out of range "
                         f"for {len(toks)}-ply line")
    return toks[start:end]


def exercise_fen(segment_fen: str, line_uci: str, offset: int) -> str:
    """Exercise start FEN: segment_fen replayed through 2*offset plies."""
    board = chess.Board(segment_fen)
    for uci in line_uci.split()[:2 * offset]:
        board.push(chess.Move.from_uci(uci))
    return board.fen()


def normalize_uci(uci: str) -> str:
    """Promotion normalization: case-insensitive UCI (e7e8Q == e7e8q)."""
    return uci.strip().lower()


def solver_score(n: int, first_divergence_ply: int | None,
                 diverged_on: str | None, solved: bool) -> float:
    """Fractional score: correct solver moves / n.

    Divergence on solver ply k (0-based k) means k correct; divergence on
    the defender reply after solver move i (1-based i) means i correct;
    solved means n. Ply indices are 0-based within the exercise line (even
    = solver, odd = defender).
    """
    if solved:
        return 1.0
    assert first_divergence_ply is not None and diverged_on is not None
    p = first_divergence_ply
    correct = (p + 1) // 2 if diverged_on == "defender" else p // 2
    return correct / n


def grade(expected: list[str], submitted: list[str]) -> dict:
    """Grade up to the first mismatch.

    Returns {"solved": bool, "first_divergence_ply": int|None,
    "diverged_on": "solver"|"defender"|None, "correct_prefix_plies": int,
    "continuation": [...] (correct moves from divergence to exercise end;
    [] on success — the line is never revealed on success)}.
    Ply indices are 0-based within the exercise line; even plies are solver
    moves, odd plies defender replies.
    """
    exp = [normalize_uci(m) for m in expected]
    sub = [normalize_uci(m) for m in submitted]
    prefix = 0
    for i, (e, s) in enumerate(zip(exp, sub)):
        if e != s:
            return {
                "solved": False,
                "first_divergence_ply": i,
                "diverged_on": "solver" if i % 2 == 0 else "defender",
                "correct_prefix_plies": i,
                "continuation": expected[i:],
            }
        prefix = i + 1
    if len(sub) != len(exp):  # length validated upstream; defensive
        return {
            "solved": False,
            "first_divergence_ply": prefix,
            "diverged_on": "solver" if prefix % 2 == 0 else "defender",
            "correct_prefix_plies": prefix,
            "continuation": expected[prefix:],
        }
    return {
        "solved": True,
        "first_divergence_ply": None,
        "diverged_on": None,
        "correct_prefix_plies": len(exp),
        "continuation": [],
    }
