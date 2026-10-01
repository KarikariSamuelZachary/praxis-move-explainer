"""
Test harness for the GameAnalyzer review loop and the review Stockfish
singleton.

Covers the review performance fix:

  A. Evaluation reuse (fake engine): the position after move N is exactly
     the position before move N+1, so a PGN with P plies must issue exactly
     P+1 engine searches (previously 2P) and every position must be
     searched at most once.
  B. Classification parity (fake engine): prescribed per-position scores
     produce the exact expected book/excellent/good/inaccuracy/mistake/
     blunder rows under Chess.com's Expected Points model (including the
     2023 "blunder must lose material or allow mate" rule), with
     explanations only for mistake/blunder.
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

from core.game_analyzer import GameAnalyzer, expected_points
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
# EP values reflect the five-game calibration fit at the reference rating.
SCORES = [
    30, 25, 20, 30, 15, 25, 40, 20, 10, 5,
    30, -20, 50, 0, 80, 70, 350, -300, 390,
]

EXPECTED_CLASSES = [
    *(["book"] * 10),
    "excellent",   # k=10  EP loss 0.0112
    "good",        # k=11  EP loss 0.0335
    "inaccuracy",  # k=12  EP loss 0.0560
    "inaccuracy",  # k=13  EP loss 0.0890
    "miss",        # k=14  EP loss 0.1671 after opponent's 0.0890 EP error
    "blunder",     # k=15  EP loss 0.4066 + allows mate (fake engine)
    "good",        # k=16  EP loss 0.0344
    "inaccuracy",  # k=17  EP loss 0.0585
]

EXPECTED_LOSSES = [
    *([0] * 10),
    10, 30, 50, 80, 150, 420, 50, 90,
]

EXPECTED_EP_LOSSES = [
    *([0.0] * 10),
    0.0112, 0.0335, 0.0560, 0.0890, 0.1671, 0.4066, 0.0344, 0.0585,
]


class _FakeEngine:
    """Deterministic engine keyed on the full FEN; records every FEN searched."""

    def __init__(self, scores_by_fen, mates_by_fen=None, second_best_by_fen=None):
        self._scores = scores_by_fen
        self._mates = mates_by_fen or {}
        self._second_best = second_best_by_fen or {}
        self.calls = []

    def evaluate(self, board, multipv=1):
        fen = board.fen()
        self.calls.append(fen)
        return Evaluation(
            score_cp=float(self._scores[fen]),
            best_move_uci="e2e4",
            best_move_san="e4",
            mate=self._mates.get(fen),
            second_best_cp=(
                self._second_best.get(fen) if int(multipv) > 1 else None
            ),
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


def _book_lookup(board, move):
    """Stand-in for services.opening_book.is_book_move: first 10 plies."""
    return board.ply() < 10


def _fake_engine_and_explainer():
    fens = _position_fens(PGN)
    assert len(fens) == len(SCORES), f"expected {len(SCORES)} positions, got {len(fens)}"
    # The position after ply 15 (Black's O-O) carries a mate score for the
    # side to move (White), so Black's move satisfies the 2023 blunder rule
    # ("allows checkmate") and lands on blunder instead of mistake.
    engine = _FakeEngine(dict(zip(fens, SCORES)), mates_by_fen={fens[16]: 3})
    explainer = _FakeExplainer()
    return engine, explainer


# ---------------------------------------------------------------------------
# A. Single evaluation per ply
# ---------------------------------------------------------------------------
def test_single_evaluation_per_ply():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

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
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    rows = analyzer.analyze_full_game(PGN)
    move_rows = rows[1:]

    classes = [row["classification"] for row in move_rows]
    losses = [row["cp_loss"] for row in move_rows]
    ep_losses = [round(row["ep_loss"], 4) for row in move_rows]
    assert classes == EXPECTED_CLASSES, f"classes mismatch:\n{classes}\n{EXPECTED_CLASSES}"
    assert losses == EXPECTED_LOSSES, f"losses mismatch:\n{losses}\n{EXPECTED_LOSSES}"
    assert ep_losses == EXPECTED_EP_LOSSES, (
        f"ep losses mismatch:\n{ep_losses}\n{EXPECTED_EP_LOSSES}"
    )

    explained = [i for i, row in enumerate(move_rows) if "explanation" in row]
    assert explained == [15], f"explained plies should be [15], got {explained}"
    assert explainer.calls == 1, f"expected 1 explanation call, got {explainer.calls}"

    assert rows[0]["san"] == "Start" and rows[0]["classification"] == "book"
    print("  [PASS] all classifications/cp_losses exact; explanations only for "
          "mistake/blunder (ply 15)")


def test_include_explanations_false():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    rows = analyzer.analyze_full_game(PGN, include_explanations=False)

    assert all("explanation" not in row for row in rows), "explanations leaked"
    assert explainer.calls == 0, f"explainer called {explainer.calls} times"
    print("  [PASS] include_explanations=False attaches no payload and calls no LLM")


# ---------------------------------------------------------------------------
# C. target_color filtering
# ---------------------------------------------------------------------------
def test_target_color_filtering():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    rows = analyzer.analyze_full_game(PGN, target_color="white")

    # Start row + 9 white plies. Positions for every ply are still searched
    # (the carryover needs them), so the engine count is unchanged.
    assert len(rows) == 10, f"expected Start + 9 white rows, got {len(rows)}"
    assert len(engine.calls) == len(SCORES), "engine search count changed under filtering"
    classes = [row["classification"] for row in rows[1:]]
    expected = ["book", "book", "book", "book", "book", "excellent", "inaccuracy", "miss", "good"]
    assert classes == expected, f"white rows mismatch: {classes}"
    assert all(row["color"] == "white" for row in rows[1:]), "non-white row leaked"
    print("  [PASS] target_color=white returns Start + 9 white plies with exact classes")


# ---------------------------------------------------------------------------
# D. analyze_pgn (legacy path) uses the same single-evaluation loop
# ---------------------------------------------------------------------------
def test_analyze_pgn_matches_full_review():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    mistakes = analyzer.analyze_pgn(PGN)

    assert len(engine.calls) == len(SCORES), (
        f"expected {len(SCORES)} searches, got {len(engine.calls)}"
    )
    moves = [analyzed.the_mistake.move_played for analyzed in mistakes]
    assert moves == ["O-O"], f"expected only the blunder move O-O; got {moves}"
    assert explainer.calls == 1, f"expected 1 explanation call, got {explainer.calls}"
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
            analyzer = GameAnalyzer(
                engine=engine_b,
                explainer=MockExplainer(),
                book_lookup=_book_lookup,
            )
            rows = analyzer.analyze_full_game("1. e4 e5 2. Nf3 Nc6")
            assert len(rows) == 5, f"expected Start + 4 rows, got {len(rows)}"
            assert all(row["classification"] == "book" for row in rows), rows
            print("  [PASS] review singleton reused; reset spawns fresh process; "
                  "4-ply review analyzed end-to-end")
        finally:
            reset_review_stockfish()
    finally:
        close_review_stockfish()


# ---------------------------------------------------------------------------
# F. Expected Points model / book plumbing (unit-level)
# ---------------------------------------------------------------------------
def test_expected_points_model():
    assert expected_points(0, 1500) == 0.5
    assert expected_points(10000, 1500) > 0.999
    assert expected_points(-10000, 1500) < 0.001
    for cp in (-500, -100, 0, 100, 500):
        assert abs(expected_points(cp, 1500) + expected_points(-cp, 1500) - 1.0) < 1e-9
    assert expected_points(200, 2400) > expected_points(200, 800), (
        "higher-rated players must gain more expected points from the same advantage"
    )
    assert expected_points(0, None) == 0.5, "missing rating must default to 1500"
    assert expected_points(0, 99999) == expected_points(0, 3500), "rating is clamped"
    print("  [PASS] EP logistic: symmetric, saturating, rating-scaled, clamped")


def test_best_move_equality_and_blunder_gate():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    # Engine's top choice -> "best" even if search noise moved the eval.
    best = analyzer.classify_move(
        Evaluation(score_cp=100, best_move_uci="e2e4", best_move_san="e4"),
        Evaluation(score_cp=-200, best_move_uci="g8f6", best_move_san="Nf6"),
        "white",
        move_uci="e2e4",
        player_rating=1500,
    )
    assert best == "best", f"expected best, got {best}"

    # Keep Best tied to the engine's top move. The depth-18 calibration set
    # improved when a 0.005 EP tolerance was removed: near-best alternatives
    # were commonly Chess.com Excellent.
    near_before = Evaluation(score_cp=0, best_move_uci="e2e4", best_move_san="e4")
    near_after = Evaluation(score_cp=4, best_move_uci="g8f6", best_move_san="Nf6")
    near = analyzer.classify_move(
        near_before, near_after, "white", move_uci="f1c4", player_rating=1500
    )
    outside_after = Evaluation(
        score_cp=5, best_move_uci="g8f6", best_move_san="Nf6"
    )
    outside = analyzer.classify_move(
        near_before, outside_after, "white", move_uci="f1c4", player_rating=1500
    )
    assert near == "excellent", f"expected non-top near-best move to be excellent, got {near}"
    assert outside == "excellent", f"expected non-top move to be excellent, got {outside}"

    # Huge EP loss without material/mate consequence -> mistake (2023 rule).
    # eval_after is scored from the OPPONENT's POV, so a mover drop is
    # positive there.
    drop_before = Evaluation(score_cp=0, best_move_uci="", best_move_san="")
    drop_after = Evaluation(score_cp=500, best_move_uci="", best_move_san="")
    without = analyzer.classify_move(
        drop_before, drop_after, "white", player_rating=1500
    )
    with_consequence = analyzer.classify_move(
        drop_before,
        drop_after,
        "white",
        player_rating=1500,
        blunder_consequence=True,
    )
    assert without == "mistake", f"expected mistake, got {without}"
    assert with_consequence == "blunder", f"expected blunder, got {with_consequence}"

    # Mating move short-circuit (terminal position has no engine score).
    mating = analyzer.classify_move(
        drop_before, drop_after, "white", player_rating=1500, delivers_mate=True
    )
    assert mating == "best", f"expected best for a mating move, got {mating}"
    print("  [PASS] best-by-move-equality, blunder consequence gate, mate shortcut")


def test_rating_scaling():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)
    eval_before = Evaluation(score_cp=0, best_move_uci="", best_move_san="")
    eval_after = Evaluation(score_cp=200, best_move_uci="", best_move_san="")

    low = analyzer.classify_move(
        eval_before, eval_after, "white", player_rating=800, blunder_consequence=True
    )
    high = analyzer.classify_move(
        eval_before, eval_after, "white", player_rating=2400, blunder_consequence=True
    )
    assert low == "mistake" and high == "blunder", (
        f"same 200cp drop should be mistake at 800 / blunder at 2400; got {low}/{high}"
    )
    print("  [PASS] the same eval swing is rated more severely for higher ratings")


def test_book_contiguity():
    pgn = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6"
    fens = _position_fens(pgn)
    engine = _FakeEngine({fen: 0 for fen in fens})
    # Book claims plies 0..2 and the position before ply 5, but NOT ply 3.
    book_fens = set(fens[:3]) | {fens[5]}
    analyzer = GameAnalyzer(
        engine=engine,
        explainer=_FakeExplainer(),
        book_lookup=lambda board, move: board.fen() in book_fens,
    )
    rows = analyzer.analyze_full_game(pgn)
    classes = [row["classification"] for row in rows[1:]]
    assert classes[:3] == ["book", "book", "book"], classes
    assert "book" not in classes[3:], (
        f"game left the book at ply 3 and must not re-enter; got {classes}"
    )
    print("  [PASS] book is contiguous: a deviation ends book for the rest of the game")


def test_material_consequence_helper():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    # Position after White's Qh5?? — Black's best reply Nxh5 wins the queen.
    board = chess.Board("rnbqkb1r/pppppppp/5n2/7Q/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1")
    capturing = Evaluation(score_cp=200, best_move_uci="f6h5", best_move_san="Nxh5")
    quiet = Evaluation(score_cp=10, best_move_uci="e7e6", best_move_san="e6")
    assert analyzer._loses_material_after_best_reply(board, capturing, "white") is True
    assert analyzer._loses_material_after_best_reply(board, quiet, "white") is False

    # A quiet checking reply reveals a rook loss three plies later. The
    # principal variation check finds it after White has had a response.
    fork_line = chess.Board("8/8/8/4b3/8/8/8/R3K2k b - - 0 1")
    deeper = Evaluation(
        score_cp=300,
        best_move_uci="e5c3",
        best_move_san="Bc3+",
        principal_variation_uci=["e5c3", "e1d1", "c3a1", "d1e1"],
    )
    assert analyzer._loses_material_after_best_reply(
        fork_line, deeper, "white"
    ) is True
    print("  [PASS] material consequence: immediate capture and 4-ply fork found")


# ---------------------------------------------------------------------------
# G. Special labels: Brilliant / Great / Miss + SEE sacrifice detection
# ---------------------------------------------------------------------------
def test_see_sacrifice_detection():
    from core.game_analyzer import _sacrifice_material

    # Bxf7+ then Kxf7: bishop (330) sacrificed for a pawn (100) -> 230.
    board = chess.Board(
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 4 4"
    )
    move = chess.Move.from_uci("c4f7")
    after = board.copy()
    after.push(move)
    assert _sacrifice_material(board, move, after) == 230, (
        _sacrifice_material(board, move, after)
    )

    # Quiet queen move onto a knight's square: full 900 at risk.
    # (Black knight on f6; the e2 pawn is removed so Qd1-h5 is legal.)
    before = chess.Board("rnbqkb1r/pppppppp/5n2/8/8/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    qh5 = chess.Move.from_uci("d1h5")
    after_qh5 = before.copy()
    after_qh5.push(qh5)
    assert _sacrifice_material(before, qh5, after_qh5) == 900, (
        _sacrifice_material(before, qh5, after_qh5)
    )

    # A normal developing move gives nothing away.
    start = chess.Board()
    nf3 = chess.Move.from_uci("g1f3")
    after_nf3 = start.copy()
    after_nf3.push(nf3)
    assert _sacrifice_material(start, nf3, after_nf3) == 0
    print("  [PASS] SEE sacrifice detection: bishop sac 230, hanging queen 900, "
          "quiet development 0")


def test_brilliant_rules():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    # Competitive position, near-best, playable after -> brilliant.
    before = Evaluation(score_cp=100, best_move_uci="c4f7", best_move_san="Bxf7+")
    after = Evaluation(score_cp=-100, best_move_uci="e8f7", best_move_san="Kxf7")
    assert analyzer.classify_move(
        before, after, "white", move_uci="c4f7", player_rating=1500, sacrifice_cp=230
    ) == "brilliant"

    # Completely winning before the move (EP > 0.995) -> never brilliant.
    winning = Evaluation(score_cp=2000, best_move_uci="c4f7", best_move_san="Bxf7+")
    assert analyzer.classify_move(
        winning, after, "white", move_uci="c4f7", player_rating=1500, sacrifice_cp=230
    ) != "brilliant"

    # Winning but not "completely": Chess.com awarded Brilliant at EP 0.94
    # and 0.99 in the calibration games, so the cap only excludes saturation.
    winning_ish = Evaluation(score_cp=900, best_move_uci="c4f7", best_move_san="Bxf7+")
    assert analyzer.classify_move(
        winning_ish, after, "white", move_uci="c4f7", player_rating=1500,
        sacrifice_cp=230,
    ) == "brilliant"

    # Sacrifices use their own 0.05 near-best tolerance (calibration: one
    # Chess.com Brilliant gave up 0.028 EP, past the 0.02 Excellent band).
    slightly_off = Evaluation(score_cp=100, best_move_uci="d1h5", best_move_san="Qh5")
    off_after = Evaluation(score_cp=-73, best_move_uci="e8f7", best_move_san="Kxf7")
    assert analyzer.classify_move(
        slightly_off, off_after, "white", move_uci="c4f7", player_rating=1500,
        sacrifice_cp=230,
    ) == "brilliant"

    # Bad position after the sacrifice -> never brilliant.
    bad_after = Evaluation(score_cp=600, best_move_uci="e8f7", best_move_san="Kxf7")
    assert analyzer.classify_move(
        before, bad_after, "white", move_uci="c4f7", player_rating=1500, sacrifice_cp=230
    ) != "brilliant"

    # Pawn sacrifices count only below the rating cutoff (1600); exchange
    # sacrifices (rook for minor = 170cp) count at any rating.
    assert analyzer.classify_move(
        before, after, "white", move_uci="c4f7", player_rating=1500, sacrifice_cp=100
    ) == "brilliant"
    assert analyzer.classify_move(
        before, after, "white", move_uci="c4f7", player_rating=2000, sacrifice_cp=100
    ) != "brilliant"
    assert analyzer.classify_move(
        before, after, "white", move_uci="c4f7", player_rating=2000, sacrifice_cp=170
    ) == "brilliant"
    print("  [PASS] Brilliant: sacrifice + near-best + competitive + playable; "
          "pawn sacs rating-gated")


def test_great_only_good_move():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    # Best move far ahead of the second-best line -> the only good move.
    before = Evaluation(
        score_cp=0, best_move_uci="e2e4", best_move_san="e4", second_best_cp=-400
    )
    after = Evaluation(score_cp=0, best_move_uci="e7e5", best_move_san="e5")
    assert analyzer.classify_move(
        before, after, "white", move_uci="e2e4", player_rating=1500
    ) == "great"

    # Second-best line nearly as good -> not Great (it is Best).
    close = Evaluation(
        score_cp=0, best_move_uci="e2e4", best_move_san="e4", second_best_cp=-20
    )
    assert analyzer.classify_move(
        close, after, "white", move_uci="e2e4", player_rating=1500
    ) == "best"

    # A capture that is the only good move stays Best (calibration: Chess.com
    # never marked a capture Great in the labeled games).
    assert analyzer.classify_move(
        before, after, "white", move_uci="e2e4", player_rating=1500,
        move_is_capture=True,
    ) != "great"
    print("  [PASS] Great: only-good-move gap >= 0.15 EP; captures excluded; "
          "small gap stays Best")


def test_miss_rules():
    engine, explainer = _fake_engine_and_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer, book_lookup=_book_lookup)

    # Opponent just blundered, the winning chance is not converted.
    before = Evaluation(score_cp=800, best_move_uci="d1h5", best_move_san="Qh5")
    equal_after = Evaluation(score_cp=0, best_move_uci="g8f6", best_move_san="Nf6")
    cls = analyzer.classify_move(
        before,
        equal_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        opponent_prev_ep_loss=0.35,
    )
    assert cls == "miss", cls

    # Same gift, but the move keeps the winning position -> not Miss.
    keeps_winning = Evaluation(
        score_cp=-700, best_move_uci="g8f6", best_move_san="Nf6"
    )
    cls2 = analyzer.classify_move(
        before,
        keeps_winning,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        opponent_prev_ep_loss=0.35,
    )
    assert cls2 != "miss", cls2

    # Forced mate slipped, even without an opponent blunder.
    mate_before = Evaluation(
        score_cp=10000, best_move_uci="d1h5", best_move_san="Qh5", mate=2
    )
    mate_after = Evaluation(
        score_cp=0, best_move_uci="g8f6", best_move_san="Nf6", mate=None
    )
    cls3 = analyzer.classify_move(
        mate_before, mate_after, "white", move_uci="f3g5", player_rating=1500
    )
    assert cls3 == "miss", cls3

    # The missed mate still wins even when the played line drops material
    # (old "Missed Win" semantics)...
    cls3b = analyzer.classify_move(
        mate_before,
        mate_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        played_line_loss=-900,
    )
    assert cls3b == "miss", cls3b

    # ...but a move that itself allows mate is a Blunder, not a Miss.
    mate_allowed_before = Evaluation(
        score_cp=10000, best_move_uci="d1h5", best_move_san="Qh5", mate=2
    )
    mate_allowed_after = Evaluation(
        score_cp=10000, best_move_uci="g8f6", best_move_san="Nf6", mate=2
    )
    cls3c = analyzer.classify_move(
        mate_allowed_before,
        mate_allowed_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        blunder_consequence=True,
    )
    assert cls3c == "blunder", cls3c

    # A reply loss below the 0.10 gate is Inaccuracy-band, not a Miss
    # (calibration: false Misses clustered at 0.05-0.10 EP).
    modest_before = Evaluation(score_cp=0, best_move_uci="d1h5", best_move_san="Qh5")
    modest_after = Evaluation(score_cp=60, best_move_uci="g8f6", best_move_san="Nf6")
    cls4 = analyzer.classify_move(
        modest_before,
        modest_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        opponent_prev_ep_loss=0.10,
    )
    assert cls4 == "inaccuracy", cls4

    # Above the gate the opponent error + a non-losing best line is a Miss.
    bigger_after = Evaluation(score_cp=200, best_move_uci="g8f6", best_move_san="Nf6")
    cls4b = analyzer.classify_move(
        modest_before,
        bigger_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        opponent_prev_ep_loss=0.10,
    )
    assert cls4b == "miss", cls4b

    # A concrete material loss along the played line keeps it a Blunder even
    # when the move also failed to punish an opponent error (2023 rules:
    # Miss and Blunder are mutually exclusive).
    blunder_after = Evaluation(
        score_cp=500, best_move_uci="g8f6", best_move_san="Nf6"
    )
    cls5 = analyzer.classify_move(
        modest_before,
        blunder_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        blunder_consequence=True,
        opponent_prev_ep_loss=0.30,
        played_line_loss=-600,
    )
    assert cls5 == "blunder", cls5

    # Allowing a forced mate is concrete too.
    mate_allowed_after = Evaluation(
        score_cp=500, best_move_uci="g8f6", best_move_san="Nf6", mate=3
    )
    cls6 = analyzer.classify_move(
        modest_before,
        mate_allowed_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        blunder_consequence=True,
        opponent_prev_ep_loss=0.30,
    )
    assert cls6 == "blunder", cls6

    # A gift while already worse (best line below the EP floor) is not a
    # missed winning chance -- it stays in the normal bands.
    worse_before = Evaluation(score_cp=-300, best_move_uci="d1h5", best_move_san="Qh5")
    cls7 = analyzer.classify_move(
        worse_before,
        blunder_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        opponent_prev_ep_loss=0.30,
    )
    assert cls7 == "mistake", cls7

    # Missed forcing tactic, no gift needed: the best move banks material
    # the reply skips, without hanging anything (Be1 calibration case).
    # Still needs a meaningful EP loss -- Qa8/Ke3 showed tiny give-ups
    # with tactical best lines are just Inaccuracy/Good.
    cls8 = analyzer.classify_move(
        modest_before,
        bigger_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        missed_tactic=True,
    )
    assert cls8 == "miss", cls8

    tiny_after = Evaluation(score_cp=30, best_move_uci="g8f6", best_move_san="Nf6")
    cls8b = analyzer.classify_move(
        modest_before,
        tiny_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        missed_tactic=True,
    )
    assert cls8b == "good", cls8b

    # ...but not when the reply hangs material itself.
    cls9 = analyzer.classify_move(
        modest_before,
        bigger_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        missed_tactic=True,
        played_line_loss=-200,
    )
    assert cls9 != "miss", cls9

    # ...and a tactic never overrides allowing mate.
    cls10 = analyzer.classify_move(
        modest_before,
        mate_allowed_after,
        "white",
        move_uci="f3g5",
        player_rating=1500,
        blunder_consequence=True,
        missed_tactic=True,
    )
    assert cls10 == "blunder", cls10
    print("  [PASS] Miss: 0.10 reply gate, EP floor, missed mate, missed "
          "tactic; concrete loss/mate stays Blunder")


def main() -> int:
    print("=== Running GameAnalyzer review-loop tests ===")
    tests = [
        test_single_evaluation_per_ply,
        test_classification_parity,
        test_include_explanations_false,
        test_target_color_filtering,
        test_analyze_pgn_matches_full_review,
        test_expected_points_model,
        test_best_move_equality_and_blunder_gate,
        test_rating_scaling,
        test_book_contiguity,
        test_material_consequence_helper,
        test_see_sacrifice_detection,
        test_brilliant_rules,
        test_great_only_good_move,
        test_miss_rules,
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
