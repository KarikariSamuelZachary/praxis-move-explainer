"""Verdict logic + segment building. Pure functions (no engine) so threshold
sensitivity can be recomputed from stored per-ply data without re-running Stockfish.

Score convention: every score is from the DEFENDER's point of view at a
defender-to-move position P_i.
  - cp > 0  : good for the defender.
  - mate > 0: defender mates (stored line is bad -> "defender_mates").
  - mate < 0: defender gets mated; |mate| = mate distance.
"""

from __future__ import annotations

import chess

# ---------------------------------------------------------------------------
# MultiPV result normalisation
#
# A stage result is a list of entries, one per PV, ordered best-first:
#   {"move": uci, "cp": int|None, "mate": int|None (defender POV, plies... no,
#    in moves), "wdl_w": int|None, "wdl_d": int|None, "wdl_l": int|None}
# ---------------------------------------------------------------------------


def judge_stage(stored_uci: str, pvs: list[dict], threshold_cp: int) -> dict:
    """Judge one defender reply against one MultiPV result.

    Returns {"verdict": clean|disagree|defender_mates, "reason": str,
             "margin_cp": int|None, "mate_involved": 0|1}.
    margin_cp is best_cp - second_cp for non-mate best lines (None for mates).
    """
    if not pvs:
        return {"verdict": "fail", "reason": "no_pvs",
                "margin_cp": None, "mate_involved": 0}
    best, second = pvs[0], (pvs[1] if len(pvs) > 1 else None)
    if stored_uci != best["move"]:
        return {"verdict": "fail", "reason": "disagree",
                "margin_cp": None, "mate_involved": 0}
    if best["mate"] is not None and best["mate"] > 0:
        # Best line mates FOR the defender: the stored puzzle line is bad.
        return {"verdict": "fail", "reason": "defender_mates",
                "margin_cp": None, "mate_involved": 0}
    if best["mate"] is not None and best["mate"] < 0:
        # Best line is mate AGAINST the defender: clean only if the second
        # line is also mate-against with a strictly SHORTER distance, i.e.
        # the stored reply is the unique longest resistance. Ties fail.
        if (second is not None and second["mate"] is not None
                and second["mate"] < 0
                and abs(second["mate"]) < abs(best["mate"])):
            return {"verdict": "clean", "reason": "unique_longest_resistance",
                    "margin_cp": None, "mate_involved": 1}
        return {"verdict": "fail", "reason": "mate_tie_or_unforced",
                "margin_cp": None, "mate_involved": 1}
    # Best line is a plain cp score.
    if second is not None and second["mate"] is not None and second["mate"] < 0:
        # Second line mates against the defender while the best does not.
        # Per spec this counts as clean (a forced mate behind a quiet best
        # is not a reasonable alternative defence).
        return {"verdict": "clean", "reason": "second_mates_against",
                "margin_cp": None, "mate_involved": 0}
    if second is None or second["cp"] is None or best["cp"] is None:
        return {"verdict": "fail", "reason": "no_second_line",
                "margin_cp": None, "mate_involved": 0}
    margin = best["cp"] - second["cp"]
    if margin >= threshold_cp:
        return {"verdict": "clean", "reason": f"margin_ge_{threshold_cp}",
                "margin_cp": margin, "mate_involved": 0}
    return {"verdict": "fail", "reason": "margin_lt_threshold",
            "margin_cp": margin, "mate_involved": 0}


def recompute_final(single_legal: bool, stage1: dict, stage2: dict | None,
                    threshold_cp: int) -> tuple[str, int | None, int]:
    """Recompute (final_verdict, margin_cp, mate_involved) at any threshold.

    stage1/stage2 are the stored raw JSON dicts {"stored": uci, "pvs": [...]};
    stage2 may be None. Stage 2 decides wherever it ran; otherwise Stage 1.
    """
    if single_legal:
        return "clean", None, 0
    stage = stage2 if stage2 is not None else stage1
    j = judge_stage(stage["stored"], stage["pvs"], threshold_cp)
    return j["verdict"], j["margin_cp"], j["mate_involved"]


def wdl_winrate(w: int | None, d: int | None, opp: int) -> float | None:
    """Win-rate expectancy from a WDL triple (permille)."""
    if w is None or d is None:
        return None
    return (w + d / 2.0) / 1000.0


def wdl_margin(pvs: list[dict]) -> float | None:
    """best-vs-second WDL win-rate margin, defender POV. None if unavailable."""
    if len(pvs) < 2:
        return None
    b, s = pvs[0], pvs[1]
    bw = wdl_winrate(b.get("wdl_w"), b.get("wdl_d"), 0)
    sw = wdl_winrate(s.get("wdl_w"), s.get("wdl_d"), 0)
    if bw is None or sw is None:
        return None
    return bw - sw


# ---------------------------------------------------------------------------
# Segment building
# ---------------------------------------------------------------------------

def build_segments(solver_moves: list[str], defender_moves: list[str],
                   clean_flags: list[bool]) -> list[dict]:
    """Maximal runs s_a..s_b (length >= 2) with every reply d_a..d_(b-1) clean.

    clean_flags[i] is the final verdict for d_i. Returns
    [{"start": a, "end": b}] with solver-move indices (inclusive).
    """
    n = len(solver_moves)
    assert len(defender_moves) == n - 1, "line must end on a solver move"
    assert len(clean_flags) == n - 1
    segments: list[dict] = []
    a: int | None = None
    for i in range(n - 1):
        if clean_flags[i]:
            if a is None:
                a = i
        else:
            if a is not None:
                if i - a + 1 >= 2:
                    segments.append({"start": a, "end": i})
                a = None
    if a is not None and (n - 1) - a + 1 >= 2:
        segments.append({"start": a, "end": n - 1})
    return segments


def segment_line_uci(solver_moves: list[str], defender_moves: list[str],
                     start: int, end: int) -> str:
    """Full interleaved solver/defender line for s_start..s_end, ending on a
    solver move (the reply after the last solver move is NOT included)."""
    toks: list[str] = []
    for i in range(start, end + 1):
        toks.append(solver_moves[i])
        if i < end:
            toks.append(defender_moves[i])
    return " ".join(toks)


def classify_solver_move(fen_before: str, uci: str) -> dict:
    """Buckets for one solver move: check / capture (incl. en passant) /
    quiet / promotion (promotion counted in addition to its bucket)."""
    board = chess.Board(fen_before)
    mv = chess.Move.from_uci(uci)
    is_capture = board.is_capture(mv)
    board.push(mv)
    return {
        "check": 1 if board.is_check() else 0,
        "capture": 1 if is_capture else 0,
        "quiet": 0 if (is_capture or board.is_check()) else 1,
        "promotion": 1 if mv.promotion else 0,
    }
