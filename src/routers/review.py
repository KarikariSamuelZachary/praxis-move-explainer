import logging
import os
from typing import Any, Dict, List

import chess.engine
from fastapi import APIRouter, Depends, HTTPException

from core.game_analyzer import GameAnalyzer
from core.rate_limit import limit_by_ip
from engines.stockfish_engine import get_review_stockfish, reset_review_stockfish
from llms.gemini_explainer import GeminiExplainer
from llms.groq_explainer import GroqExplainer
from llms.mock_explainer import MockExplainer
from llms.openai_explainer import OpenAIExplainer
from schemas.review_schemas import ReviewMoveResponse, ReviewRequest

router = APIRouter()
log = logging.getLogger(__name__)


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
            "eval_cp": row.get("eval_cp", 0),
            "eval_mate": row.get("eval_mate"),
            "best_move_san": row.get("best_move_san"),
            "best_move_uci": row.get("best_move_uci"),
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
def review_game(
    body: ReviewRequest,
    _: None = Depends(limit_by_ip(limit=5, window=60)),
):
    pgn = body.pgn.strip()
    if not pgn:
        raise HTTPException(status_code=400, detail="Missing PGN")

    # Long-lived singleton (booted at startup) instead of a fresh Stockfish
    # subprocess per review -- spawning + UCI handshake cost ~0.3-0.7s before
    # the first evaluation even started.
    explainer = _build_explainer()
    log.info("Selected review explainer: %s", explainer.__class__.__name__)

    try:
        engine = get_review_stockfish(depth=int(os.getenv("REVIEW_DEPTH", "18")))
        analyzer = GameAnalyzer(engine=engine, explainer=explainer)
        review_rows = analyzer.analyze_full_game(pgn, target_color=body.target_color)
        return _normalize_review_rows(review_rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (chess.engine.EngineError, RuntimeError) as exc:
        # The singleton handle may be dead (mid-session engine crash) or in an
        # unknown state after a failed/timeout analyse -- drop it so the next
        # review starts a fresh subprocess instead of retrying into a poisoned
        # engine. This never touches the sparring singleton.
        reset_review_stockfish()
        log.exception("Game review engine failed")
        raise HTTPException(status_code=500, detail="Failed to analyze PGN") from exc
    except Exception as exc:
        log.exception("Failed to analyze PGN")
        raise HTTPException(status_code=500, detail="Failed to analyze PGN") from exc
