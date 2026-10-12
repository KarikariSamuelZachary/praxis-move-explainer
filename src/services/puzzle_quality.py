"""Static puzzle-quality analysis for the personal puzzle extractor.

Pure board/PV analysis -- no engine calls -- so it is cheap to run inside
the synchronous extraction worker for every Stage-2 survivor.

Stage-2 (500k nodes, MultiPV=4) has already established that the best move
is unique (EP margin over the second line) and that the played move was a
real mistake. This layer answers the next question: is the best move
*instructive*? It builds the short solution line from the stored PV,
detects piece traps, scores how forcing the line is, tags a theme, and
applies the acceptance policy:

  a position only counts as a puzzle when the solver's last move in the
  solution line ends in checkmate, a promotion, or a capture. Theme tags
  (mate / promotion / trap / material / forcing / quiet) stay descriptive;
  trap and forcing lines on their own no longer accept.

Limitations (deliberate, documented):
  - Attack/defence counting is static plus a 1-ply trade check. It does
    not see pins except through the legality check on the actual capture,
    and it does not look past the first recapture.
  - "Safe escape" means a legal destination our side does not attack. A
    piece fleeing to an attacked-but-adequately-defended square counts as
    trapped; that is conservative in the keep direction and tuned later.
  - Threats beyond checks/captures (e.g. a quiet mating-net tightener) do
    not count as forcing. Those lines must qualify via mate/material/trap.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import chess

# Centipawn piece scale (same as the pilot's VALS / game_analyzer).
PIECE_CP = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

MATE_IN_MAX = 4
MATERIAL_GAIN_ACCEPT = 300  # >= a minor piece, centipawn scale
MATERIAL_GAIN_PLIES = 3
MARGIN_HIGH = 0.12
FORCING_MIN = 2  # forcing plies (check/capture) within FORCING_WINDOW
FORCING_WINDOW = 4
TRAP_MIN_VALUE = 300  # only minor pieces and up count as "trapped"
# Solution lines keep at most this many plies (3 solver moves + replies).
SOLUTION_MAX_PLIES = 6
# The solver is "up enough" to stop the line once ahead by a pawn after a
# full opponent reply. Checked only on even plies so a hanging capture that
# is immediately recaptured does not end the puzzle.
SOLUTION_MATERIAL_STOP_CP = 100

ENDING_MATE = "mate"
ENDING_PROMOTION = "promotion"
ENDING_CAPTURE = "capture"

THEME_MATE = "mate"
THEME_PROMOTION = "promotion"
THEME_TRAP = "trap"
THEME_MATERIAL = "material"
THEME_FORCING = "forcing"
THEME_QUIET = "quiet"
# Primary tag when several apply. Mate first (most decisive), quiet last.
THEME_PRIORITY = (
    THEME_MATE,
    THEME_PROMOTION,
    THEME_TRAP,
    THEME_MATERIAL,
    THEME_FORCING,
    THEME_QUIET,
)


def material_balance(board: chess.Board, color: chess.Color) -> int:
    """Total material for `color` minus opponent's, centipawn scale."""
    score = 0
    for piece_type, value in PIECE_CP.items():
        score += value * len(board.pieces(piece_type, color))
        score -= value * len(board.pieces(piece_type, not color))
    return score


def material_swing_cp(
    fen_before: str, pv_uci: List[str], plies: int = MATERIAL_GAIN_PLIES
) -> Optional[int]:
    """Net material change for the side to move after up to `plies` PV plies.

    Captures remove enemy material and promotions add it, so both show up
    as a positive swing. Returns None when the PV is empty or its first
    move is illegal in the position.
    """
    try:
        board = chess.Board(fen_before)
    except ValueError:
        return None
    if not pv_uci:
        return None
    solver = board.turn
    start = material_balance(board, solver)
    pushed = 0
    for token in pv_uci[:plies]:
        try:
            move = chess.Move.from_uci(token.strip().lower())
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        board.push(move)
        pushed += 1
    if pushed == 0:
        return None
    return material_balance(board, solver) - start


def forcing_flags(
    fen_before: str, pv_uci: List[str], window: int = FORCING_WINDOW
) -> List[bool]:
    """Per-ply check-or-capture flags over the first `window` PV plies."""
    flags: List[bool] = []
    try:
        board = chess.Board(fen_before)
    except ValueError:
        return flags
    for token in (pv_uci or [])[:window]:
        try:
            move = chess.Move.from_uci(token.strip().lower())
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        is_capture = board.is_capture(move)
        board.push(move)
        flags.append(bool(is_capture or board.is_check()))
    return flags


