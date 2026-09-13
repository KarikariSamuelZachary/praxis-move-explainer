"""
Skill Level vs UCI_Elo strength investigation (MEASUREMENT ONLY, no src changes).

Question: can Stockfish's Skill Level (0-20) produce genuinely weaker play
than this binary's UCI_Elo floor (1320), making it a viable "below 1320"
tier for sparring?

Phases (run separately, each saves JSON to /tmp/opencode/skill_probe/):

  gen-suite  -- build a fixed position suite from quick SF-vs-SF games so
                every config is measured on IDENTICAL positions.
  suite      -- for each config: play one move per suite position at fixed
                time, score every move against a full-strength analyzer
                (depth 13): cp-loss, blunder rate, missed favorable
                captures, missed forced mates.
  games      -- play N games per config vs a FIXED UCI_Elo=2000 reference,
                same per-move metrics on the config side + outcomes.
  ladder     -- suite metric for extra UCI_Elo rungs (1600/1900/2200/2500)
                to locate Skill 0's profile relative to known Elo settings.
  combo      -- raw-UCI probe: what does the binary echo back when
                UCI_LimitStrength+UCI_Elo and Skill Level are set together
                in both orders, plus suite behavior for both orders.
  report     -- aggregate everything into tables.

Usage: venv/bin/python skill_level_probe.py <phase> [args]
"""

import json
import os
import statistics
import subprocess
import sys

import chess
import chess.engine

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from engines.stockfish_engine import configure_strength, resolve_stockfish_path  # noqa: E402

SF_PATH = resolve_stockfish_path()
OUT_DIR = "/tmp/opencode/skill_probe"
SUITE_PATH = os.path.join(OUT_DIR, "suite.json")
SUITE_SIZE = 60
MOVE_TIME = 0.15          # per-move think time for players (suite + games)
ANALYZE_DEPTH = 13        # analyzer depth for reference evals
BLUNDER_CP = 300
BIG_DROP_CP = 150
CAPTURE_GAIN_CP = 250     # best-vs-second gap that marks a "free material" moment
MATE_CP = 10000
LOSS_CAP = 1000           # cap per-move loss for means (mate scores explode)
GAME_CAP_PLIES = 160
RESIGN_CP = -2000         # config side resigns once past this (its POV)
GAMES_PER_CONFIG = 6      # 3 as white, 3 as black


def new_engine():
    eng = chess.engine.SimpleEngine.popen_uci(SF_PATH)
    eng.configure({"Threads": 1, "Hash": 64})
    return eng


def new_analyzer():
    eng = chess.engine.SimpleEngine.popen_uci(SF_PATH)
    eng.configure({"Threads": 1, "Hash": 128})
    return eng


def score_cp(info, pov):
    s = info["score"].pov(pov)
    if s.is_mate():
        return MATE_CP if s.mate() > 0 else -MATE_CP
    return s.score()


# --------------------------------------------------------------------------
# gen-suite
# --------------------------------------------------------------------------

def gen_suite():
    """Sample middlegame positions from quick full-strength SF vs SF games."""
    positions = []
    seen = set()
    eng = new_engine()
    try:
        while len(positions) < SUITE_SIZE:
            board = chess.Board()
            plies = 0
            while not board.is_game_over() and plies < 90:
                if plies >= 12 and plies % 2 == 0 and len(positions) < SUITE_SIZE:
                    fen = board.fen()
                    if fen not in seen and board.legal_moves.count() >= 5:
                        seen.add(fen)
                        positions.append(fen)
                r = eng.play(board, chess.engine.Limit(time=0.05))
                board.push(r.move)
                plies += 1
            print(f"  game done: plies={plies}, sampled={len(positions)}",
                  flush=True)
    finally:
        eng.quit()
    with open(SUITE_PATH, "w") as f:
        json.dump(positions, f)
    print(f"wrote {len(positions)} positions -> {SUITE_PATH}")


# --------------------------------------------------------------------------
# shared metric machinery
# --------------------------------------------------------------------------

