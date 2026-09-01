"""
Shared feature extractor for the Engine Sparring persona reranker.

This module is the SINGLE source of per-move style features. It computes, for
one candidate move, the raw signals that a FUTURE task will turn into persona
weights (Attacker / Defender / Sacrificer / Positional). It deliberately does
NOT build any weight vectors or reranking logic, and it does NOT depend on
persona_bounds.py (which was built earlier and lives one layer "above" this
one: it will eventually consume these raw scores, decay them by engine trust,
and clamp the final [-1, 1] persona bias).

Design contract (hard rules):
  * compute_style_scores(board_before, move) compares ONLY the position
    before `move` (P) to the position after `move` (P'). No search, no
    opponent-reply evaluation, no game history, no Stockfish. Everything is
    derived from python-chess board state.
  * initiative_proxy is ALWAYS 0.0 (it would require evaluating opponent
    replies = lookahead, out of scope).

Relationship to persona_bounds.py:
  * The fields returned here are PRE-CLAMP, PRE-DECAY raw features. The
    ranges documented below are the raw input ranges. persona_bounds.py
    expects the FUTURE weight-vector layer to combine these raw features
    (with its own normalization) into a single raw_persona_score in
    [-1, 1], then clamp it. This module does NOT clamp to [-1, 1]; it only
    guarantees the ranges documented below so that the future weighting
    layer knows what it is consuming.

SACRIFICE SIGNAL -- static en-prise proxy
=========================================
The canonical gated sacrifice heuristic `_is_sacrifice()` in
services.opponent_style.py requires a 3-ply recoup look-ahead window over a
game mainline (it needs `mainline_after` and `move_index`). That is
incompatible with this module's "P -> P' only, no lookahead" contract, and in
single-move mode it is mathematically degenerate (a legal move never reduces
the mover's own material in one ply, so it would always return False).

Per an explicit decision, sacrifice_signal here is therefore a STATIC en-prise
proxy that REUSES only the material primitives from opponent_style.py
(`_PIECE_VALUE`, `_material_for_color`). The gate threshold is LOCAL to this
module (`SACRIFICE_THRESHOLD`), deliberately NOT imported from opponent_style:
that module's `SAC_MATERIAL_THRESHOLD == 3` was tuned for its own "did the
player blunder" consumer, whereas this is a style feature that must FIND
sacrifices, so the local threshold is 2 (admitting minor-piece-for-pawn). See
`_sacrifice_concession()` for the exact rule (including the check-gated
"king-stab" special case) and its documented limitations.
"""
from dataclasses import dataclass
from typing import Dict, Tuple

import chess

from services.opponent_style import (
    _PIECE_VALUE,
    _material_for_color,
)

# --- attack/defense piece weights -------------------------------------------
# These are NOT material values; they are "attacking power" weights used only
# for king-zone pressure / defense (a queen exerts more pressure on a king's
# ring than a pawn does, but this is not about how much material it is worth).
# Kings are excluded (a king does not exert attack pressure).
_ATTACK_PIECE_VALUE = {
    chess.PAWN: 1.0,
    chess.KNIGHT: 2.0,
    chess.BISHOP: 2.0,
    chess.ROOK: 2.0,
    chess.QUEEN: 3.0,
}

# --- king danger-zone weights ------------------------------------------------
# Squares at Chebyshev distance <= 1 from a king get weight 1.0; distance 2
# gets weight 0.4. Distance 0 (the king's own square) is included at 1.0 so
# that a piece attacking the king square (a check, or a defender covering the
# king square) is measured.
_ZONE_WEIGHT_NEAR = 1.0
_ZONE_WEIGHT_FAR = 0.4

# --- volatility normalization references -------------------------------------
# Material swing is normalized by the value of a queen (the largest single
# capture); king-pressure swing is normalized by a reference "large" swing.
_VOL_MAT_SWING_MAX = 9.0
_VOL_PRESSURE_REF = 6.0

# --- sacrifice threshold ------------------------------------------------------
# LOCAL to this module, deliberately NOT imported from opponent_style (whose
# SAC_MATERIAL_THRESHOLD == 3 was tuned for a "did the player blunder" consumer).
# Here sacrifice_signal is a style feature that must DETECT sacrifices, so 2 is
# used to admit the canonical minor-piece-for-pawn class (Greek gift / Fried
# Liver). Even trades and favorable captures (net <= 0) still score 0.0.
SACRIFICE_THRESHOLD = 2


