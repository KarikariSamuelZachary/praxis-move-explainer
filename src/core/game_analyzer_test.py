"""
Test harness for the GameAnalyzer review loop and the review Stockfish
singleton.

Covers the review performance fix:

  A. Evaluation reuse (fake engine): the position after move N is exactly
     the position before move N+1, so a PGN with P plies must issue exactly
     P+1 engine searches (previously 2P) and every position must be
     searched at most once.
  B. Classification parity (fake engine): prescribed per-position scores
     produce the exact expected book/best/excellent/good/inaccuracy/
     mistake/blunder rows, with explanations only for mistake/blunder.
  C. target_color filtering and include_explanations=False behavior.
  D. LIVE: the long-lived review Stockfish singleton is reused across
     calls, a reset creates a fresh process, and analyze_full_game runs
     end-to-end against it.

Run with: cd src && ../venv/bin/python core/game_analyzer_test.py
"""
import os
import sys
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import chess.pgn

from core.game_analyzer import GameAnalyzer
from engines.stockfish_engine import (
    close_review_stockfish,
    get_review_stockfish,
    reset_review_stockfish,
)
from llms.mock_explainer import MockExplainer
from schemas.models import Evaluation, Explanation

# Ruy Lopez, Breyer-ish: 18 plies, so plies 10..17 (past the 10-ply book
# window) exercise every classification bucket.
PGN = (
    "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 "
    "6. Re1 b5 7. Bb3 d6 8. c3 O-O 9. h3 Nb8"
)

# Prescribed score (side-to-move perspective) per position p0..p18, one per
# ply boundary. For the mover of ply k, cp_loss = base[k] + base[k+1]
# (eval_after is negated into the mover's frame), which yields the exact
# classification sequence asserted in EXPECTED_CLASSES below.
SCORES = [
    30, 25, 20, 30, 15, 25, 40, 20, 10, 5,
    30, -20, 50, 0, 80, 70, 350, -300, 390,
]

EXPECTED_CLASSES = [
    *(["book"] * 10),
    "best",        # k=10  cp_loss =  30 + -20 =  10
    "excellent",   # k=11  cp_loss = -20 +  50 =  30
    "good",        # k=12  cp_loss =  50 +   0 =  50
    "inaccuracy",  # k=13  cp_loss =   0 +  80 =  80
    "mistake",     # k=14  cp_loss =  80 +  70 = 150
    "blunder",     # k=15  cp_loss =  70 + 350 = 420
    "good",        # k=16  cp_loss = 350 + -300 = 50
    "inaccuracy",  # k=17  cp_loss = -300 + 390 = 90
]

EXPECTED_LOSSES = [
    *([0] * 10),
    10, 30, 50, 80, 150, 420, 50, 90,
]


class _FakeEngine:
    """Deterministic engine keyed on the full FEN; records every FEN searched."""

    def __init__(self, scores_by_fen):
        self._scores = scores_by_fen
        self.calls = []

    def evaluate(self, board):
        fen = board.fen()
        self.calls.append(fen)
        return Evaluation(
            score_cp=float(self._scores[fen]),
            best_move_uci="e2e4",
            best_move_san="e4",
        )


class _FakeExplainer:
    def __init__(self):
        self.calls = 0

    def explain_mistake(self, mistake):
        self.calls += 1
        return Explanation(
            why_good="",
            why_failed="failed",
            concept_involved="concept",
            typical_pattern="pattern",
        )


def _position_fens(pgn: str):
    game = chess.pgn.read_game(StringIO(pgn))
    board = game.board()
    fens = [board.fen()]
    for node in game.mainline():
        board.push(node.move)
        fens.append(board.fen())
    return fens


def _fake_engine_and_explainer():
    fens = _position_fens(PGN)
    assert len(fens) == len(SCORES), f"expected {len(SCORES)} positions, got {len(fens)}"
    engine = _FakeEngine(dict(zip(fens, SCORES)))
    explainer = _FakeExplainer()
    return engine, explainer


# ---------------------------------------------------------------------------
# A. Single evaluation per ply
# ---------------------------------------------------------------------------
def test_single_evaluation_per_ply():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer)

    rows = analyzer.analyze_full_game(PGN)

    plies = len(SCORES) - 1
    assert len(engine.calls) == plies + 1, (
        f"expected {plies + 1} engine searches (one per position), got {len(engine.calls)}"
    )
    assert len(engine.calls) == len(set(engine.calls)), (
        "the same position was searched more than once"
    )
    assert len(rows) == plies + 1, f"expected {plies + 1} rows, got {len(rows)}"
    print(f"  [PASS] {plies} plies -> {len(engine.calls)} searches "
          f"(previously {2 * plies}); every position searched once")


