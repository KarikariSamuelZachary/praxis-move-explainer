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
    print("  [PASS] env parsing (flag, nodes, backstop)")


def test_deterministic_calls_use_nodes_and_fresh_token():
    engine = _RecordingEngine()
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
    assert all(call["nodes"] == review_nodes() for call in engine.calls)
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


def run() -> int:
    print("=== Running analysis-mode tests ===")
    tests = [
        test_env_parsing,
        test_deterministic_calls_use_nodes_and_fresh_token,
        test_non_deterministic_call_shape_unchanged,
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