@dataclass
class AttackGainSub:
    """Subcomponents of attack_gain, exposed per-move for debugging.

    Units are raw (heterogeneous) deltas; see compute_style_scores() for the
    exact formula of each.
    """
    king_zone_pressure: float
    king_adjacent_attacks: float
    checks: float
    open_lines: float
    escape_square_pressure: float


@dataclass
class DefenseGainSub:
    """Subcomponents of defense_gain, exposed per-move for debugging.

    Units are raw (heterogeneous) deltas EXCEPT king_zone_defense, which is a
    normalized coverage fraction (see _king_zone_coverage) and therefore lives
    in [-1, +1]. See compute_style_scores() for the exact formula of each.
    """
    enemy_pressure_reduction: float
    king_zone_defense: float
    line_blocking: float
    pawn_shield: float
    king_mobility: float


# Intended numeric ranges for the top-level StyleScores fields. These are the
# RAW, PRE-CLAMP feature values; the future weight-vector layer is responsible
# for normalizing/weighting them into a raw_persona_score in [-1, 1] (which
# persona_bounds.bounded_persona_bias() will then clamp). None of these are
# themselves bounded to [-1, 1] except where explicitly stated.
#
#   attack_gain       : raw SUM of 5 subcomponents in heterogeneous units
#                       (see below). Higher = more attacking. Typical range
#                       roughly [-8, +20]; unbounded in principle. A move that
#                       does nothing to the enemy king scores ~0.
#   defense_gain      : raw SUM of 5 subcomponents, same units philosophy as
#                       attack_gain. Higher = more defensive/consolidating.
#                       Typical range roughly [-8, +20]. NOTE: one subcomponent
#                       (king_zone_defense) is now a NORMALIZED coverage
#                       fraction in [-1, +1] rather than a raw count; the other
#                       four remain raw deltas.
#   sacrifice_signal  : [0, 1], binary. 1.0 iff the move leaves >=
#                       SACRIFICE_THRESHOLD (LOCAL constant = 2) material en
#                       prise net of what it captured, or is a check-gated
#                       "king-stab" (see _sacrifice_concession). 0.0 otherwise.
#   volatility        : [0, 1], higher = more tactically volatile (capture /
#                       check / big pressure swing). Always in [0, 1] by
#                       construction (three normalized inputs averaged).
#   initiative_proxy  : ALWAYS 0.0 (not implemented; requires lookahead).
@dataclass
class StyleScores:
    attack_gain: float
    defense_gain: float
    sacrifice_signal: float
    volatility: float
    initiative_proxy: float
    attack_sub: AttackGainSub
    defense_sub: DefenseGainSub


def _danger_zone_weights(king_square: chess.Square) -> Dict[chess.Square, float]:
    """Weight every square within Chebyshev distance 2 of `king_square`.

    distance <= 1 -> _ZONE_WEIGHT_NEAR (1.0); distance == 2 -> _ZONE_WEIGHT_FAR
    (0.4). The king's own square (distance 0) is included at weight 1.0.
    """
    king_file = chess.square_file(king_square)
    king_rank = chess.square_rank(king_square)
    weights: Dict[chess.Square, float] = {}
    for df in (-2, -1, 0, 1, 2):
        for dr in (-2, -1, 0, 1, 2):
            f = king_file + df
            r = king_rank + dr
            if not (0 <= f < 8 and 0 <= r < 8):
                continue
            distance = max(abs(df), abs(dr))
            weights[chess.square(f, r)] = (
                _ZONE_WEIGHT_NEAR if distance <= 1 else _ZONE_WEIGHT_FAR
            )
    return weights


def _zone_pressure(
    board: chess.Board,
    attacker_color: chess.Color,
    zone_weights: Dict[chess.Square, float],
) -> float:
    """Sum of (attack_piece_value * zone_weight) for every piece of
    `attacker_color` that attacks a square in the weighted danger zone.

    A piece attacking multiple zone squares contributes once per square (so a
    queen controlling the whole ring is correctly scored as high pressure).
    Kings are excluded (a king does not exert attack pressure).
    """
    total = 0.0
    for square, weight in zone_weights.items():
        for attacker_square in board.attackers(attacker_color, square):
            piece = board.piece_at(attacker_square)
            if piece is None or piece.piece_type == chess.KING:
                continue
            total += _ATTACK_PIECE_VALUE.get(piece.piece_type, 0.0) * weight
    return total


