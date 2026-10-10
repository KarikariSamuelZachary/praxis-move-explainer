"""Read-only sheet rebuild from stored pilot data. No engine, no fetch.

Reads extract_pilot.sqlite and writes a new review sheet:
  - all kept puzzles
  - ALL rejects with verified EP margin in [0.05, 0.10)
  - 3 random other rejects (seeded)
  - up to 8 clock-excluded + up to 5 book_fallback-excluded user plies that
    passed the candidate screen (top by screen ep_loss, labelled, no Stage 2)

Usage:
    python scripts/puzzle_extract_pilot/rebuild_sheet.py \
        --db scripts/puzzle_extract_pilot/extract_pilot.sqlite \
        --sheet scripts/puzzle_extract_pilot/review_sheet_v2.md [--seed 20261008]
"""

from __future__ import annotations

import argparse
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
from extract import best_move_flags, char_label, pv_material_delta  # noqa: E402
from store import analysis_url  # noqa: E402


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--max-clock", type=int, default=8)
    ap.add_argument("--max-bookfb", type=int, default=5)
    ap.add_argument("--n-other-rejects", type=int, default=3)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    con.row_factory = sqlite3.Row

    kept = con.execute(
        "SELECT v.*, p.fen_before, p.played_san, p.played_uci, p.move_number,"
        " p.ply_index, p.screen_ep_loss, p.screen_cp_loss, p.player_rating,"
        " g.url FROM verify v JOIN plies p ON p.id=v.ply_id "
        "JOIN games g ON g.id=p.game_id WHERE v.kept=1 "
        "ORDER BY v.ep_loss_verified DESC"
    ).fetchall()
    near = con.execute(
        "SELECT v.*, p.fen_before, p.played_san, p.played_uci, p.move_number,"
        " p.ply_index, p.screen_ep_loss, p.screen_cp_loss, p.player_rating,"
        " g.url FROM verify v JOIN plies p ON p.id=v.ply_id "
        "JOIN games g ON g.id=p.game_id WHERE v.kept=0 AND v.margin_ep>=0.05 "
        "AND v.margin_ep<0.10 ORDER BY v.margin_ep DESC"
    ).fetchall()
    other = con.execute(
        "SELECT v.*, p.fen_before, p.played_san, p.played_uci, p.move_number,"
        " p.ply_index, p.screen_ep_loss, p.screen_cp_loss, p.player_rating,"
        " g.url FROM verify v JOIN plies p ON p.id=v.ply_id "
        "JOIN games g ON g.id=p.game_id WHERE v.kept=0 AND NOT "
        "(v.margin_ep>=0.05 AND v.margin_ep<0.10) ORDER BY v.ply_id"
    ).fetchall()
    rng = random.Random(args.seed)
    other_sample = rng.sample(other, min(args.n_other_rejects, len(other)))

    clock_ex = con.execute(
        "SELECT p.*, g.url FROM plies p JOIN games g ON g.id=p.game_id "
        "WHERE p.exclusion='clock' AND p.screen_ep_loss>=0.15 "
        "AND p.screen_cp_loss>=100 ORDER BY p.screen_ep_loss DESC LIMIT ?",
        (args.max_clock,),
    ).fetchall()
    book_ex = con.execute(
        "SELECT p.*, g.url FROM plies p JOIN games g ON g.id=p.game_id "
        "WHERE p.exclusion='book_fallback' AND p.screen_ep_loss>=0.15 "
        "AND p.screen_cp_loss>=100 ORDER BY p.screen_ep_loss DESC LIMIT ?",
        (args.max_bookfb,),
    ).fetchall()
    settings = con.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
    con.close()

    L: List[str] = []
    L.append("# Puzzle-extract pilot -- hand-review sheet (rebuilt read-only)\n\n")
    if settings:
        L.append("Source DB settings: `%s`\n\n" % settings[0][:400])
    L.append("Kept: %d; near-miss rejects [0.05,0.10): %d; other rejects sampled: %d; "
             "clock-excluded screen-passing: %d; book_fallback-excluded screen-passing: %d.\n" % (
                 len(kept), len(near), len(other_sample), len(clock_ex), len(book_ex)))

    L.append("\n---\n\n## Kept puzzles (%d)\n" % len(kept))
    for i, v in enumerate(kept, 1):
        L.append(entry_verified(i, "P", v))

    L.append("\n---\n\n## Near-miss rejects, margin in [0.05, 0.10) (%d)\n" % len(near))
    for i, v in enumerate(near, 1):
        L.append(entry_verified(i, "N", v))

    L.append("\n---\n\n## Other rejects sample (%d, seed=%d)\n" % (len(other_sample), args.seed))
    for i, v in enumerate(other_sample, 1):
        L.append(entry_verified(i, "R", v))

    L.append("\n---\n\n## Clock-excluded, passed candidate screen (%d, no Stage 2)\n" % len(clock_ex))
    for i, r in enumerate(clock_ex, 1):
        L.append(entry_excluded(i, "C", r))

    L.append("\n---\n\n## Book_fallback-excluded, passed candidate screen (%d, no Stage 2)\n" % len(book_ex))
    for i, r in enumerate(book_ex, 1):
        L.append(entry_excluded(i, "B", r))

    with open(args.sheet, "w", encoding="utf-8") as f:
        f.write("".join(L))
    print("kept=%d near=%d other=%d clock_ex=%d book_ex=%d sheet=%s" % (
        len(kept), len(near), len(other_sample), len(clock_ex), len(book_ex), args.sheet))
    return 0