def measure_move(analyzer, board_before, played, mover):
    """Score one played move against full-strength analyzer opinion.

    Returns dict with cp_loss (clamped to [0, LOSS_CAP]), raw_loss, blunder,
    big_drop, missed_capture (free-material moment passed up), missed_mate.
    """
    infos = analyzer.analyse(
        board_before, chess.engine.Limit(depth=ANALYZE_DEPTH), multipv=2
    )
    best = infos[0]
    best_score = score_cp(best, mover)
    second_score = score_cp(infos[1], mover) if len(infos) > 1 else best_score
    best_move = best["pv"][0]
    best_is_capture = board_before.is_capture(best_move)

    board_after = board_before.copy()
    board_after.push(played)
    after_info = analyzer.analyse(board_after, chess.engine.Limit(depth=ANALYZE_DEPTH))
    after_score = score_cp(after_info, mover)

    raw_loss = best_score - after_score
    clamped = max(0, min(raw_loss, LOSS_CAP))
    missed_mate = (
        best_score == MATE_CP and after_score != MATE_CP
    )
    missed_capture = (
        best_is_capture
        and played != best_move
        and (best_score - second_score) >= CAPTURE_GAIN_CP
    )
    return {
        "cp_loss": clamped,
        "raw_loss": raw_loss,
        "blunder": raw_loss >= BLUNDER_CP,
        "big_drop": raw_loss >= BIG_DROP_CP,
        "missed_capture": missed_capture,
        "missed_mate": missed_mate,
        "after_score": after_score,
    }


def run_config_suite(eng, analyzer, positions):
    """Play one MOVE_TIME move per suite position; return per-move metrics."""
    rows = []
    for fen in positions:
        board = chess.Board(fen)
        result = eng.play(board, chess.engine.Limit(time=MOVE_TIME))
        rows.append({"fen": fen, "played": result.move.uci(),
                     **measure_move(analyzer, board, result.move, board.turn)})
    return rows


def summarize(rows):
    losses = [r["cp_loss"] for r in rows]
    n = len(rows)
    return {
        "moves": n,
        "mean_cp_loss": round(statistics.mean(losses), 1),
        "median_cp_loss": round(statistics.median(losses), 1),
        "p90_cp_loss": round(sorted(losses)[int(0.9 * (n - 1))], 1),
        "blunder_rate": round(sum(r["blunder"] for r in rows) / n, 4),
        "big_drop_rate": round(sum(r["big_drop"] for r in rows) / n, 4),
        "missed_capture_rate": round(sum(r["missed_capture"] for r in rows) / n, 4),
        "missed_mates": sum(r["missed_mate"] for r in rows),
    }


CONFIGS = {
    "full": lambda e: configure_strength(e),  # metric calibration floor
    "skill_0": lambda e: configure_strength(e, skill_level=0),
    "skill_1": lambda e: configure_strength(e, skill_level=1),
    "skill_2": lambda e: configure_strength(e, skill_level=2),
    "skill_3": lambda e: configure_strength(e, skill_level=3),
    "skill_5": lambda e: configure_strength(e, skill_level=5),
    "elo_1320": lambda e: configure_strength(e, elo=1320),
    "elo_1600": lambda e: configure_strength(e, elo=1600),
    "elo_1900": lambda e: configure_strength(e, elo=1900),
    "elo_2200": lambda e: configure_strength(e, elo=2200),
    "elo_2500": lambda e: configure_strength(e, elo=2500),
    # Both mechanisms at once, Elo set first then Skill Level (the order a
    # caller doing "limit to 1320, then also dumb it down" would produce).
    "combo_elo_then_skill0": lambda e: (
        e.configure({"UCI_LimitStrength": True, "UCI_Elo": 1320}),
        e.configure({"Skill Level": 0}),
    )[1] or {"note": "elo first, skill after"},
    # Reverse order: dumb it down first, then apply the Elo limit.
    "combo_skill0_then_elo": lambda e: (
        e.configure({"Skill Level": 0}),
        e.configure({"UCI_LimitStrength": True, "UCI_Elo": 1320}),
    )[1] or {"note": "skill first, elo after"},
    # Sharper discriminator: at Elo 1320 the internal Elo->skill mapping is
    # already skill 0, so a 1320 combo can't tell which mechanism wins.
    # At Elo 2500 the two mechanisms disagree strongly: if Elo wins the
    # combo behaves like elo_2500 (mean 27.4); if Skill Level wins or the
    # last-set option sticks, it behaves like skill_0 (~44).
    "combo_elo2500_skill0": lambda e: (
        e.configure({"UCI_LimitStrength": True, "UCI_Elo": 2500}),
        e.configure({"Skill Level": 0}),
    )[1] or {"note": "elo 2500 first, skill 0 after"},
    "combo_skill0_elo2500": lambda e: (
        e.configure({"Skill Level": 0}),
        e.configure({"UCI_LimitStrength": True, "UCI_Elo": 2500}),
    )[1] or {"note": "skill 0 first, elo 2500 after"},
}


