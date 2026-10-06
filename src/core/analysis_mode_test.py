"""
Deterministic-mode tests for the review analysis path.

Verified:

  A. REVIEW_DETERMINISTIC / REVIEW_NODES / REVIEW_NODES_BACKSTOP parsing.
  B. deterministic=True routes every position through a nodes-limited,
     fresh-token evaluate() call, with N from the env.
  C. deterministic=False keeps the historical evaluate(board, multipv=...)
     call shape so background callers are untouched.

Run with: cd src && ../venv/bin/python core/analysis_mode_test.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.analysis_mode import (
    DEFAULT_NODES_BACKSTOP_SECONDS,
    DEFAULT_REVIEW_NODES,
    book_fingerprint,
    review_deterministic_enabled,
    review_nodes,
    review_nodes_backstop_seconds,
)
from core.game_analyzer import GameAnalyzer
from llms.mock_explainer import MockExplainer
from schemas.models import Evaluation

PGN = "1. e4 e5 2. Nf3 Nc6"


class _RecordingEngine:
    def __init__(self):
        self.calls = []

    def evaluate(
        self,
        board,
        depth_limit=None,
        pov=None,
        time_limit=None,
        multipv=1,
        nodes=None,
        fresh_token=False,
    ):
        self.calls.append(
            {
                "multipv": multipv,
                "nodes": nodes,
                "time_limit": time_limit,
                "fresh_token": fresh_token,
            }
        )
        return Evaluation(
            score_cp=0.0,
            best_move_uci="e2e4",
            best_move_san="e4",
            mate=None,
            second_best_cp=1.0,
            principal_variation_uci=["e2e4"],
        )


def test_env_parsing():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REVIEW_DETERMINISTIC", None)
        os.environ.pop("REVIEW_NODES", None)
        os.environ.pop("REVIEW_NODES_BACKSTOP", None)
        assert review_deterministic_enabled() is False
        assert review_nodes() == DEFAULT_REVIEW_NODES
        assert review_nodes_backstop_seconds() == DEFAULT_NODES_BACKSTOP_SECONDS

    with patch.dict(
        os.environ,
        {
            "REVIEW_DETERMINISTIC": "1",
            "REVIEW_NODES": "150000",
            "REVIEW_NODES_BACKSTOP": "12.5",
        },
    ):
        assert review_deterministic_enabled() is True
        assert review_nodes() == 150_000
        assert review_nodes_backstop_seconds() == 12.5

    with patch.dict(os.environ, {"REVIEW_NODES": "not-a-number"}):
        assert review_nodes() == DEFAULT_REVIEW_NODES

    with patch.dict(
        os.environ, {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "not-a-number"}
    ):
        try:
            review_nodes()
        except RuntimeError:
            pass
        else:
            raise AssertionError("flag on + invalid REVIEW_NODES must raise")

    with patch.dict(os.environ, {"REVIEW_DETERMINISTIC": "1"}):
        os.environ.pop("REVIEW_NODES", None)
        try:
            review_nodes()
        except RuntimeError:
            pass
        else:
            raise AssertionError("flag on without REVIEW_NODES must raise")
    print("  [PASS] env parsing; flag on requires explicit REVIEW_NODES")


def test_deterministic_calls_use_nodes_and_fresh_token():
    engine = _RecordingEngine()
    with patch.dict(
        os.environ, {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "120000"}
    ):
        analyzer = GameAnalyzer(
            engine=engine,
            explainer=MockExplainer(),
            multipv=2,
            deterministic=True,
        )
        rows = analyzer.analyze_full_game(PGN, include_explanations=False)

        plies = 4
        assert len(rows) == plies + 1
        assert len(engine.calls) == plies + 1
        assert all(call["nodes"] == 120_000 for call in engine.calls)
        assert all(call["fresh_token"] is True for call in engine.calls)
        assert all(
            call["time_limit"] == review_nodes_backstop_seconds()
            for call in engine.calls
        )
    print(f"  [PASS] {len(engine.calls)} searches all nodes+fresh-token")


def test_non_deterministic_call_shape_unchanged():
    engine = _RecordingEngine()
    analyzer = GameAnalyzer(
        engine=engine, explainer=MockExplainer(), deterministic=False
    )
    analyzer.analyze_full_game(PGN, include_explanations=False)

    assert all(call["nodes"] is None for call in engine.calls)
    assert all(call["fresh_token"] is False for call in engine.calls)
    print("  [PASS] flag off keeps the historical evaluate() call shape")


def test_review_max_plies_gating_and_formula():
    from core.analysis_mode import GATE_P99_PLIES, review_max_plies

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REVIEW_DETERMINISTIC", None)
        os.environ.pop("REVIEW_CONTAINER_NPS", None)
        assert review_max_plies() is None, "flag off must have no cap"

    with patch.dict(
        os.environ,
        {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "100000"},
        clear=False,
    ):
        os.environ.pop("REVIEW_CONTAINER_NPS", None)
        assert review_max_plies() == GATE_P99_PLIES

    with patch.dict(
        os.environ,
        {
            "REVIEW_DETERMINISTIC": "1",
            "REVIEW_NODES": "100000",
            "REVIEW_CONTAINER_NPS": "220000",
        },
        clear=False,
    ):
        assert review_max_plies() == int(120.0 * 220000 / 100000) - 1

    with patch.dict(
        os.environ,
        {
            "REVIEW_DETERMINISTIC": "1",
            "REVIEW_NODES": "100000",
            "REVIEW_CONTAINER_NPS": "220000",
            "REVIEW_LLM_SECONDS": "6",
        },
        clear=False,
    ):
        assert review_max_plies() == int(117.0 * 220000 / 100000) - 1
    print("  [PASS] review_max_plies: none when off; formula when on")


def test_book_fingerprint_forces_the_lazy_load():
    """Regression for the stale-409: the mode is computed before the game
    analysis runs, so the fingerprint itself must load the book. Otherwise
    the first review after boot records book=unloaded while its own labels
    already used the loaded book, and every explore is rejected as stale."""
    from services import opening_book

    real_loader = opening_book._load_book_from_db
    opening_book.invalidate_cache()
    try:
        opening_book._load_book_from_db = lambda: (
            {"k": frozenset({"e2e4"})},
            "rev-1",
        )
        assert opening_book.get_book_revision() is None, (
            "the book starts unloaded in this test"
        )
        assert book_fingerprint() == "rev-1", (
            "book_fingerprint must force the load, not read the stale None"
        )
        assert opening_book.get_book_revision() == "rev-1"
    finally:
        opening_book._load_book_from_db = real_loader
        opening_book.invalidate_cache()
    print("  [PASS] book fingerprint forces the lazy book load")


def test_live_mode_compatibility():
    from core.analysis_mode import live_mode_compatible

    review_mode = (
        "rev-det-v1|engine=Stockfish 19|threads=1|hash=16|nodes=150000|"
        "multipv=2|classifier=v1:abc|book=rev1"
    )
    live_mode = (
        "rev-live-v1|engine=Stockfish 19|threads=1|hash=16|depth=20|"
        "classifier=v1:abc|book=rev1"
    )
    assert live_mode_compatible(review_mode, live_mode), (
        "search budget may differ; engine/classifier/book must match"
    )
    assert live_mode_compatible(None, live_mode), "no echo is allowed"
    assert not live_mode_compatible(
        "rev-det-v1|engine=Stockfish 16|classifier=v1:abc|book=rev1",
        live_mode,
    ), "different engine must be stale"
    assert not live_mode_compatible(
        "rev-det-v1|engine=Stockfish 19|classifier=v1:abc|book=other",
        live_mode,
    ), "different book must be stale"
    assert not live_mode_compatible("rev-det-v1|nodes=1|stale", live_mode), (
        "a foreign fingerprint without the parity keys must be rejected"
    )
    print("  [PASS] live mode compatibility: budget may differ, parity keys must match")


def test_multipv_pinned_only_for_deterministic():
    engine = _RecordingEngine()
    try:
        GameAnalyzer(
            engine=engine,
            explainer=MockExplainer(),
            multipv=1,
            deterministic=True,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("deterministic with MultiPV=1 must raise")

    background = GameAnalyzer(
        engine=engine, explainer=MockExplainer(), multipv=1, deterministic=False
    )
    assert background.multipv == 1, "background jobs keep their own width"
    print("  [PASS] deterministic requires MultiPV=2; background keeps 1")


def run() -> int:
    print("=== Running analysis-mode tests ===")
    tests = [
        test_env_parsing,
        test_deterministic_calls_use_nodes_and_fresh_token,
        test_non_deterministic_call_shape_unchanged,
        test_review_max_plies_gating_and_formula,
        test_book_fingerprint_forces_the_lazy_load,
        test_live_mode_compatibility,
        test_multipv_pinned_only_for_deterministic,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"  [FAIL] {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
    print(f"  {len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
