"""
Feature-dump harness for the Engine Sparring persona reranker.

Diagnostic tool (NOT user-facing, no pass/fail assertions): for each fixture
position it starts a real Stockfish instance, asks for its top-5 candidate
moves at THREE strength levels (full / mid Elo / low Elo), and runs
compute_style_scores() on every candidate so a human can eyeball whether the
feature extractor's numbers look sane BEFORE any persona weight vectors are
built on top of them.

Output is one table per (fixture, strength) pair:
  * candidate move (SAN), engine score_cp, and engine_norm_cp (candidate
    score minus that level's own best score -- the raw engine-relative gap,
    NOT persona-biased yet),
  * every StyleScores top-level field (attack_gain, defense_gain,
    sacrifice_signal, volatility),
  * the attack/defense subcomponents from the debug dict.

Strength levels:
  * "full"     -> no configure_strength() call (fresh engine = full strength).
  * "elo-1800" -> configure_strength(engine.engine, elo=1800).
  * "elo-1320" -> configure_strength(engine.engine, elo=1320).
    NOTE: the spec suggested "low Elo (e.g. 1200)", but this Stockfish 16
    binary advertises UCI_Elo in [1320, 3190] (verified live), so 1200 is
    below the supported floor and would be rejected by configure_strength().
    1320 is the binary's actual minimum and is used as the "low" level.

Usage:
    cd src && ../venv/bin/python services/persona_dump.py                # all fixtures
    cd src && ../venv/bin/python services/persona_dump.py "obvious sacrifice" "sharp tactical no quiet alternative"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from engines.stockfish_engine import StockfishEngine, configure_strength
from services.persona_bounds import game_phase
from services.persona_features import compute_style_scores
from services.persona_fixtures import FIXTURES

# multipv width. 5 gives a readable spread of near-best candidates without
# drowning the reviewer in tail moves that a persona could never plausibly
# reach (the trust decay in persona_bounds.py hard-rejects candidates ~75cp
# below best anyway).
NUM_MOVES = 5

# Slightly above the wrapper's FAST_ANALYSIS_TIME (0.1s): 0.3s gives a more
# stable MultiPV ordering/score so the norm_cp gaps are meaningful for a
# human, while keeping the whole 12x3 dump under ~15s.
TIME_LIMIT = 0.3

STRENGTH_LEVELS = [
    ("full", None),
    ("elo-1800", 1800),
    ("elo-1320", 1320),
]


def _dump_fixture(engine, fixture, label):
    board = chess.Board(fixture["fen"])
    phase = game_phase(board)

    print("=" * 100)
    print(f"FIXTURE: {fixture['name']}   [strength: {label}]   phase={phase:.2f}")
    print(f"  {fixture['description']}")
    print(f"  FEN: {fixture['fen']}")
    print("-" * 100)

    suggestions = engine.suggest(board, num_moves=NUM_MOVES, time_limit=TIME_LIMIT)
    if not suggestions:
        print("  (no candidate moves returned)")
        return

    best_score = max(s["score_cp"] for s in suggestions)

    header = (
        f"  {'#':>2}  {'move':<7} {'score':>8} {'norm':>7} "
        f"{'atk':>9} {'def':>9} {'sac':>5} {'vol':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    for idx, cand in enumerate(suggestions, 1):
        move = chess.Move.from_uci(cand["uci"])
        scores, debug = compute_style_scores(board, move)
        norm_cp = cand["score_cp"] - best_score

        a = scores.attack_sub
        d = scores.defense_sub

        print(
            f"  {idx:>2}  {cand['san']:<7} {cand['score_cp']:>8} {norm_cp:>7} "
            f"{scores.attack_gain:>+9.2f} {scores.defense_gain:>+9.2f} "
            f"{scores.sacrifice_signal:>5.1f} {scores.volatility:>7.2f}"
        )
        print(
            f"      attack: kzp={a.king_zone_pressure:+.2f} adj={a.king_adjacent_attacks:+.1f} "
            f"chk={a.checks:.0f} open={a.open_lines:.0f} esc={a.escape_square_pressure:+.1f}"
            f"  |  defense: epr={d.enemy_pressure_reduction:+.2f} kzd={d.king_zone_defense:+.2f} "
            f"blk={d.line_blocking:.0f} shd={d.pawn_shield:+.2f} mob={d.king_mobility:+.1f}"
        )
    print()


def main() -> int:
    selected = set(sys.argv[1:])

    engine = StockfishEngine()
    engine.start()
    try:
        for label, elo in STRENGTH_LEVELS:
            # "full" is the fresh-engine default: deliberately NO configure call.
            if elo is not None:
                configure_strength(engine.engine, elo=elo)
            for fixture in FIXTURES:
                if selected and fixture["name"] not in selected:
                    continue
                _dump_fixture(engine, fixture, label)
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
