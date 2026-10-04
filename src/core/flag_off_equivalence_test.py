"""
Flag-off row-for-row equivalence against 62e8c19.

With REVIEW_DETERMINISTIC off, GameAnalyzer must produce byte-identical rows
to the pre-deterministic code. This test runs a fake engine (fixed evals, no
engine subprocess) over three short games ending in checkmate, stalemate and
insufficient material, and compares every row against a fixture captured from
62e8c19 (the commit before the deterministic series).

The fixture is generated with:
  cp src/core/flag_off_equivalence_test.py /tmp/opencode/base62/src/core/
  cd /tmp/opencode/base62 && ../..//venv/bin/python src/core/flag_off_equivalence_test.py \
      --capture <repo>/src/core/flag_off_equivalence_fixture.json

Run with: cd src && ../venv/bin/python core/flag_off_equivalence_test.py
"""
import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess  # noqa: E402

from core.game_analyzer import GameAnalyzer  # noqa: E402
from llms.mock_explainer import MockExplainer  # noqa: E402
from schemas.models import Evaluation  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flag_off_equivalence_fixture.json")

GAMES = {
    "mate": "1. f3 e5 2. g4 Qh4#",
    "stalemate": (
        '[SetUp "1"]\n[FEN "7k/Q7/6K1/8/8/8/8/8 w - - 0 1"]\n\n1. Qf7\n'
    ),
    "insufficient_material": (
        '[SetUp "1"]\n[FEN "8/8/8/4k3/8/4K3/8/8 w - - 0 1"]\n\n1. Kd3\n'
    ),
}


class FakeEngine:
    """Deterministic evaluations; never returns a mate score."""

    def __init__(self):
        self.calls = 0

    def evaluate(
        self,
        board: chess.Board,
        depth_limit=None,
        pov=None,
        time_limit=None,
        multipv=1,
        nodes=None,
        fresh_token=False,
    ) -> Evaluation:
        self.calls += 1
        legal = list(board.legal_moves)
        if legal:
            first = legal[0]
            return Evaluation(
                score_cp=float(board.fullmove_number),
                best_move_uci=first.uci(),
                best_move_san=board.san(first),
                mate=None,
                second_best_cp=float(board.fullmove_number),
                principal_variation_uci=[first.uci()],
            )
        return Evaluation(
            score_cp=0.0,
            best_move_uci="",
            best_move_san="(none)",
            mate=None,
            second_best_cp=None,
            principal_variation_uci=[],
        )


def analyze(pgn: str) -> List[Dict[str, Any]]:
    analyzer = GameAnalyzer(engine=FakeEngine(), explainer=MockExplainer())
    return analyzer.analyze_full_game(pgn, include_explanations=False)


def normalized(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean = []
    for row in rows:
        assert row.get("draw_claimable", False) is False, row
        clean.append({k: v for k, v in row.items() if k != "draw_claimable"})
    return clean


def capture(path: str) -> None:
    fixture = {name: normalized(analyze(pgn)) for name, pgn in GAMES.items()}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, indent=2, sort_keys=True)
    counts = {name: len(rows) for name, rows in fixture.items()}
    print(f"captured {counts} -> {path}")


def run() -> int:
    print("=== Flag-off equivalence vs 62e8c19 ===")
    with open(FIXTURE, encoding="utf-8") as fh:
        fixture = json.load(fh)

    failures = 0
    for name, pgn in GAMES.items():
        expected = fixture.get(name)
        if expected is None:
            print(f"  [FAIL] {name}: missing from fixture")
            failures += 1
            continue
        actual = normalized(analyze(pgn))
        if len(actual) != len(expected):
            print(f"  [FAIL] {name}: {len(actual)} rows, expected {len(expected)}")
            failures += 1
            continue
        for index, (left, right) in enumerate(zip(expected, actual)):
            if left != right:
                print(f"  [FAIL] {name} row {index}:\n    old={left}\n    new={right}")
                failures += 1
                break
        else:
            print(f"  [PASS] {name}: {len(actual)} rows identical to 62e8c19")
    print(f"  {len(GAMES) - failures}/{len(GAMES)} games equivalent")
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", default=None, help="Write the fixture instead of comparing.")
    args = parser.parse_args()
    if args.capture:
        capture(args.capture)
        raise SystemExit(0)
    raise SystemExit(1 if run() else 0)