def build_solution_line(
    fen_before: str,
    best_uci: str,
    pv_uci: List[str],
    max_plies: int = SOLUTION_MAX_PLIES,
) -> List[str]:
    """Short forcing quiz line from the stored engine PV.

    Stops at mate or once the solver is up SOLUTION_MATERIAL_STOP_CP after
    a full opponent reply. A line that reaches neither is kept (capped at
    `max_plies`) only while it stays forcing; otherwise it collapses to the
    single best move rather than quizzing a quiet engine-PV continuation.
    Served to the frontend as solution_moves_uci (routers/user_puzzles.py
    reuses this, so extraction and grading agree on the line).
    """
    best_move_uci = (best_uci or "").strip().lower()
    if not best_move_uci:
        return []
    try:
        board = chess.Board(fen_before)
        best_move = chess.Move.from_uci(best_move_uci)
    except (ValueError, TypeError):
        return [best_move_uci]
    if best_move not in board.legal_moves:
        return [best_move_uci]

    raw_pv = [t.strip().lower() for t in (pv_uci or [])]
    if not raw_pv or raw_pv[0] != best_move_uci:
        raw_pv = [best_move_uci]

    solver_color = board.turn
    initial_material = material_balance(board, solver_color)
    line: List[str] = []
    for token in raw_pv[:max_plies]:
        try:
            move = chess.Move.from_uci(token)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        board.push(move)
        line.append(token)
        if board.is_checkmate():
            if board.turn != solver_color:
                return line
            break
        if board.is_game_over(claim_draw=False):
            break
        # Check material only after a complete opponent reply, so a
        # temporary capture that can immediately be recaptured does not
        # end the puzzle.
        if len(line) >= 2 and len(line) % 2 == 0:
            if material_balance(board, solver_color) - initial_material >= SOLUTION_MATERIAL_STOP_CP:
                return line

    if len(line) > 1 and sum(forcing_flags(fen_before, line)) >= FORCING_MIN:
        return line
    return [best_move_uci]


def solution_ending(
    fen_before: str, best_uci: str, pv_uci: List[str]
) -> Optional[str]:
    """How the solution line concludes for the solver.

    Returns ENDING_MATE / ENDING_PROMOTION / ENDING_CAPTURE when the
    solver's last move in the built line mates, promotes, or captures;
    otherwise None. Only the first three count as a puzzle.
    """
    line = build_solution_line(fen_before, best_uci, pv_uci)
    if not line:
        return None
    try:
        board = chess.Board(fen_before)
    except ValueError:
        return None
    solver_color = board.turn
    last_capture = False
    last_promotion = False
    for index, token in enumerate(line):
        try:
            move = chess.Move.from_uci(token)
        except ValueError:
            return None
        if move not in board.legal_moves:
            return None
        if index % 2 == 0:
            last_capture = board.is_capture(move)
            last_promotion = move.promotion is not None
        board.push(move)
        if board.is_checkmate():
            return ENDING_MATE if board.turn != solver_color else None
    if last_promotion:
        return ENDING_PROMOTION
    if last_capture:
        return ENDING_CAPTURE
    return None


@dataclass
class TrapInfo:
    square: str  # e.g. "b3"
    piece: str  # e.g. "N"
    value: int  # centipawn scale
    escapes: int  # legal destinations our side does not attack
    attackers: int
    defenders: int
    capture_possible: bool  # a legal capture of the square exists
    created_by_move: bool  # not already hanging before our move


def _safe_escapes(board: chess.Board, sq: int, mover: chess.Color) -> int:
    """Legal destinations of the piece on `sq` that `mover` does not attack."""
    safe = 0
    for move in board.legal_moves:
        if move.from_square != sq:
            continue
        if not board.is_attacked_by(mover, move.to_square):
            safe += 1
    return safe