def _king_zone_coverage(board: chess.Board, color: chess.Color) -> float:
    """Fraction (0..1) of `color`'s own king danger-zone that is defended by at
    least one non-king piece.

    Replaces the raw absolute defender COUNT previously used for
    king_zone_defense. It is normalized by the zone's total weight so the score
    is invariant to WHERE the zone sits: a center king has ~25 zone squares, a
    corner king far fewer, and a raw count mechanically collapses when the king
    castles into the corner even though it is objectively safer.

    Each zone square is counted ONCE (binary) whether defended by one piece or
    many, so a developing move that merely over-protects an already-covered
    square contributes nothing -- only newly defending a previously-undefended
    square (filling a "hole") moves the fraction. The king itself is excluded as
    a defender (a king cannot defend its own square).
    """
    king_square = board.king(color)
    if king_square is None:
        return 0.0
    zone = _danger_zone_weights(king_square)
    total_weight = sum(zone.values())
    if total_weight == 0.0:
        return 0.0
    covered_weight = 0.0
    for square, weight in zone.items():
        defenders = board.attackers(color, square)
        if any(defender != king_square for defender in defenders):
            covered_weight += weight
    return covered_weight / total_weight


def _king_adjacent_attack_count(
    board: chess.Board, attacker_color: chess.Color, king_square: chess.Square
) -> int:
    """Number of squares at Chebyshev distance exactly 1 from `king_square`
    that are attacked by at least one non-king piece of `attacker_color`."""
    king_file = chess.square_file(king_square)
    king_rank = chess.square_rank(king_square)
    count = 0
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            f = king_file + df
            r = king_rank + dr
            if not (0 <= f < 8 and 0 <= r < 8):
                continue
            square = chess.square(f, r)
            for attacker_square in board.attackers(attacker_color, square):
                piece = board.piece_at(attacker_square)
                if piece is not None and piece.piece_type != chess.KING:
                    count += 1
                    break
    return count


def _king_available_squares(board: chess.Board, color: chess.Color) -> int:
    """Pseudo-legal king mobility: number of squares at distance 1 from the
    king that are not occupied by an own piece and not attacked by the enemy.

    Turn-independent (based purely on attack maps), so it can be compared
    before and after a move even though the side to move changes. Castling is
    intentionally ignored (king steps only).
    """
    king_square = board.king(color)
    if king_square is None:
        return 0
    enemy = not color
    count = 0
    for to_square in chess.SQUARES:
        if chess.square_distance(king_square, to_square) != 1:
            continue
        piece = board.piece_at(to_square)
        if piece is not None and piece.color == color:
            continue
        if board.attackers(enemy, to_square):
            continue
        count += 1
    return count


def _has_clear_ray(board: chess.Board, from_square: chess.Square,
                   to_square: chess.Square) -> bool:
    """True iff from_square and to_square are on a shared rank/file/diagonal
    and every square strictly between them is empty.

    Two ALIGNED but ADJACENT squares count as clear (there are zero squares
    strictly between them). This must be distinguished from two squares that
    are NOT aligned at all, which also have no squares between them: only the
    aligned case constitutes a ray. ``chess.between`` returns an empty set in
    both cases, so alignment is checked explicitly first.
    """
    if from_square == to_square:
        return False

    from_file = chess.square_file(from_square)
    from_rank = chess.square_rank(from_square)
    to_file = chess.square_file(to_square)
    to_rank = chess.square_rank(to_square)

    same_rank = from_rank == to_rank
    same_file = from_file == to_file
    same_diagonal = abs(from_file - to_file) == abs(from_rank - to_rank)

    if not (same_rank or same_file or same_diagonal):
        return False

    between = chess.between(from_square, to_square)
    return not (board.occupied & between)


def _sliders_with_clear_ray(
    board: chess.Board, color: chess.Color, zone_weights: Dict[chess.Square, float]
) -> set:
    """Set of `color`'s sliders (bishop/rook/queen) that have at least one
    clear, unblocked ray to a square in `zone_weights`."""
    sliders = set()
    slider_pieces = (
        board.pieces(chess.BISHOP, color)
        | board.pieces(chess.ROOK, color)
        | board.pieces(chess.QUEEN, color)
    )
    for piece_square in slider_pieces:
        for zone_square in zone_weights:
            if _has_clear_ray(board, piece_square, zone_square):
                sliders.add(piece_square)
                break
    return sliders