def fmt_margin(m) -> str:
    return "(none)" if m is None else "%.4f" % m


def fmt_mat(v) -> str:
    return "?" if v is None else "%+d" % v


def char_of_verified(v) -> str:
    flags = {
        "capture": v["best_is_capture"] or 0, "check": v["best_is_check"] or 0,
        "mate": v["best_is_mate"] or 0, "promotion": v["best_is_promotion"] or 0,
        "quiet": v["best_is_quiet"] or 0,
    }
    return char_label(flags)


def entry_verified(i: int, tag: str, v) -> str:
    return (
        "\n### %s%d: played %s, best %s (game: %s, move %s, ply %s)\n"
        "- fen: `%s`\n- analysis: %s\n- game URL: %s ply %s\n"
        "- screen ep_loss/cp_loss: %.4f / %d; verified: %.4f / %d\n"
        "- margin: %s; best character: %s; pv material Δ (best/played): %s / %s\n"
        "- my rating: %d; reject: %s\n" % (
            tag, i, v["played_san"], v["stage2_best_san"], v["url"],
            v["move_number"], v["ply_index"], v["fen_before"],
            analysis_url(v["fen_before"]), v["url"], v["ply_index"],
            v["screen_ep_loss"], v["screen_cp_loss"],
            v["ep_loss_verified"], v["cp_loss_verified"],
            fmt_margin(v["margin_ep"]), char_of_verified(v),
            fmt_mat(v["pv_mat_best"]), fmt_mat(v["pv_mat_played"]),
            v["player_rating"], v["reject"] or "kept",
        )
    )


def entry_excluded(i: int, tag: str, r) -> str:
    try:
        board = chess.Board(r["fen_before"])
    except ValueError:
        board = None
    if board is not None:
        flags = best_move_flags(board, r["screen_best_uci"], False)
        label = char_label(flags)
        pv = (r["screen_best_pv_uci"] or "").split()
        mat = pv_material_delta(r["fen_before"], pv)
    else:
        label, mat = "?", None
    return (
        "\n### %s%d: played %s, screen best %s (game: %s, move %s, ply %s) [%s]\n"
        "- fen: `%s`\n- analysis: %s\n- game URL: %s ply %s\n"
        "- screen ep_loss/cp_loss: %.4f / %d; margin: (none, no Stage 2)\n"
        "- best character (screen): %s; pv material Δ: %s\n"
        "- my rating: %d; exclusion: %s; clock: %ss / base %ss\n" % (
            tag, i, r["played_san"], r["screen_best_san"] or "?",
            r["url"], r["move_number"], r["ply_index"], r["exclusion"],
            r["fen_before"], analysis_url(r["fen_before"]),
            r["url"], r["ply_index"], r["screen_ep_loss"], r["screen_cp_loss"],
            label, fmt_mat(mat), r["player_rating"], r["exclusion"],
            r["clk_seconds"] if r["clk_seconds"] is not None else "?",
            r["base_time_s"] if r["base_time_s"] is not None else "?",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
