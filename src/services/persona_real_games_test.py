"""
Real-game fixture test for the Engine Sparring persona weight layer.

Positions are taken from the USER'S actual Lichess blitz games (account
'iaminspiredbroo', fetched live via integrations.lichess on 2026-09-10,
all 3 games the account had) and pinned here as static FENs with provenance
so the suite is offline and deterministic. Extraction rule: the positions
after White's 10th, Black's 16th, and White's 22nd moves (plies 20/32/44),
skipping positions with fewer than 10 pieces or an already-decided game.

The engine pipeline (suggest -> canonicalize_by_score -> compute_style_scores
-> attacker_score / sacrificer_score -> persona_adjusted_score with real
engine_norm_cp and game_phase) runs PRINT-ONLY, per the established harness
style: MultiPV scores jitter between runs, and the deterministic contract
guarantees are asserted in persona_features_test.py / persona_weights_test.py.
This file's job is to eyeball persona behavior on real chess, not synthetic
positions -- the numbers below are the review material.

The canonicalize_by_score() step is the Issue-1 fix: Stockfish's MultiPV
list order is not guaranteed cp-sorted, and this file's first run caught
two real inversions (game 0 move 10: Bh3+ 101 listed before Nh3 111;
game 1 move 22: Qb4 400 before Qb7 411). Every table below therefore shows
ENGINE order as true cp order; when the raw order needed re-sorting, a
"(note: raw MultiPV order was not cp-sorted...)" line prints above the
table so a reviewer can see the canonicalizer fire on live output.

Run with: cd src && ../venv/bin/python services/persona_real_games_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from engines.stockfish_engine import StockfishEngine
from services.persona_bounds import game_phase
from services.persona_features import compute_style_scores
from services.persona_weights import (
    attacker_score,
    canonicalize_by_score,
    persona_adjusted_score,
    sacrificer_score,
)

REAL_GAME_POSITIONS = [
    # game 0: user (White) vs MixerI, won 1-0
    {"fen": "2kr1bnr/ppp2ppp/2n3q1/4p3/3PN3/2P2P2/PP5P/R1BQKBNR w KQ - 0 11",
     "url": "https://lichess.org/5YJl49rw", "move": 10, "mover": "user", "pieces": 28},
    {"fen": "2kr2n1/ppp2Npp/2n5/2b5/8/1QPp1p2/PP5P/R1B1KB1R w KQ - 2 17",
     "url": "https://lichess.org/5YJl49rw", "move": 16, "mover": "user", "pieces": 23},
    {"fen": "6Q1/pppk2pp/4N3/2b5/8/2P5/PPn1Kp1P/R1B2B1R w - - 5 23",
     "url": "https://lichess.org/5YJl49rw", "move": 22, "mover": "user", "pieces": 20},
    # game 1: DansQ vs user (Black), lost 1-0
    {"fen": "rn1qk2r/p4ppp/b1p1pn2/4N3/PbBPP3/1pN5/1P3PPP/R1BQK2R w KQkq - 1 11",
     "url": "https://lichess.org/VM4BHF3k", "move": 10, "mover": "opponent", "pieces": 30},
    {"fen": "1r1q1rk1/p4ppp/1np1pn2/8/P1NPP3/1pP1BP2/6PP/1R1Q1RK1 w - - 3 17",
     "url": "https://lichess.org/VM4BHF3k", "move": 16, "mover": "opponent", "pieces": 26},
    {"fen": "5rk1/5ppp/1Qp1pn2/3qP3/P2P4/2P1BP2/6PP/5RK1 w - - 1 23",
     "url": "https://lichess.org/VM4BHF3k", "move": 22, "mover": "opponent", "pieces": 20},
    # game 2: user (White) vs M0036, lost 0-1
    {"fen": "r2q1rk1/pb3ppp/2pb1n2/n3p1N1/B7/5Q2/PPPP1PPP/RNB2RK1 w - - 6 11",
     "url": "https://lichess.org/3Z7iMZto", "move": 10, "mover": "user", "pieces": 29},
    {"fen": "2rq1rk1/pb2bppp/8/2p1p3/B3P3/1PN1BQ2/PnP2PPP/R4RK1 w - - 1 17",
     "url": "https://lichess.org/3Z7iMZto", "move": 16, "mover": "user", "pieces": 27},
    {"fen": "2rq1rk1/5ppp/5b2/p2Pp3/8/1P2BQ2/RnP1BPPP/5RK1 w - - 2 23",
     "url": "https://lichess.org/3Z7iMZto", "move": 22, "mover": "user", "pieces": 23},
]

NUM_MOVES = 5
TIME_LIMIT = 0.3


def run_position(engine, pos):
    board = chess.Board(pos["fen"])
    phase = game_phase(board)
    raw_suggestions = engine.suggest(board, num_moves=NUM_MOVES, time_limit=TIME_LIMIT)
    suggestions = canonicalize_by_score(raw_suggestions)
    resorted = [s["uci"] for s in suggestions] != [s["uci"] for s in raw_suggestions]
    if not suggestions:
        print("  (no candidate moves returned)")
        return

    best = max(s["score_cp"] for s in suggestions)
    rows = []
    for s in suggestions:
        move = chess.Move.from_uci(s["uci"])
        scores, _ = compute_style_scores(board, move)
        norm_cp = s["score_cp"] - best
        a = attacker_score(scores, board)
        x = sacrificer_score(scores, board)
        final_a = persona_adjusted_score(norm_cp, a, phase)
        final_s = persona_adjusted_score(norm_cp, x, phase)
        rows.append({
            "san": s["san"], "cp": s["score_cp"], "norm": norm_cp,
            "atk": scores.attack_gain, "def": scores.defense_gain,
            "sac": scores.sacrifice_signal, "vol": scores.volatility,
            "a": a, "x": x, "d_a": final_a - norm_cp, "d_s": final_s - norm_cp,
            "final_a": final_a, "final_s": final_s,
        })

    side = "White" if board.turn == chess.WHITE else "Black"
    who = "USER on move" if pos["mover"] == "user" else "opponent on move"
    print("=" * 118)
    print(f"REAL GAME {pos['url']}   move {pos['move']} ({side} = {who})   phase={phase:.2f}")
    if resorted:
        print("  (note: raw MultiPV order was not cp-sorted; ENGINE order below is canonicalized)")
    header = (f"   {'#':>2} {'move':<7} {'cp':>6} {'norm':>6} {'atk':>7} {'def':>7} "
              f"{'sac':>4} {'vol':>5} {'A':>8} {'S':>8} {'dA_cp':>7} {'dS_cp':>7} "
              f"{'finalA':>9} {'finalS':>9}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for i, r in enumerate(rows):
        print(f"   {i + 1:>2} {r['san']:<7} {r['cp']:>6} {r['norm']:>6} "
              f"{r['atk']:>+7.2f} {r['def']:>+7.2f} {r['sac']:>4.1f} {r['vol']:>5.2f} "
              f"{r['a']:>+8.4f} {r['x']:>+8.4f} {r['d_a']:>+7.2f} {r['d_s']:>+7.2f} "
              f"{r['final_a']:>+9.2f} {r['final_s']:>+9.2f}")

    att_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_a"], i))
    sac_idx = sorted(range(len(rows)), key=lambda i: (-rows[i]["final_s"], i))
    eng = " > ".join(rows[i]["san"] for i in range(len(rows)))
    att = " > ".join(rows[i]["san"] for i in att_idx)
    sac = " > ".join(rows[i]["san"] for i in sac_idx)
    print(f"  ENGINE order:     {eng}")
    mark_a = "  (unchanged)" if att == eng else ""
    mark_s = "  (unchanged)" if sac == eng else ""
    print(f"  ATTACKER order:   {att}{mark_a}")
    print(f"  SACRIFICER order: {sac}{mark_s}")
    print()


def main() -> int:
    print("=== Real-game persona pipeline (print-only) ===")
    engine = StockfishEngine()
    engine.start()
    try:
        for pos in REAL_GAME_POSITIONS:
            run_position(engine, pos)
    finally:
        engine.close()
    print("Real-game run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
