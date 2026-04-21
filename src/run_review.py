#!/usr/bin/env python3
"""Run full-game review analysis and emit JSON for the Next.js API route."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from core.game_analyzer import GameAnalyzer
from engines.stockfish_engine import StockfishEngine
from llms.gemini_explainer import GeminiExplainer
from llms.groq_explainer import GroqExplainer
from llms.mock_explainer import MockExplainer


def _load_environment() -> None:
    """Load local env files for direct CLI execution."""
    repo_root = Path(__file__).resolve().parent.parent
    load_dotenv(repo_root / ".env")
    load_dotenv(repo_root / "src" / ".env")
    load_dotenv(repo_root / "frontend" / ".env.local")


def _build_explainer():
    """Prefer real providers when configured; otherwise fall back to mock."""
    groq_api_key = os.getenv("GROQ_API_KEY")
    if groq_api_key:
        return GroqExplainer(
            api_key=groq_api_key,
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        )

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if gemini_api_key:
        return GeminiExplainer(
            api_key=gemini_api_key,
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        )

    return MockExplainer()


def _normalize_review_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapt analyzer output to the frontend GameReviewMove shape."""
    normalized_rows: List[Dict[str, Any]] = []

    for row in rows:
        normalized_row: Dict[str, Any] = {
            "fen": row["fen"],
            "san": row["san"],
            "color": row["color"],
            "classification": row["classification"],
            "cp_loss": row["cp_loss"],
        }

        explanation = row.get("explanation")
        if explanation:
            normalized_row["explanation"] = {
                "explanation": explanation.get("why_failed") or explanation.get("why_good") or "",
                "concept": explanation.get("concept_involved"),
                "tip": explanation.get("typical_pattern"),
            }

        normalized_rows.append(normalized_row)

    return normalized_rows


def main() -> int:
    _load_environment()

    pgn = sys.stdin.read().strip()
    if not pgn:
        print("Missing PGN input on stdin", file=sys.stderr)
        return 1

    stockfish_path = os.getenv("STOCKFISH_PATH", "stockfish")
    depth = int(os.getenv("REVIEW_DEPTH", "18"))

    engine = StockfishEngine(stockfish_path=stockfish_path, depth=depth)
    explainer = _build_explainer()
    analyzer = GameAnalyzer(engine=engine, explainer=explainer)

    try:
        engine.start()
        review_rows = analyzer.analyze_full_game(pgn)
        normalized_rows = _normalize_review_rows(review_rows)
        print(json.dumps(normalized_rows))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
