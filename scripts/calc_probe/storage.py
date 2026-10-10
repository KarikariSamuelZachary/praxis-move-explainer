"""SQLite storage for the calc probe. Portable types only (TEXT/INTEGER/REAL)
so the schema ports to Postgres later. JSON blobs stored as TEXT."""

from __future__ import annotations

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS probe_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    engine_sha256 TEXT,
    settings_json TEXT NOT NULL,
    seed INTEGER NOT NULL,
    git_commit TEXT,
    wall_s REAL,
    sampling_s REAL,
    engine_phase_s REAL,
    conv_phase_s REAL
);
CREATE TABLE IF NOT EXISTS probe_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    nodes INTEGER,
    ms REAL,
    reported_nps INTEGER,
    t_end REAL
);
CREATE TABLE IF NOT EXISTS probe_strata (
    run_id TEXT NOT NULL,
    band TEXT NOT NULL,
    family TEXT NOT NULL,
    depth_bucket TEXT NOT NULL,
    population_count INTEGER NOT NULL,
    sampled_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS probe_puzzles (
    run_id TEXT NOT NULL,
    puzzle_id TEXT NOT NULL,
    rating INTEGER NOT NULL,
    themes TEXT NOT NULL,
    popularity INTEGER,
    nb_plays INTEGER,
    band TEXT NOT NULL,
    family TEXT NOT NULL,
    depth_bucket TEXT NOT NULL,
    solver_move_count INTEGER NOT NULL,
    puzzle_fen TEXT NOT NULL,
    solver_line_json TEXT NOT NULL,
    defender_line_json TEXT NOT NULL,
    solver_check_json TEXT,
    status TEXT NOT NULL,
    engine_calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, puzzle_id)
);
CREATE TABLE IF NOT EXISTS probe_defender_plies (
    run_id TEXT NOT NULL,
    puzzle_id TEXT NOT NULL,
    reply_index INTEGER NOT NULL,
    fen_before TEXT NOT NULL,
    stored_move TEXT NOT NULL,
    single_legal INTEGER NOT NULL,
    stage1_json TEXT NOT NULL,
    stage2_json TEXT,
    flipped INTEGER,
    fail_sample INTEGER NOT NULL DEFAULT 0,
    final_verdict TEXT NOT NULL,
    margin_cp INTEGER,
    best_mate INTEGER,
    second_mate INTEGER,
    mate_involved INTEGER NOT NULL DEFAULT 0,
    wdl_margin REAL,
    PRIMARY KEY (run_id, puzzle_id, reply_index)
);
CREATE TABLE IF NOT EXISTS probe_solver_plies (
    run_id TEXT NOT NULL,
    puzzle_id TEXT NOT NULL,
    solver_index INTEGER NOT NULL,
    fen_before TEXT NOT NULL,
    stored_move TEXT NOT NULL,
    is_best INTEGER,
    margin_cp INTEGER,
    alt_mate INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, puzzle_id, solver_index)
);
CREATE TABLE IF NOT EXISTS clean_forced_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    puzzle_id TEXT NOT NULL,
    segment_fen TEXT NOT NULL,
    line_uci TEXT NOT NULL,
    start_solver_index INTEGER NOT NULL,
    solver_move_count INTEGER NOT NULL,
    min_cp_margin INTEGER,
    min_wdl_margin REAL,
    mate_involved INTEGER NOT NULL DEFAULT 0,
    checks INTEGER NOT NULL,
    captures INTEGER NOT NULL,
    quiet INTEGER NOT NULL,
    promotions INTEGER NOT NULL,
    engine_verified_replies INTEGER NOT NULL DEFAULT 0,
    trivially_forced INTEGER NOT NULL DEFAULT 0,
    forced_reply_count INTEGER NOT NULL DEFAULT 0,
    verified_level INTEGER NOT NULL DEFAULT 1,
    quiet_share REAL NOT NULL,
    rating INTEGER NOT NULL,
    themes TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    nodes_stage1 INTEGER NOT NULL,
    nodes_stage2 INTEGER NOT NULL,
    multipv_stage1 INTEGER NOT NULL,
    multipv_stage2 INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, puzzle_id, start_solver_index)
);
CREATE TABLE IF NOT EXISTS probe_convergence (
    run_id TEXT NOT NULL,
    puzzle_id TEXT NOT NULL,
    reply_index INTEGER NOT NULL,
    fen_before TEXT NOT NULL,
    stored_move TEXT NOT NULL,
    nodes INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    flipped_to_fail INTEGER NOT NULL,
    PRIMARY KEY (run_id, puzzle_id, reply_index)
);
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))