def find_piece_trap(fen_before: str, best_uci: str) -> Optional[TrapInfo]:
    """Detect whether the best move traps an enemy piece.

    A piece counts as trapped when, after our move: it is attacked, it has
    at most one safe escape square, a legal capture wins (either strictly
    more attackers than defenders, or a 1-ply trade where our cheapest
    legal attacker is worth less than the piece -- e.g. a pawn takes a
    defended bishop), and it was not already hanging (attacked and
    undefended) before -- so the move itself creates the trap. Kings never
    count; pawns are below TRAP_MIN_VALUE. Returns the most valuable
    trapped piece, if several.
    """
    try:
        before = chess.Board(fen_before)
        best = chess.Move.from_uci((best_uci or "").strip().lower())
    except ValueError:
        return None
    if best not in before.legal_moves:
        return None
    solver = before.turn
    foe = not solver
    after = before.copy()
    after.push(best)  # foe to move
    found: Optional[TrapInfo] = None
    for sq, piece in after.piece_map().items():
        if piece.color != foe or piece.piece_type == chess.KING:
            continue
        value = PIECE_CP.get(piece.piece_type, 0)
        if value < TRAP_MIN_VALUE:
            continue
        attackers = after.attackers(solver, sq)
        if not attackers:
            continue
        # Already hanging before our move: we did not create this.
        if before.attackers(solver, sq) and not before.attackers(foe, sq):
            continue
        defenders = after.attackers(foe, sq)
        escapes = _safe_escapes(after, sq, solver)
        if escapes > 1:
            continue
        # Pin-aware: only captures that are actually legal count, and the
        # cheapest legal attacker decides whether the capture still wins
        # when the piece is defended (1-ply trade check, e.g. pawn takes
        # a defended bishop). Note after.push() left the turn with the
        # opponent, so legality is tested on a copy with our turn to move.
        ours = after.copy(stack=False)
        ours.turn = solver
        legal_caps = [
            m for m in ours.legal_moves
            if m.to_square == sq and m.from_square in attackers
        ]
        if not legal_caps:
            continue
        cheapest = min(
            PIECE_CP[after.piece_at(m.from_square).piece_type]
            for m in legal_caps
        )
        if not (len(attackers) > len(defenders) or cheapest < value):
            continue
        info = TrapInfo(
            square=chess.square_name(sq),
            piece=piece.symbol().upper(),
            value=value,
            escapes=escapes,
            attackers=len(attackers),
            defenders=len(defenders),
            capture_possible=True,
            created_by_move=True,
        )
        if found is None or value > found.value:
            found = info
    return found


@dataclass
class QualityReport:
    themes: List[str]
    primary: str
    mate_in: Optional[int]
    material_gain: Optional[int]
    trap: Optional[TrapInfo]
    forcing: int
    is_promotion: bool


def classify_quality(
    fen_before: str,
    best_uci: str,
    pv_uci: List[str],
    best_mate: Optional[int],
) -> QualityReport:
    """Tag the best move. Themes are descriptive; acceptance is decide()."""
    pv = list(pv_uci or [])
    mate_in = best_mate if best_mate is not None and best_mate > 0 else None
    material_gain = material_swing_cp(fen_before, pv, MATERIAL_GAIN_PLIES)
    trap = find_piece_trap(fen_before, best_uci)
    forcing = sum(forcing_flags(fen_before, pv, FORCING_WINDOW))
    try:
        is_promotion = (
            chess.Move.from_uci((best_uci or "").strip().lower()).promotion
            is not None
        )
    except ValueError:
        is_promotion = False

    themes: List[str] = []
    if mate_in is not None and mate_in <= MATE_IN_MAX:
        themes.append(THEME_MATE)
    if is_promotion and (
        mate_in is not None
        or (material_gain is not None and material_gain >= MATERIAL_GAIN_ACCEPT)
    ):
        themes.append(THEME_PROMOTION)
    if trap is not None:
        themes.append(THEME_TRAP)
    if material_gain is not None and material_gain >= MATERIAL_GAIN_ACCEPT:
        themes.append(THEME_MATERIAL)
    if forcing >= FORCING_MIN:
        themes.append(THEME_FORCING)
    if not themes:
        themes.append(THEME_QUIET)
    primary = next(t for t in THEME_PRIORITY if t in themes)
    return QualityReport(
        themes=themes,
        primary=primary,
        mate_in=mate_in,
        material_gain=material_gain,
        trap=trap,
        forcing=forcing,
        is_promotion=is_promotion,
    )


def decide(
    quality: QualityReport, ending: Optional[str]
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Acceptance policy. Returns (accept, theme, reject_reason).

    A position only counts as a puzzle when the solver's last move in the
    solution line mates, promotes, or captures. Mate and promotion endings
    keep their theme; a capture keeps the trap theme when the best move
    also sets a clean trap, else material. Anything else falls out as
    "no_concrete_ending" -- quiet squeezes included. An announced mate the
    stored line cannot deliver is "mate_too_deep".
    """
    if quality.mate_in is not None and ending != ENDING_MATE:
        # Announced mate beyond the stored line: not a usable puzzle.
        return False, None, "mate_too_deep"
    if ending == ENDING_MATE:
        return True, THEME_MATE, None
    if ending == ENDING_PROMOTION:
        return True, THEME_PROMOTION, None
    if ending == ENDING_CAPTURE:
        if quality.trap is not None:
            return True, THEME_TRAP, None
        return True, THEME_MATERIAL, None
    return False, None, "no_concrete_ending"