# --------------------------------------------------------------------------
# suite
# --------------------------------------------------------------------------

def run_suite(config_names):
    with open(SUITE_PATH) as f:
        positions = json.load(f)
    analyzer = new_analyzer()
    try:
        for name in config_names:
            out = os.path.join(OUT_DIR, f"suite_{name}.json")
            if os.path.exists(out):
                print(f"[skip] {name} already done")
                continue
            eng = new_engine()
            try:
                applied = CONFIGS[name](eng)
                print(f"running suite: {name} ({applied})...", flush=True)
                rows = run_config_suite(eng, analyzer, positions)
            finally:
                eng.quit()
            payload = {"config": name, "applied": str(applied),
                       "summary": summarize(rows), "rows": rows}
            with open(out, "w") as f:
                json.dump(payload, f)
            print(f"  -> {summarize(rows)}")
    finally:
        analyzer.quit()


# --------------------------------------------------------------------------
# games
# --------------------------------------------------------------------------

def play_games(config_name, n_games):
    """Config vs fixed UCI_Elo=2000 reference; metrics on the config side."""
    with open(SUITE_PATH) as f:
        _ = json.load(f)  # sanity: suite exists
    analyzer = new_analyzer()
    out_path = os.path.join(OUT_DIR, f"games_{config_name}.json")
    results = []
    try:
        for i in range(n_games):
            out = os.path.join(OUT_DIR, f"games_{config_name}_{i}.json")
            if os.path.exists(out):
                with open(out) as f:
                    results.append(json.load(f)["meta"])
                print(f"[skip] game {i} for {config_name}")
                continue
            white_is_config = (i % 2 == 0)
            cfg = new_engine()
            ref = new_engine()
            try:
                CONFIGS[config_name](cfg)
                configure_strength(ref, elo=2000)
                board = chess.Board()
                move_rows = []
                outcome = None
                while not board.is_game_over() and board.ply() < GAME_CAP_PLIES:
                    mover = board.turn
                    is_config = (mover == chess.WHITE) == white_is_config
                    if is_config:
                        pre_board = board.copy()
                        result = cfg.play(board, chess.engine.Limit(time=MOVE_TIME))
                        board.push(result.move)
                        row = {"ply": board.ply(),
                               **measure_move(analyzer, pre_board, result.move, mover)}
                        move_rows.append(row)
                        if row["after_score"] <= RESIGN_CP:
                            outcome = "resign-adjudicated"
                            break
                    else:
                        r = ref.play(board, chess.engine.Limit(time=MOVE_TIME))
                        board.push(r.move)
                if outcome is None:
                    outcome = ("1-0" if board.turn == chess.BLACK else "0-1") \
                        if board.is_checkmate() else "draw/cap"
                meta = {
                    "game": i, "config_white": white_is_config,
                    "outcome": outcome, "plies": board.ply(),
                    "config_moves": len(move_rows),
                }
                payload = {"meta": meta, "rows": move_rows}
                with open(out, "w") as f:
                    json.dump(payload, f)
                results.append(meta)
                print(f"  game {i}: {meta}", flush=True)
            finally:
                cfg.quit()
                ref.quit()
        wins = losses = draws = 0
        for m in results:
            o = m["outcome"]
            if o == "resign-adjudicated":
                losses += 1  # only the config side ever resigns
            elif o in ("1-0", "0-1"):
                config_won = (o == "1-0") == m["config_white"]
                wins += config_won
                losses += not config_won
            else:
                draws += 1
        summary = {"config": config_name, "games": len(results),
                   "wins": wins, "losses": losses, "draws_or_caps": draws,
                   "mean_plies": round(statistics.mean(
                       [m["plies"] for m in results]), 1)}
        with open(out_path, "w") as f:
            json.dump({"summary": summary, "games": results}, f)
        print(f"{config_name}: {summary}")
    finally:
        analyzer.quit()


