"""SQLite output + hand-review sheet. Portable SQL types only (TEXT/INTEGER/REAL)."""

from __future__ import annotations

import random
import sqlite3
from typing import Any, Dict, List

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    stockfish_path TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    engine_sha256 TEXT NOT NULL DEFAULT '',
    allow_other_engine INTEGER NOT NULL DEFAULT 0,
    settings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    url TEXT NOT NULL,
    user_color TEXT NOT NULL,
    variant TEXT NOT NULL,
    time_class TEXT NOT NULL,
    result TEXT NOT NULL,
    white_name TEXT NOT NULL,
    black_name TEXT NOT NULL,
    user_rating INTEGER NOT NULL,
    n_plies INTEGER NOT NULL,
    n_user_plies INTEGER NOT NULL,
    n_non_user INTEGER NOT NULL,
    clk_plies INTEGER NOT NULL,
    screen_ms REAL NOT NULL,
    verify_ms REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    ply_index INTEGER NOT NULL,
    move_number INTEGER NOT NULL,
    fen_before TEXT NOT NULL,
    fen_after TEXT NOT NULL,
    position_key TEXT NOT NULL,
    played_san TEXT NOT NULL,
    played_uci TEXT NOT NULL,
    screen_best_san TEXT NOT NULL DEFAULT '',
    screen_best_uci TEXT NOT NULL DEFAULT '',
    screen_second_san TEXT NOT NULL DEFAULT '',
    screen_second_uci TEXT NOT NULL DEFAULT '',
    screen_best_pv_uci TEXT NOT NULL DEFAULT '',
    screen_ep_loss REAL NOT NULL,
    screen_cp_loss INTEGER NOT NULL,
    screen_ep_best REAL NOT NULL,
    screen_eval_cp_mover REAL NOT NULL,
    classification TEXT NOT NULL DEFAULT '',
    player_rating INTEGER NOT NULL,
    is_book INTEGER NOT NULL DEFAULT 0,
    exclusion TEXT NOT NULL DEFAULT '',
    clk_seconds REAL,
    base_time_s REAL,
    mode TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS verify (
    ply_id INTEGER PRIMARY KEY,
    stage2_best_uci TEXT NOT NULL DEFAULT '',
    stage2_best_san TEXT NOT NULL DEFAULT '',
    stage2_best_cp REAL,
    stage2_best_mate INTEGER,
    stage2_second_uci TEXT NOT NULL DEFAULT '',
    stage2_second_cp REAL,
    stage2_second_mate INTEGER,
    stage2_pv_uci TEXT NOT NULL DEFAULT '',
    stage2_pv_san TEXT NOT NULL DEFAULT '',
    played_cp_verified REAL,
    ep_best_verified REAL NOT NULL,
    ep_played_verified REAL NOT NULL,
    ep_loss_verified REAL NOT NULL,
    cp_loss_verified INTEGER NOT NULL,
    margin_ep REAL,
    legal_count INTEGER NOT NULL,
    best_agrees INTEGER NOT NULL,
    stage2_ms REAL NOT NULL,
    reject TEXT NOT NULL DEFAULT '',
    kept INTEGER NOT NULL DEFAULT 0,
    best_is_capture INTEGER NOT NULL DEFAULT 0,
    best_is_check INTEGER NOT NULL DEFAULT 0,
    best_is_mate INTEGER NOT NULL DEFAULT 0,
    best_is_promotion INTEGER NOT NULL DEFAULT 0,
    best_is_quiet INTEGER NOT NULL DEFAULT 0,
    pv_mat_best INTEGER,
    pv_mat_played INTEGER
);
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    # Pilot schema still evolves: old output files keep their stale tables
    # (CREATE TABLE IF NOT EXISTS never adds columns). Refuse loudly instead
    # of crashing mid-run on a missing column.
    need = {
        "runs": {"stockfish_path", "engine_sha256"},
        "plies": {"base_time_s"},
        "verify": {"best_is_capture", "pv_mat_best", "pv_mat_played"},
    }
    for table, cols in need.items():
        have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}
        missing = cols - have
        if missing:
            con.close()
            raise RuntimeError(
                "stale pilot schema in %s: table %s lacks %s; "
                "delete the file and rerun" % (path, table, sorted(missing))
            )
    return con


