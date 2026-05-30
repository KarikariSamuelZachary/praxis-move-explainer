import logging
import os
import shutil
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from core.game_analyzer import GameAnalyzer
from engines.stockfish_engine import StockfishEngine
from llms.gemini_explainer import GeminiExplainer
from llms.groq_explainer import GroqExplainer
from llms.mock_explainer import MockExplainer
from llms.openai_explainer import OpenAIExplainer
from schemas.review_schemas import ReviewMoveResponse, ReviewRequest

router = APIRouter()
log = logging.getLogger(__name__)


def _stockfish_path() -> str:
    configured_path = os.getenv("STOCKFISH_PATH")
    if configured_path:
        return configured_path

    discovered_path = shutil.which("stockfish")
    if discovered_path:
        return discovered_path

    for candidate in (
        "/workspace/.apt/usr/games/stockfish",
        "/workspace/.apt/usr/bin/stockfish",
        "/app/.apt/usr/games/stockfish",
        "/app/.apt/usr/bin/stockfish",
        "/usr/games/stockfish",
        "/usr/bin/stockfish",
    ):
        if os.path.exists(candidate):
            return candidate

    return "stockfish"


def _build_explainer():
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

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key:
        return OpenAIExplainer(
            api_key=openai_api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        )

    return MockExplainer()


def _normalize_review_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


@router.post("/review", response_model=List[ReviewMoveResponse])
def review_game(request: ReviewRequest):
    pgn = request.pgn.strip()
    if not pgn:
        raise HTTPException(status_code=400, detail="Missing PGN")

    engine = StockfishEngine(
        stockfish_path=_stockfish_path(),
        depth=int(os.getenv("REVIEW_DEPTH", "18")),
    )

    try:
        engine.start()
        analyzer = GameAnalyzer(engine=engine, explainer=_build_explainer())
        review_rows = analyzer.analyze_full_game(pgn, target_color=request.target_color)
        return _normalize_review_rows(review_rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Failed to analyze PGN")
        raise HTTPException(status_code=500, detail="Failed to analyze PGN") from exc
    finally:
        engine.close()
