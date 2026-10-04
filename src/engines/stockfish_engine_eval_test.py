"""
Live harness for evaluate() search telemetry and the nodes backstop.

Verified:

  A. nodes=5000 + time backstop -> Evaluation.nodes/depth populated,
     backstop_fired False.
  B. Unreachable nodes + tiny time -> backstop_fired True and nodes < limit.
  C. Terminal position -> nodes is None and the None guard keeps
     backstop_fired False (previously `None < N` would raise TypeError).

Run with: cd src && ../venv/bin/python engines/stockfish_engine_eval_test.py
Requires: a Stockfish binary (STOCKFISH_PATH or PATH).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from engines.stockfish_engine import StockfishEngine, resolve_stockfish_path

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
STALEMATE = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"


def _engine():
    engine = StockfishEngine(stockfish_path=resolve_stockfish_path())
    engine.start()
    return engine


def test_nodes_telemetry_and_no_false_backstop():
    engine = _engine()
    try:
        evaluation = engine.evaluate(
            chess.Board(START),
            nodes=5000,
            time_limit=30.0,
            multipv=2,
            fresh_token=True,
        )
    finally:
        engine.close()

    assert evaluation.nodes is not None, "nodes not reported"
    assert evaluation.nodes >= 5000, f"nodes={evaluation.nodes} < requested 5000"
    assert evaluation.depth is not None, "depth not reported"
    assert evaluation.backstop_fired is False, "backstop fired with a 30s budget"
    print(
        f"  [PASS] nodes={evaluation.nodes} depth={evaluation.depth} "
        f"nps={evaluation.nps} backstop={evaluation.backstop_fired}"
    )


def test_backstop_fires_with_tiny_time():
    engine = _engine()
    try:
        evaluation = engine.evaluate(
            chess.Board(START),
            nodes=50_000_000,
            time_limit=0.05,
            multipv=2,
            fresh_token=True,
        )
    finally:
        engine.close()

    assert evaluation.backstop_fired is True, "tiny time budget did not fire"
    assert evaluation.nodes is not None and evaluation.nodes < 50_000_000
    print(
        f"  [PASS] tiny backstop fired at nodes={evaluation.nodes} "
        f"(limit 50,000,000)"
    )


def test_terminal_nodes_none_guard():
    engine = _engine()
    try:
        evaluation = engine.evaluate(
            chess.Board(STALEMATE),
            nodes=5000,
            time_limit=30.0,
            multipv=2,
            fresh_token=True,
        )
    finally:
        engine.close()

    assert evaluation.nodes is None, f"terminal nodes={evaluation.nodes}"
    assert evaluation.backstop_fired is False, "None guard failed"
    print("  [PASS] terminal position: nodes None, backstop False")


def run() -> int:
    print("=== Running Stockfish evaluate telemetry tests ===")
    tests = [
        test_nodes_telemetry_and_no_false_backstop,
        test_backstop_fires_with_tiny_time,
        test_terminal_nodes_none_guard,
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