def write_sheet(
    path: str,
    *,
    report_lines: List[str],
    kept: List[Dict[str, Any]],
    rejected: List[Dict[str, Any]],
    seed: int,
) -> None:
    """Write review_sheet.md: report + kept entries + 10 random rejects."""
    rng = random.Random(seed)
    sample = rng.sample(rejected, min(10, len(rejected)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Puzzle-extract pilot -- hand-review sheet\n\n")
        f.write("".join(report_lines))
        f.write("\n---\n\n## Kept puzzles (%d)\n" % len(kept))
        for i, p in enumerate(kept, 1):
            f.write(
                "\n### P%d: %s to move, play %s (game: %s, move %s)\n"
                % (i, p["side_to_move"], p["best_san"], p["url"], p["move_number"])
            )
            f.write("- fen: `%s`\n" % p["fen_before"])
            f.write("- side to move: %s\n" % p["side_to_move"])
            f.write("- my played move: %s (%s)\n" % (p["played_san"], p["played_uci"]))
            f.write("- best move: %s (%s)\n" % (p["best_san"], p["best_uci"]))
            f.write("- PV (explanation only, not graded): %s\n" % (p["pv_san"] or "(none)"))
            f.write(
                "- win-prob before: %.4f after (verified): %.4f\n"
                % (p["ep_best_verified"], p["ep_played_verified"])
            )
            f.write(
                "- screen ep_loss/cp_loss: %.4f / %d; verified: %.4f / %d\n"
                % (
                    p["screen_ep_loss"],
                    p["screen_cp_loss"],
                    p["ep_loss_verified"],
                    p["cp_loss_verified"],
                )
            )
            f.write(
                "- uniqueness margin (EP best-second): %s\n" % _fmt_margin(p["margin_ep"])
            )
            f.write(
                "- best character: %s; pv material Δ (best/played, cp): %s / %s\n"
                % (p.get("best_char") or "?", _fmt_mat(p.get("pv_mat_best")),
                   _fmt_mat(p.get("pv_mat_played")))
            )
            f.write("- game URL: %s ply %d\n" % (p["url"], p["ply_index"]))
            f.write("- analysis: %s\n" % analysis_url(p["fen_before"]))
        f.write("\n---\n\n## Rejected candidates (random %d of %d, seed=%d)\n" % (len(sample), len(rejected), seed))
        for i, r in enumerate(sample, 1):
            f.write(
                "\n### R%d: %s (game: %s, move %s, played %s, ep_loss %.4f)\n"
                % (
                    i,
                    r["reject"],
                    r["url"],
                    r["move_number"],
                    r["played_san"],
                    r["screen_ep_loss"],
                )
            )
            f.write("- fen: `%s`\n" % r["fen_before"])
            f.write(
                "- screen best: %s (%s); verified best: %s\n"
                % (r["screen_best_san"], r["screen_best_uci"], r["stage2_best_uci"] or "(none)")
            )
            f.write("- rejection reason: %s\n" % r["reject"])
            f.write("- game URL: %s ply %s\n" % (r["url"], r.get("ply_index", "?")))
            f.write("- analysis: %s\n" % analysis_url(r["fen_before"]))


def analysis_url(fen_before: str) -> str:
    """Lichess analysis link for a FEN (spaces -> underscores)."""
    return "https://lichess.org/analysis/%s" % (fen_before or "").replace(" ", "_")


def _fmt_margin(m) -> str:
    if m is None:
        return "(mate / n/a)"
    return "%.4f" % m


def _fmt_mat(v) -> str:
    if v is None:
        return "?"
    return "%+d" % v
