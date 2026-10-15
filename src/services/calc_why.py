""""Show why": engine-derived consequence of a wrong solver move.

Only the first divergence being a SOLVER move triggers a search; a
defender-reply divergence returns defender_misjudged with no engine call
(the stored reply is the verified best defense).

Score convention: every eval is from the USER's side POV (the solver, i.e.
the side to move before W). Engine output after W is natively the
opponent's POV and is flipped on the way in.
"""

from __future__ import annotations

import logging

import chess

from services.calc_grading import exercise_line_tokens

log = logging.getLogger(__name__)

# Classification constants, ONE place.
WHY_MATE_PV_PLIES = 10       # mate/draw must land within this many plies
WHY_MATERIAL_DROP = -2       # material-loss trigger (standard 1/3/3/5/9)
WHY_MATERIAL_WINDOW = 8      # earliest-drop search horizon (plies)
WHY_RECOVER_PLIES = 2        # no recovery by t+2 confirms the loss
WHY_HIGH_WINPROB = 0.8       # "winning" baseline for stalemate tricks
WHY_ALSO_FINE_DROP = 0.05    # win-prob drop below this keeps the win
WHY_FALLBACK_PLIES = 6       # win_slips truncation
WHY_PV_WALK_CAP = 16         # safety cap when walking long PVs

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                chess.ROOK: 5, chess.QUEEN: 9}


def material_balance(board: chess.Board, side: chess.Color) -> int:
    """Side's material minus the opponent's (standard values)."""
    total = 0
    for piece_type, value in PIECE_VALUES.items():
        total += value * (len(board.pieces(piece_type, side))
                          - len(board.pieces(piece_type, not side)))
    return total


def _eval(cp, mate, win_prob) -> dict:
    return {"cp": cp, "mate": mate, "win_prob": win_prob}


def derive_inputs(segment_fen: str, line_uci: str, offset: int, n: int,
                  submitted_line: str, ply: int,
                  mode: str = "lines") -> dict:
    """Position before W + W itself, mapped per submission mode.

    lines: the full interleaved line was submitted, W = submitted[ply].
    premove: only solver moves were submitted, W = the k-th submitted
      solver move with k = ply // 2 (ply is always a solver ply here —
      defender divergences never reach the search).
    Raises ValueError when the window is out of range, the index is outside
    the submitted line, the ply/mode combination is incoherent, or W is not
    legal in its position.
    """
    exercise_line_tokens(line_uci, offset, n)  # bounds-check the window
    board = chess.Board(segment_fen)
    for uci in line_uci.split()[:2 * offset + ply]:
        board.push(chess.Move.from_uci(uci))
    fen_before = board.fen()
    toks = submitted_line.split()
    if mode == "premove":
        if ply % 2 != 0:
            raise ValueError("premove mode has no defender-ply submissions")
        k = ply // 2
        if k < 0 or k >= len(toks):
            raise ValueError("solver-move index outside submitted line")
        w = chess.Move.from_uci(toks[k])
    elif mode == "lines":
        if ply < 0 or ply >= len(toks):
            raise ValueError("divergence ply outside submitted line")
        w = chess.Move.from_uci(toks[ply])
    else:
        raise ValueError(f"unknown submission mode {mode!r}")
    if w not in board.legal_moves:
        raise ValueError("submitted move illegal in its position")
    board.push(w)
    return {"fen_before": fen_before, "fen_after": board.fen(),
            "w_uci": w.uci(), "user_side": chess.Board(fen_before).turn}


