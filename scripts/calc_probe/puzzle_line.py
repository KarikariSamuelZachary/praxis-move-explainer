"""Lichess puzzle line handling + pilot taxonomy.

Setup-move quirk (mirrors scripts/source_endgame_candidates.py::replay_ending and
frontend/src/lib/lichess.ts): the CSV/DB FEN is the position BEFORE the
opponent's setup move; Moves[0] is that setup move; the solver line is Moves[1:].

After the setup move the line alternates, solver to move first:
    s0, d0, s1, d1, ...  (S solver moves, S-1 defender replies)
"""

from __future__ import annotations

import chess

BANDS = [
    (1200, 1400),
    (1400, 1600),
    (1600, 1800),
    (1800, 2000),
    (2000, 2200),
    (2200, None),  # 2200+
]

MATE_IN_PREFIX = "mateIn"
QUIET_THEME = "quietMove"
ENDGAME_THEME = "endgame"
FORCING_THEMES = frozenset({
    "sacrifice", "deflection", "attraction", "discoveredAttack",
    "doubleCheck", "fork", "pin", "skewer", "interference", "clearance",
})


def band_of(rating: int) -> str | None:
    for lo, hi in BANDS:
        if hi is None:
            if rating >= lo:
                return f"{lo}+"
        elif lo <= rating < hi:
            return f"{lo}-{hi}"
    return None


def band_bounds(label: str) -> tuple[int, int | None]:
    if label.endswith("+"):
        return int(label[:-1]), None
    lo, hi = label.split("-")
    return int(lo), int(hi)


def family_of(themes: list[str]) -> str | None:
    """Exactly one family per puzzle, priority: mate > quiet > endgame > forcing."""
    if any(t.startswith(MATE_IN_PREFIX) and t[len(MATE_IN_PREFIX):].isdigit()
           and int(t[len(MATE_IN_PREFIX):]) >= 2 for t in themes):
        return "mate"
    if QUIET_THEME in themes:
        return "quiet"
    if ENDGAME_THEME in themes:
        return "endgame"
    if any(t in FORCING_THEMES for t in themes):
        return "forcing"
    return None


def depth_bucket(solver_move_count: int) -> str:
    if solver_move_count <= 5:
        return str(solver_move_count)
    return "6+"


def build_line(raw_fen: str, moves: list[str]) -> dict:
    """Apply the setup move and split the solver/defender line.

    Returns dict with puzzle_fen (position after setup, solver to move),
    setup_move, solver_moves (= line[0::2] of Moves[1:]), defender_moves
    (= line[1::2]), and solver_move_count S.
    """
    tokens = [m for m in moves if m]
    if len(tokens) < 1:
        raise ValueError("puzzle has no moves")
    board = chess.Board(raw_fen)
    setup = chess.Move.from_uci(tokens[0])
    if setup not in board.legal_moves:
        raise ValueError(f"setup move {tokens[0]} illegal in raw FEN")
    board.push(setup)
    puzzle_fen = board.fen()
    rest = tokens[1:]
    for uci in rest:
        mv = chess.Move.from_uci(uci)
        if mv not in board.legal_moves:
            raise ValueError(f"line move {uci} illegal at {board.fen()}")
        board.push(mv)
    solver_moves = rest[0::2]
    defender_moves = rest[1::2]
    return {
        "puzzle_fen": puzzle_fen,
        "raw_fen": raw_fen,
        "setup_move": tokens[0],
        "solver_moves": solver_moves,
        "defender_moves": defender_moves,
        "solver_move_count": len(solver_moves),
    }


def puzzle_fen_after_setup(raw_fen: str, setup_uci: str) -> str:
    board = chess.Board(raw_fen)
    board.push(chess.Move.from_uci(setup_uci))
    return board.fen()


def line_positions(puzzle_fen: str, solver_moves: list[str],
                   defender_moves: list[str]) -> tuple[list[str], list[str]]:
    """Return (solver_fens, defender_fens_before).

    solver_fens[i] = position before solver move s_i (solver to move).
    defender_fens_before[i] = position before defender reply d_i (defender to move).
    """
    board = chess.Board(puzzle_fen)
    solver_fens: list[str] = []
    defender_fens: list[str] = []
    for i, s in enumerate(solver_moves):
        solver_fens.append(board.fen())
        board.push(chess.Move.from_uci(s))
        if i < len(defender_moves):
            defender_fens.append(board.fen())
            board.push(chess.Move.from_uci(defender_moves[i]))
    return solver_fens, defender_fens
