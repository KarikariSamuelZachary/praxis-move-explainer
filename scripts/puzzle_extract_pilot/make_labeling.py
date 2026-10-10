"""Build blind labeling materials from stored pilot data. Read-only DB, no engine.

EXACT SUGGESTION RULES (machine-derived, written to labeling_key.json ONLY,
never to the blind sheet or answers):
  suggest_T: best move is a capture AND the captured piece is worth >= 300cp
    (P=100 N=320 B=330 R=500 Q=900) AND the destination square had zero
    opponent attackers before the move (python-chess board query, no engine).
    "N/A" when the best move is missing/illegal (cannot evaluate).
  suggest_X: win probability before the move (EP of the best line) >= 0.85
    or <= 0.15. Uses verified EP where Stage 2 exists, else screen EP.
  suggest_C: clock remaining < 20% of base time. "N/A" when clock or base
    time is missing.
  suggest_A: verified Stage 2 ep_loss < 0.12. "N/A" for entries with no
    Stage 2 data.
  suggest_Q: best move is quiet (not a capture and does not give check) AND
    PV material delta over the first 6 plies < 100cp. Uses Stage-2 PV where
    it exists, else the screen best PV. "N/A" when the PV is empty/missing.

Outputs: labeling_sheet.md (blind), labeling_key.json (key+suggestions),
answers.md (post-attempt checks), labels.csv (empty template).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import chess  # noqa: E402
from extract import pv_material_delta  # noqa: E402
from store import analysis_url  # noqa: E402

SEED = 20261008
VALS = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
        chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
NA = "N/A"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--seed", type=int, default=SEED)
    return ap.parse_args(argv)


def side_of_fen(fen: str) -> str:
    try:
        return "white" if chess.Board(fen).turn == chess.WHITE else "black"
    except ValueError:
        return "?"


def suggest_t(fen: str, best_uci: Optional[str]):
    """Capture of an undefended piece worth >= 300cp. N/A if unevaluable."""
    if not best_uci:
        return NA, "no best move stored"
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(best_uci)
    except ValueError:
        return NA, "bad fen/move"
    if move not in board.legal_moves:
        return NA, "best move illegal in fen"
    if not board.is_capture(move):
        return False, "best is not a capture"
    if board.is_en_passant(move):
        return False, "en passant (100cp < 300)"
    target = board.piece_at(move.to_square)
    gain = VALS.get(target.piece_type, 0) if target else 0
    if gain < 300:
        return False, "captured value %d < 300" % gain
    defended = len(list(board.attackers(not board.turn, move.to_square))) > 0
    if defended:
        return False, "captured piece defended"
    return True, "undefended capture gaining %d" % gain


def main(argv=None) -> int:
    args = parse_args(argv)
    os.makedirs(args.outdir, exist_ok=True)
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    con.row_factory = sqlite3.Row

    rows: List[Dict[str, Any]] = []
    # Verified entries: all kept + ALL near-miss (margin in [0.05, 0.10)) +
    # a fixed 3-sample of the remaining rejects (seeded, ply_id order --
    # same sample as review_sheet_v2.md).
    vq = ("SELECT v.*, p.fen_before, p.played_san, p.played_uci, p.move_number,"
          " p.ply_index, p.screen_ep_loss, p.screen_cp_loss, p.player_rating,"
          " p.clk_seconds, p.base_time_s, g.url FROM verify v "
          "JOIN plies p ON p.id=v.ply_id JOIN games g ON g.id=p.game_id ")
    for v in con.execute(vq + "WHERE v.kept=1 ORDER BY v.ply_id").fetchall():
        rows.append(collect_verified(dict(v)))
    for v in con.execute(vq + "WHERE v.kept=0 AND v.margin_ep>=0.05 "
                              "AND v.margin_ep<0.10 ORDER BY v.ply_id").fetchall():
        rows.append(collect_verified(dict(v)))
    other = con.execute(vq + "WHERE v.kept=0 AND NOT (v.margin_ep>=0.05 "
                             "AND v.margin_ep<0.10) ORDER BY v.ply_id").fetchall()
    for v in random.Random(args.seed).sample(other, min(3, len(other))):
        rows.append(collect_verified(dict(v)))
    # Excluded screen-passers (top by ep per reason, same set as v2 sheet).
    for reason, lim in (("clock", 8), ("book_fallback", 5)):
        for r in con.execute(
            "SELECT p.*, g.url FROM plies p JOIN games g ON g.id=p.game_id "
            "WHERE p.exclusion=? AND p.screen_ep_loss>=0.15 "
            "AND p.screen_cp_loss>=100 ORDER BY p.screen_ep_loss DESC LIMIT ?",
            (reason, lim),
        ).fetchall():
            rows.append(collect_excluded(dict(r), reason))
    con.close()

    rng = random.Random(args.seed)
    order = list(range(len(rows)))
    rng.shuffle(order)

    key: Dict[str, Any] = {"seed": args.seed, "entries": {}}
    sheet: List[str] = ["# Blind labeling sheet (%d positions, seed=%d)\n" % (len(rows), args.seed),
                        "\nAttempt each position, then record keep (K/D), a code and a note in labels.csv.\n"]
    answers: List[str] = ["# Answers (open only AFTER attempting each position)\n"]
    for num, idx in enumerate(order, 1):
        e = rows[idx]
        key["entries"][str(num)] = e
        sheet.append(
            "\n## %d\n- analysis: %s\n- side to move: %s\n- my rating: %s\n- game: %s ply %s\n" % (
                num, analysis_url(e["fen_before"]), e["side_to_move"],
                e["rating"], e["url"], e["ply_index"]))
        answers.append(
            "\n## %d\n- best (%s): %s\n- PV: %s\n- played: %s\n- win prob before/after: %s / %s\n" % (
                num, e["best_source"], e["best_san"] or "?",
                e["pv_san"] or "(none)", e["played_san"],
                fmt_wp(e["ep_best"]), fmt_wp(e["ep_played"])))

    with open(os.path.join(args.outdir, "labeling_sheet.md"), "w") as f:
        f.write("".join(sheet))
    with open(os.path.join(args.outdir, "labeling_key.json"), "w") as f:
        json.dump(key, f, indent=1)
    with open(os.path.join(args.outdir, "answers.md"), "w") as f:
        f.write("".join(answers))
    with open(os.path.join(args.outdir, "labels.csv"), "w") as f:
        f.write("entry,keep,code,note\n")
    print("entries=%d sheet=%s key=%s answers=%s labels=%s" % (
        len(rows), "labeling_sheet.md", "labeling_key.json", "answers.md", "labels.csv"))
    return 0


def fmt_wp(v) -> str:
    return "N/A" if v is None else "%.4f" % v


def base_entry(url, fen, ply_index, move_number, played_san, rating) -> Dict[str, Any]:
    return {
        "url": url, "fen_before": fen, "side_to_move": side_of_fen(fen),
        "ply_index": ply_index, "move_number": move_number,
        "played_san": played_san, "rating": rating,
    }


def collect_verified(v: Dict[str, Any]) -> Dict[str, Any]:
    e = base_entry(v["url"], v["fen_before"], v["ply_index"], v["move_number"],
                   v["played_san"], v["player_rating"])
    e.update({
        "group": "kept" if v["kept"] else ("near" if v["margin_ep"] is not None and 0.05 <= v["margin_ep"] < 0.10 else "reject"),
        "origin": "verify", "ply_id": v["ply_id"],
        "best_source": "stage2", "best_san": v["stage2_best_san"],
        "best_uci": v["stage2_best_uci"], "pv_san": v["stage2_pv_san"],
        "ep_best": v["ep_best_verified"], "ep_played": v["ep_played_verified"],
        "ep_loss": v["ep_loss_verified"], "cp_loss": v["cp_loss_verified"],
        "margin_ep": v["margin_ep"],
        "char": {"capture": v["best_is_capture"] or 0, "check": v["best_is_check"] or 0,
                 "mate": v["best_is_mate"] or 0, "promotion": v["best_is_promotion"] or 0,
                 "quiet": v["best_is_quiet"] or 0},
        "pv_mat_best": v["pv_mat_best"], "pv_mat_played": v["pv_mat_played"],
        "clk_seconds": v["clk_seconds"], "base_time_s": v["base_time_s"],
        "pilot_reject": v["reject"] or "kept",
    })
    e["suggestions"] = {
        "suggest_T": _t(v["fen_before"], v["stage2_best_uci"]),
        "suggest_X": _x(v["ep_best_verified"]),
        "suggest_C": _c(v["clk_seconds"], v["base_time_s"]),
        "suggest_A": _a(True, v["ep_loss_verified"]),
        "suggest_Q": _q(bool(v["best_is_quiet"]), v["pv_mat_best"]),
    }
    return e


def collect_excluded(r: Dict[str, Any], reason: str) -> Dict[str, Any]:
    e = base_entry(r["url"], r["fen_before"], r["ply_index"], r["move_number"],
                   r["played_san"], r["player_rating"])
    pv = (r["screen_best_pv_uci"] or "").split()
    ep_played = r["screen_ep_best"] - r["screen_ep_loss"]
    e.update({
        "group": "clock_ex" if reason == "clock" else "book_ex",
        "origin": "plies", "ply_id": r["id"],
        "best_source": "screen", "best_san": r["screen_best_san"],
        "best_uci": r["screen_best_uci"], "pv_san": None,
        "ep_best": r["screen_ep_best"], "ep_played": ep_played,
        "ep_loss": r["screen_ep_loss"], "cp_loss": r["screen_cp_loss"],
        "margin_ep": None,
        "char": screen_char(r["fen_before"], r["screen_best_uci"]),
        "pv_mat_best": pv_material_delta(r["fen_before"], pv),
        "pv_mat_played": None,
        "clk_seconds": r["clk_seconds"], "base_time_s": r["base_time_s"],
        "pilot_reject": "excluded:" + reason,
    })
    e["suggestions"] = {
        "suggest_T": _t(r["fen_before"], r["screen_best_uci"]),
        "suggest_X": _x(r["screen_ep_best"]),
        "suggest_C": _c(r["clk_seconds"], r["base_time_s"]),
        "suggest_A": _a(False, None),
        "suggest_Q": _q(bool(e["char"]["quiet"]), e["pv_mat_best"]),
    }
    return e


def screen_char(fen: str, uci: Optional[str]) -> Dict[str, int]:
    flags = {"capture": 0, "check": 0, "mate": 0, "promotion": 0, "quiet": 0}
    if not uci:
        return flags
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
    except ValueError:
        return flags
    if move not in board.legal_moves:
        return flags
    probe = board.copy()
    flags["capture"] = 1 if probe.is_capture(move) else 0
    flags["promotion"] = 1 if move.promotion else 0
    probe.push(move)
    flags["check"] = 1 if probe.is_check() else 0
    if not flags["capture"] and not flags["check"]:
        flags["quiet"] = 1
    return flags


def _t(fen, uci):
    val, why = suggest_t(fen, uci)
    return {"value": val, "why": why}


def _x(ep_best):
    if ep_best is None:
        return {"value": NA, "why": "no EP stored"}
    hit = ep_best >= 0.85 or ep_best <= 0.15
    return {"value": hit, "why": "ep_best=%.4f" % ep_best}


def _c(clk, base):
    if clk is None or base is None:
        return {"value": NA, "why": "clock/base missing"}
    return {"value": clk < 0.20 * base, "why": "%.1fs vs 20%% of %.0fs" % (clk, base)}


def _a(has_stage2: bool, ep_loss):
    if not has_stage2:
        return {"value": NA, "why": "no Stage 2 data"}
    return {"value": ep_loss < 0.12, "why": "ep_loss=%.4f" % ep_loss}


def _q(quiet: bool, pv_mat):
    if pv_mat is None:
        return {"value": NA, "why": "PV empty/missing"}
    return {"value": bool(quiet) and pv_mat < 100, "why": "quiet=%s pv_mat=%s" % (quiet, pv_mat)}


if __name__ == "__main__":
    raise SystemExit(main())