def classify(fen_before: str, fen_after: str, w_uci: str,
             baseline: dict, reply: dict, nodes: int) -> dict:
    """Classify the consequence. baseline/reply are single-PV analyses
    (side-to-move POV each); reply is flipped to user POV inside."""
    user_board = chess.Board(fen_before)
    user_side = user_board.turn
    ev_before = _eval(baseline["cp"], baseline["mate"],
                       baseline["win_prob"])
    # Flip opponent-POV reply scores to the user's side.
    r_cp = None if reply["cp"] is None else -reply["cp"]
    r_mate = None if reply["mate"] is None else -reply["mate"]
    r_wp = None
    if reply["win_prob"] is not None:
        r_wp = 1.0 - reply["win_prob"]
    ev_after = _eval(r_cp, r_mate, r_wp)
    params: dict = {"nodes": nodes,
                    "mate_plies": WHY_MATE_PV_PLIES,
                    "material_drop": WHY_MATERIAL_DROP,
                    "high_winprob": WHY_HIGH_WINPROB,
                    "also_fine_drop": WHY_ALSO_FINE_DROP}

    pv = (reply.get("pv") or [])[:]
    # Walk the PV, recording positions after each ply (cap for safety).
    positions: list[chess.Board] = []
    walker = chess.Board(fen_after)
    for uci in pv[:WHY_PV_WALK_CAP]:
        try:
            walker.push(chess.Move.from_uci(uci))
        except ValueError:
            break
        positions.append(walker.copy())

    def terminal_kind(board: chess.Board) -> str | None:
        if board.is_stalemate():
            return "stalemate"
        if board.is_insufficient_material():
            return "insufficient_material"
        if board.is_repetition(3):
            return "threefold_repetition"
        return None

    # mate_against: PV mates the user's side within 10 plies.
    if r_mate is not None and r_mate < 0:
        for t, pos in enumerate(positions[:WHY_MATE_PV_PLIES], 1):
            if pos.is_checkmate():
                dist_moves = (t + 1) // 2
                line = [w_uci] + pv[:t]
                return {"type": "mate_against",
                        "headline": f"Your move allows mate in {dist_moves}.",
                        "line_uci": line, "eval_before": ev_before,
                        "eval_after": ev_after, "truncated_at_ply": t,
                        "params": {**params,
                                   "mate_distance_moves": dist_moves}}
    # stalemate_or_draw: was winning (>=0.8), PV ends drawn within 10.
    # t=0: W itself leaves a dead-drawn position (e.g. Qxb7+ Kxb7 leaves
    # bare kings — insufficient immediately; repetition needs history the
    # walked stack does not have, so only stalemate/insufficient apply).
    if (ev_before["win_prob"] is not None
            and ev_before["win_prob"] >= WHY_HIGH_WINPROB):
        immediate = terminal_kind(chess.Board(fen_after))
        if immediate in ("stalemate", "insufficient_material"):
            label = immediate.replace("_", " ")
            return {"type": "stalemate_or_draw",
                    "headline": f"Your move leaves only a draw by {label}.",
                    "line_uci": [w_uci], "eval_before": ev_before,
                    "eval_after": ev_after, "truncated_at_ply": 0,
                    "params": {**params, "terminal": immediate}}
        for t, pos in enumerate(positions[:WHY_MATE_PV_PLIES], 1):
            kind = terminal_kind(pos)
            if kind is not None:
                line = [w_uci] + pv[:t]
                label = kind.replace("_", " ")
                return {"type": "stalemate_or_draw",
                        "headline": f"A winning position collapses into a"
                                    f" draw by {label}.",
                        "line_uci": line, "eval_before": ev_before,
                        "eval_after": ev_after, "truncated_at_ply": t,
                        "params": {**params, "terminal": kind}}
    # material_loss: earliest ply t<=8 with balance <= -2 vs after-W and no
    # recovery by t+2.
    base_bal = material_balance(chess.Board(fen_after), user_side)
    max_t = min(WHY_MATERIAL_WINDOW, len(positions) - WHY_RECOVER_PLIES)
    for t in range(1, max_t + 1):
        drop = (material_balance(positions[t - 1], user_side) - base_bal)
        if drop <= WHY_MATERIAL_DROP and all(
                material_balance(positions[j - 1], user_side) - base_bal
                <= WHY_MATERIAL_DROP
                for j in (t + 1, t + 2)):
            line = [w_uci] + pv[:t]
            return {"type": "material_loss",
                    "headline": f"Material drops by {-drop} points with no"
                                f" recovery.",
                    "line_uci": line, "eval_before": ev_before,
                    "eval_after": ev_after, "truncated_at_ply": t,
                    "params": {**params, "material_delta": drop,
                               "at_ply": t}}
    # slower_win: still mates, but longer.
    if (ev_before["mate"] is not None and ev_before["mate"] > 0
            and r_mate is not None and r_mate > 0
            and abs(r_mate) > abs(ev_before["mate"])):
        line = [w_uci] + pv[:WHY_MATE_PV_PLIES]
        return {"type": "slower_win",
                "headline": f"Still mate, but longer: mate in"
                            f" {ev_before['mate']} becomes mate in {r_mate}.",
                "line_uci": line, "eval_before": ev_before,
                "eval_after": ev_after,
                "truncated_at_ply": len(line) - 1,
                "params": {**params, "mate_before": ev_before["mate"],
                           "mate_after": r_mate}}
    # also_fine: win probability barely moves.
    if (ev_before["win_prob"] is not None and r_wp is not None
            and ev_before["win_prob"] - r_wp < WHY_ALSO_FINE_DROP):
        log.info("why also_fine: %s winprob %.3f -> %.3f",
                 w_uci, ev_before["win_prob"], r_wp)
        return {"type": "also_fine",
                "headline": f"The win is kept (win probability"
                            f" {ev_before['win_prob']:.2f} → {r_wp:.2f}).",
                "line_uci": [], "eval_before": ev_before,
                "eval_after": ev_after, "truncated_at_ply": None,
                "params": {**params,
                           "winprob_before": ev_before["win_prob"],
                           "winprob_after": r_wp}}
    # win_slips fallback.
    line = [w_uci] + pv[:WHY_FALLBACK_PLIES]
    before_s = (f"{ev_before['win_prob']:.2f}"
                if ev_before["win_prob"] is not None else "unknown")
    after_s = f"{r_wp:.2f}" if r_wp is not None else "unknown"
    return {"type": "win_slips",
            "headline": f"Winning chances fall from {before_s} to {after_s}.",
            "line_uci": line, "eval_before": ev_before,
            "eval_after": ev_after, "truncated_at_ply": len(line) - 1,
            "params": {**params, "winprob_before": ev_before["win_prob"],
                       "winprob_after": r_wp}}