def _pawn_shield_score(board: chess.Board, color: chess.Color) -> float:
    """Score the pawn cover DIRECTLY IN FRONT of `color`'s king.

    Geometry (deliberately NOT "any pawn near the king counts as defensive"):
      * Consider the king's file plus one file on each side (clipped to the
        board). King file counts 1.0, side files 0.5.
      * "In front" = the two ranks immediately ahead of the king in the king's
        direction of travel (rank+1 and rank+2 for White, rank-1/-2 for Black).
        Immediate rank counts 1.0, second rank 0.5.
      * A pawn of `color` on a qualifying square adds file_weight*rank_weight.

    A full 3-pawn shield directly in front sums to 3.0; a bare king sums to 0.
    """
    king_square = board.king(color)
    if king_square is None:
        return 0.0
    king_file = chess.square_file(king_square)
    king_rank = chess.square_rank(king_square)
    direction = 1 if color == chess.WHITE else -1
    score = 0.0
    for df in (-1, 0, 1):
        f = king_file + df
        if not (0 <= f < 8):
            continue
        file_weight = 1.0 if df == 0 else 0.5
        for step in (1, 2):
            r = king_rank + direction * step
            if not (0 <= r < 8):
                continue
            rank_weight = 1.0 if step == 1 else 0.5
            piece = board.piece_at(chess.square(f, r))
            if piece is not None and piece.piece_type == chess.PAWN and piece.color == color:
                score += file_weight * rank_weight
    return score


def _captured_value(board_before: chess.Board, move: chess.Move) -> int:
    """Material value of the piece captured by `move` (0 if not a capture).

    Reuses opponent_style._PIECE_VALUE (P=1,N=3,B=3,R=5,Q=9). En-passant
    counts the pawn (which is not on move.to_square).
    """
    if not board_before.is_capture(move):
        return 0
    if board_before.is_en_passant(move):
        return _PIECE_VALUE[chess.PAWN]
    captured = board_before.piece_at(move.to_square)
    if captured is None:
        return 0
    return _PIECE_VALUE.get(captured.piece_type, 0)


def _sacrifice_concession(
    board_before: chess.Board, move: chess.Move, board_after: chess.Board
) -> Tuple[int, int, int]:
    """Static en-prise sacrifice proxy (P -> P' only). Reuses opponent_style
    material primitives rather than reimplementing a material table.

    Returns (hung_value, captured_value, concession) where:
      * hung_value     = total material of the MOVER'S MOVED PIECE(S) left en
                         prise in the after-position, per the rule below. Only
                         the piece(s) this move actually moved are considered
                         (plus the castling rook when applicable), so material
                         the mover was ALREADY losing before the move is not
                         attributed to it.
      * captured_value = material the move itself captured (0 if quiet).
      * concession     = max(0, hung_value - captured_value). The subtraction
                         is what stops an even trade (NxN, QxQ) from being
                         misread as a sacrifice: capturing equal material
                         cancels out the piece you leave hanging.

    A moved piece counts as hung when it is attacked by the enemy and not
    defended by the mover, EXCEPT for the check-gated "king-stab" case: if the
    move gives CHECK, captured strictly cheaper material than the moved piece,
    and the moved piece lands on a square the ENEMY KING attacks, the enemy
    king's attack overrides any friendly piece defense (the Greek-gift
    signature -- a king "recapture" cannot be dealt with like a normal piece
    attacker). The check gate is what keeps a safely defended, non-checking
    pawn grab (e.g. Bxg7) from being mislabeled as a sacrifice.

    Documented limitations:
      * SEE depth is 1: a piece defended by a single own piece is treated as
        safe even if a cheaper enemy piece could win the exchange. Not flagged.
      * Only the moved piece(s) are checked; a move that exposes a DIFFERENT
        already-present piece (a rare discovered hang) is not detected.
      * COMPENSATION-BLINDNESS (named known gap): this proxy cannot
        distinguish "material given up with real compensation/follow-up" from
        "material given up for nothing". An undefended minor-piece-for-pawn
        capture is flagged sacrificial whether it is a genuine tactical
        sacrifice or a plain blunder, because that distinction fundamentally
        requires looking past the single move -- the same category of problem
        initiative_proxy was deferred for. No static fix is attempted here.
      * The king-stab gate requires CHECK, so a non-checking piece-for-pawn
        stab that IS a real sacrifice (e.g. Nxh7 when nothing defends h7) is
        deliberately missed: a conservative false negative accepted over a
        false positive on a safely defended move.
    """
    mover = board_before.turn
    enemy = not mover

    captured_value = _captured_value(board_before, move)
    enemy_king_square = board_after.king(enemy)

    moved_squares = [move.to_square]
    if board_before.is_castling(move):
        rank = chess.square_rank(move.to_square)
        rook_file = (
            chess.square_file(chess.F1)
            if board_before.is_kingside_castling(move)
            else chess.square_file(chess.D1)
        )
        moved_squares.append(chess.square(rook_file, rank))

    hung_value = 0
    for square in moved_squares:
        piece = board_after.piece_at(square)
        if piece is None or piece.color != mover or piece.piece_type == chess.KING:
            continue
        enemy_attackers = board_after.attackers(enemy, square)
        if not enemy_attackers:
            continue
        piece_value = _PIECE_VALUE.get(piece.piece_type, 0)
        king_stab = (
            board_after.is_check()
            and captured_value < piece_value
            and enemy_king_square is not None
            and enemy_king_square in enemy_attackers
        )
        if not king_stab and board_after.attackers(mover, square):
            continue
        hung_value += piece_value

    concession = max(0, hung_value - captured_value)
    return hung_value, captured_value, concession