def aggregate_games(config_name):
    """Fold per-game move rows into the same summary shape as the suite."""
    rows = []
    metas = []
    for i in range(GAMES_PER_CONFIG):
        path = os.path.join(OUT_DIR, f"games_{config_name}_{i}.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            data = json.load(f)
        rows.extend(data["rows"])
        metas.append(data["meta"])
    return {"config": config_name, "summary": summarize(rows),
            "metas": metas}


# --------------------------------------------------------------------------
# combo: raw UCI readback
# --------------------------------------------------------------------------

def combo_readback():
    """Raw UCI session: set both mechanisms, echo current option values."""
    def session(cmds):
        p = subprocess.run(
            [SF_PATH], input="\n".join(cmds + ["quit"]) + "\n",
            capture_output=True, text=True, timeout=30)
        opts = {}
        for line in p.stdout.splitlines():
            if line.startswith("option name"):
                parts = line.split()
                name = parts[2]
                if name in ("Skill Level", "UCI_Elo", "UCI_LimitStrength"):
                    if "default" in parts:
                        opts[name] = parts[parts.index("default") + 1]
        return opts

    baseline = session(["uci"])
    order_a = session([
        "setoption name UCI_LimitStrength value true",
        "setoption name UCI_Elo value 1320",
        "setoption name Skill Level value 0",
        "uci",
    ])
    order_b = session([
        "setoption name Skill Level value 0",
        "setoption name UCI_LimitStrength value true",
        "setoption name UCI_Elo value 1320",
        "uci",
    ])
    out = {"baseline_defaults": baseline,
           "elo_then_skill0": order_a,
           "skill0_then_elo": order_b}
    with open(os.path.join(OUT_DIR, "combo_readback.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def report():
    suite_names = ["full", "skill_0", "skill_1", "skill_2", "skill_3", "skill_5",
                   "elo_1320", "elo_1600", "elo_1900", "elo_2200", "elo_2500",
                   "combo_elo_then_skill0", "combo_skill0_then_elo",
                   "combo_elo2500_skill0", "combo_skill0_elo2500"]
    print("=" * 78)
    print("POSITION SUITE (60 fixed positions, 1 move each @ 0.15s,")
    print("scored vs full-strength Stockfish depth 13)")
    print("=" * 78)
    header = (f"{'config':<24}{'moves':>6}{'meanCP':>8}{'medCP':>7}{'p90':>7}"
              f"{'>=300':>7}{'>=150':>7}{'missCap':>9}{'missMate':>9}")
    print(header)
    for name in suite_names:
        path = os.path.join(OUT_DIR, f"suite_{name}.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            s = json.load(f)["summary"]
        print(f"{name:<24}{s['moves']:>6}{s['mean_cp_loss']:>8}{s['median_cp_loss']:>7}"
              f"{s['p90_cp_loss']:>7}{s['blunder_rate']:>7.2%}{s['big_drop_rate']:>7.2%}"
              f"{s['missed_capture_rate']:>9.2%}{s['missed_mates']:>9}")
    print()
    print("=" * 78)
    print("GAMES vs fixed UCI_Elo=2000 reference (6 per config, alternating")
    print("colors, 0.15s/move both sides, 160-ply cap, resign at -2000cp)")
    print("=" * 78)
    print(f"{'config':<12}{'W':>3}{'L':>3}{'D/cap':>6}{'avgPlies':>9}"
          f"{'cfgMoves':>9}{'meanCP':>8}{'medCP':>7}{'>=300':>7}"
          f"{'>=150':>7}{'missCap':>9}{'missMate':>9}")
    for name in ["skill_0", "skill_1", "skill_2", "skill_3", "skill_5",
                 "elo_1320"]:
        path = os.path.join(OUT_DIR, f"games_{name}.json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            g = json.load(f)["summary"]
        agg = aggregate_games(name)["summary"]
        print(f"{name:<12}{g['wins']:>3}{g['losses']:>3}{g['draws_or_caps']:>6}"
              f"{g['mean_plies']:>9}{agg['moves']:>9}{agg['mean_cp_loss']:>8}"
              f"{agg['median_cp_loss']:>7}{agg['blunder_rate']:>7.2%}"
              f"{agg['big_drop_rate']:>7.2%}{agg['missed_capture_rate']:>9.2%}"
              f"{agg['missed_mates']:>9}")
    combo_path = os.path.join(OUT_DIR, "combo_readback.json")
    if os.path.exists(combo_path):
        print()
        print("=" * 78)
        print("COMBO READBACK (raw UCI echo of current option values)")
        print("=" * 78)
        with open(combo_path) as f:
            print(json.dumps(json.load(f), indent=2))


PHASES = {
    "gen-suite": lambda: gen_suite(),
    "suite": lambda: run_suite(sys.argv[2:]),
    "games": lambda: play_games(sys.argv[2], GAMES_PER_CONFIG),
    "combo": lambda: combo_readback(),
    "report": lambda: report(),
}

if __name__ == "__main__":
    PHASES[sys.argv[1]]()