# ---------------------------------------------------------------------------
# B. Classification parity
# ---------------------------------------------------------------------------
def test_classification_parity():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer)

    rows = analyzer.analyze_full_game(PGN)
    move_rows = rows[1:]

    classes = [row["classification"] for row in move_rows]
    losses = [row["cp_loss"] for row in move_rows]
    assert classes == EXPECTED_CLASSES, f"classes mismatch:\n{classes}\n{EXPECTED_CLASSES}"
    assert losses == EXPECTED_LOSSES, f"losses mismatch:\n{losses}\n{EXPECTED_LOSSES}"

    explained = [i for i, row in enumerate(move_rows) if "explanation" in row]
    assert explained == [14, 15], f"explained plies should be [14, 15], got {explained}"
    assert explainer.calls == 2, f"expected 2 explanation calls, got {explainer.calls}"

    assert rows[0]["san"] == "Start" and rows[0]["classification"] == "book"
    print("  [PASS] all classifications/cp_losses exact; explanations only for "
          "mistake/blunder (plies 14, 15)")


def test_include_explanations_false():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer)

    rows = analyzer.analyze_full_game(PGN, include_explanations=False)

    assert all("explanation" not in row for row in rows), "explanations leaked"
    assert explainer.calls == 0, f"explainer called {explainer.calls} times"
    print("  [PASS] include_explanations=False attaches no payload and calls no LLM")


# ---------------------------------------------------------------------------
# C. target_color filtering
# ---------------------------------------------------------------------------
def test_target_color_filtering():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer)

    rows = analyzer.analyze_full_game(PGN, target_color="white")

    # Start row + 9 white plies. Positions for every ply are still searched
    # (the carryover needs them), so the engine count is unchanged.
    assert len(rows) == 10, f"expected Start + 9 white rows, got {len(rows)}"
    assert len(engine.calls) == len(SCORES), "engine search count changed under filtering"
    classes = [row["classification"] for row in rows[1:]]
    expected = ["book", "book", "book", "book", "book", "best", "good", "mistake", "good"]
    assert classes == expected, f"white rows mismatch: {classes}"
    assert all(row["color"] == "white" for row in rows[1:]), "non-white row leaked"
    print("  [PASS] target_color=white returns Start + 9 white plies with exact classes")


# ---------------------------------------------------------------------------
# D. analyze_pgn (legacy path) uses the same single-evaluation loop
# ---------------------------------------------------------------------------
def test_analyze_pgn_matches_full_review():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer)

    mistakes = analyzer.analyze_pgn(PGN)

    assert len(engine.calls) == len(SCORES), (
        f"expected {len(SCORES)} searches, got {len(engine.calls)}"
    )
    moves = [analyzed.the_mistake.move_played for analyzed in mistakes]
    assert moves == ["c3", "O-O"], f"expected mistake/blunder moves c3, O-O; got {moves}"
    assert explainer.calls == 2, f"expected 2 explanation calls, got {explainer.calls}"
    print("  [PASS] analyze_pgn returns the same mistake/blunder set with N+1 searches")


# ---------------------------------------------------------------------------
# E. LIVE: review singleton lifecycle + end-to-end analysis
# ---------------------------------------------------------------------------
def test_live_review_singleton_and_analysis():
    engine_a = get_review_stockfish(depth=2)
    try:
        assert get_review_stockfish(depth=2) is engine_a, (
            "get_review_stockfish should return the same live singleton"
        )

        board = chess.Board()
        first = engine_a.evaluate(board)
        assert first.best_move_uci, "live engine returned no best move"

        reset_review_stockfish()
        engine_b = get_review_stockfish(depth=2)
        assert engine_b is not engine_a, "reset should drop the old subprocess"

        try:
            analyzer = GameAnalyzer(engine=engine_b, explainer=MockExplainer())
            rows = analyzer.analyze_full_game("1. e4 e5 2. Nf3 Nc6")
            assert len(rows) == 5, f"expected Start + 4 rows, got {len(rows)}"
            assert all(row["classification"] == "book" for row in rows), rows
            print("  [PASS] review singleton reused; reset spawns fresh process; "
                  "4-ply review analyzed end-to-end")
        finally:
            reset_review_stockfish()
    finally:
        close_review_stockfish()


def main() -> int:
    print("=== Running GameAnalyzer review-loop tests ===")
    tests = [
        test_single_evaluation_per_ply,
        test_classification_parity,
        test_include_explanations_false,
        test_target_color_filtering,
        test_analyze_pgn_matches_full_review,
        test_live_review_singleton_and_analysis,
    ]
    for test in tests:
        try:
            test()
        except (AssertionError, ValueError) as exc:
            print(f"\n  [FAIL] {test.__name__}: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
            return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