def compute_style_scores(
    board_before: chess.Board, move: chess.Move
) -> Tuple[StyleScores, dict]:
    """Compute every per-move style feature by comparing board_before to the
    position after `move` (P -> P' only). No lookahead, no search, no history.

    Returns (scores, debug) where `debug` is a plain dict exposing every
    subcomponent plus the raw intermediate values used for tuning.
    """
    if not board_before.is_legal(move):
        raise ValueError(f"illegal move {move.uci()} in position {board_before.fen()}")

    mover = board_before.turn
    enemy = not mover

    board_after = board_before.copy(stack=False)
    board_after.push(move)

    enemy_king_before = board_before.king(enemy)
    enemy_king_after = board_after.king(enemy)
    own_king_before = board_before.king(mover)
    own_king_after = board_after.king(mover)

    enemy_zone_before = _danger_zone_weights(enemy_king_before) if enemy_king_before is not None else {}
    enemy_zone_after = _danger_zone_weights(enemy_king_after) if enemy_king_after is not None else {}
    own_zone_before = _danger_zone_weights(own_king_before) if own_king_before is not None else {}
    own_zone_after = _danger_zone_weights(own_king_after) if own_king_after is not None else {}

    # --- attack_gain subcomponents -----------------------------------------
    king_zone_pressure = (
        _zone_pressure(board_after, mover, enemy_zone_after)
        - _zone_pressure(board_before, mover, enemy_zone_before)
    )

    kaa_before = _king_adjacent_attack_count(board_before, mover, enemy_king_before) if enemy_king_before is not None else 0
    kaa_after = _king_adjacent_attack_count(board_after, mover, enemy_king_after) if enemy_king_after is not None else 0
    king_adjacent_attacks = float(kaa_after - kaa_before)

    checks = 1.0 if board_after.is_check() else 0.0

    open_before = _sliders_with_clear_ray(board_before, mover, enemy_zone_before)
    open_after = _sliders_with_clear_ray(board_after, mover, enemy_zone_after)
    open_lines = float(len(open_after - open_before))

    escape_square_pressure = float(
        _king_available_squares(board_before, enemy)
        - _king_available_squares(board_after, enemy)
    )

    # --- defense_gain subcomponents ----------------------------------------
    enemy_pressure_reduction = (
        _zone_pressure(board_before, enemy, own_zone_before)
        - _zone_pressure(board_after, enemy, own_zone_after)
    )

    king_zone_defense = (
        _king_zone_coverage(board_after, mover)
        - _king_zone_coverage(board_before, mover)
    )

    # line_blocking specifically asks "is an enemy slider's ray toward the
    # OWN KING'S SQUARE now blocked?". We target the king square (not the
    # whole zone) so that interposing ON a square adjacent to the king still
    # counts as blocking the line *to the king*.
    enemy_line_before = (
        _sliders_with_clear_ray(board_before, enemy, {own_king_before: 1.0})
        if own_king_before is not None else set()
    )
    enemy_line_after = (
        _sliders_with_clear_ray(board_after, enemy, {own_king_after: 1.0})
        if own_king_after is not None else set()
    )
    line_blocking = float(len(enemy_line_before - enemy_line_after))

    pawn_shield = _pawn_shield_score(board_after, mover) - _pawn_shield_score(board_before, mover)

    king_mobility = float(
        _king_available_squares(board_after, mover)
        - _king_available_squares(board_before, mover)
    )

    # --- sacrifice_signal --------------------------------------------------
    hung_value, captured_value, concession = _sacrifice_concession(
        board_before, move, board_after
    )
    sacrifice_signal = 1.0 if concession >= SACRIFICE_THRESHOLD else 0.0

    # --- volatility --------------------------------------------------------
    # Three inputs, each normalized to [0, 1] BEFORE combining:
    #   1. material swing  : captured_value / 9 (queen = the max single capture).
    #   2. king-pressure swing: (|attack pressure delta| + |own-king enemy-
    #      pressure delta|) / 6, capped at 1.0. 6 ~ a queen moving into the
    #      enemy king's ring (a "large but not maximal" swing).
    #   3. check/capture flag: 1.0 if the move checks OR captures, else 0.0.
    # volatility is their arithmetic mean -> always in [0, 1].
    material_swing_norm = min(1.0, captured_value / _VOL_MAT_SWING_MAX)
    enemy_pressure_delta = _zone_pressure(board_after, enemy, own_zone_after) - _zone_pressure(
        board_before, enemy, own_zone_before
    )
    king_pressure_swing = abs(king_zone_pressure) + abs(enemy_pressure_delta)
    king_pressure_norm = min(1.0, king_pressure_swing / _VOL_PRESSURE_REF)
    check_capture_flag = 1.0 if (board_after.is_check() or board_before.is_capture(move)) else 0.0
    volatility = (material_swing_norm + king_pressure_norm + check_capture_flag) / 3.0

    # --- aggregate ---------------------------------------------------------
    attack_gain = (
        king_zone_pressure
        + king_adjacent_attacks
        + checks
        + open_lines
        + escape_square_pressure
    )
    defense_gain = (
        enemy_pressure_reduction
        + king_zone_defense
        + line_blocking
        + pawn_shield
        + king_mobility
    )

    attack_sub = AttackGainSub(
        king_zone_pressure=king_zone_pressure,
        king_adjacent_attacks=king_adjacent_attacks,
        checks=checks,
        open_lines=open_lines,
        escape_square_pressure=escape_square_pressure,
    )
    defense_sub = DefenseGainSub(
        enemy_pressure_reduction=enemy_pressure_reduction,
        king_zone_defense=king_zone_defense,
        line_blocking=line_blocking,
        pawn_shield=pawn_shield,
        king_mobility=king_mobility,
    )

    scores = StyleScores(
        attack_gain=attack_gain,
        defense_gain=defense_gain,
        sacrifice_signal=sacrifice_signal,
        volatility=volatility,
        initiative_proxy=0.0,
        attack_sub=attack_sub,
        defense_sub=defense_sub,
    )

    debug = {
        "move_uci": move.uci(),
        "move_san": board_before.san(move),
        "material_before": _material_for_color(board_before, mover),
        "material_after": _material_for_color(board_after, mover),
        "attack": {
            "king_zone_pressure": king_zone_pressure,
            "king_adjacent_attacks": king_adjacent_attacks,
            "checks": checks,
            "open_lines": open_lines,
            "escape_square_pressure": escape_square_pressure,
        },
        "defense": {
            "enemy_pressure_reduction": enemy_pressure_reduction,
            "king_zone_defense": king_zone_defense,
            "line_blocking": line_blocking,
            "pawn_shield": pawn_shield,
            "king_mobility": king_mobility,
        },
        "sacrifice": {
            "hung_value": hung_value,
            "captured_value": captured_value,
            "concession": concession,
            "signal": sacrifice_signal,
        },
        "volatility": {
            "material_swing_norm": material_swing_norm,
            "king_pressure_swing": king_pressure_swing,
            "king_pressure_norm": king_pressure_norm,
            "check_capture_flag": check_capture_flag,
        },
    }

    return scores, debug
