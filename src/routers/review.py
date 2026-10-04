import logging
import os
import threading
from collections import OrderedDict
from io import StringIO
from typing import Any, Dict, List, Optional

import chess.engine
import chess.pgn
from fastapi import APIRouter, Depends, HTTPException

from core.analysis_mode import (
    REVIEW_MULTIPV,
    current_mode_string,
    review_deterministic_enabled,
    review_max_plies,
    review_nodes,
)
from core.auth import require_clerk_user_id
from core.game_analyzer import GameAnalyzer, pv_to_san
from core.rate_limit import limit_by_clerk_user_id, limit_by_ip
from engines.stockfish_engine import (
    get_review_engine_name,
    get_review_stockfish,
    reset_review_stockfish,
)
from llms.gemini_explainer import GeminiExplainer
from llms.groq_explainer import GroqExplainer
from llms.mock_explainer import MockExplainer
from llms.openai_explainer import OpenAIExplainer
from schemas.models import Evaluation
from schemas.review_schemas import (
    ReviewMoveResponse,
    ReviewRequest,
    SandboxLine,
    SandboxMoveRequest,
    SandboxMoveResponse,
)
from services.opening_book import is_book_move

router = APIRouter()
log = logging.getLogger(__name__)

# Interactive sandbox caching: deterministic fixed-N results are pure
# functions of (position, move, rating, mode string), so repeat explores are
# served without touching the engine. Bounded LRU, process-local.
SANDBOX_CACHE_MAX = 2048
_SANDBOX_LOCK = threading.Lock()
_SANDBOX_EVAL_CACHE: "OrderedDict[str, Evaluation]" = OrderedDict()
_SANDBOX_RESULT_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _cache_get(cache: "OrderedDict[str, Any]", key: str) -> Optional[Any]:
    with _SANDBOX_LOCK:
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value


def _cache_put(cache: "OrderedDict[str, Any]", key: str, value: Any) -> None:
    with _SANDBOX_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > SANDBOX_CACHE_MAX:
            cache.popitem(last=False)


def _resolve_sandbox_move(board: chess.Board, raw: str) -> chess.Move:
    """Accept UCI or SAN and return the legal move, or raise ValueError."""
    candidate = raw.strip()
    if not candidate:
        raise ValueError("Missing move")
    try:
        move = chess.Move.from_uci(candidate)
        if move in board.legal_moves:
            return move
    except ValueError:
        pass
    try:
        return board.parse_san(candidate)
    except ValueError as exc:
        raise ValueError(f"Illegal move: {raw}") from exc


def _line_from_eval(
    board: chess.Board, evaluation: Evaluation
) -> SandboxLine:
    """One suggestion line, evaluations from White's point of view."""
    white_sign = 1 if board.turn == chess.WHITE else -1
    pv_uci = list(evaluation.principal_variation_uci)
    return SandboxLine(
        move_uci=evaluation.best_move_uci or None,
        move_san=(
            evaluation.best_move_san
            if evaluation.best_move_san and evaluation.best_move_san != "(none)"
            else None
        ),
        eval_cp=round(white_sign * evaluation.score_cp, 1),
        eval_mate=(
            white_sign * evaluation.mate
            if evaluation.mate is not None
            else None
        ),
        pv_uci=pv_uci,
        pv_san=pv_to_san(board, pv_uci),
    )


def _second_line_from_eval(
    board: chess.Board, evaluation: Evaluation
) -> Optional[SandboxLine]:
    if not evaluation.second_best_move_uci and not evaluation.second_best_pv_uci:
        return None
    white_sign = 1 if board.turn == chess.WHITE else -1
    pv_uci = list(evaluation.second_best_pv_uci)
    return SandboxLine(
        move_uci=evaluation.second_best_move_uci,
        move_san=evaluation.second_best_move_san,
        eval_cp=(
            round(white_sign * evaluation.second_best_cp, 1)
            if evaluation.second_best_cp is not None
            else None
        ),
        pv_uci=pv_uci,
        pv_san=pv_to_san(board, pv_uci),
    )


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


def _mainline_plies(pgn: str) -> int:
    game = chess.pgn.read_game(StringIO(pgn))
    if game is None:
        raise HTTPException(status_code=400, detail="Invalid PGN")
    return sum(1 for _ in game.mainline_moves())


def _normalize_review_rows(
    rows: List[Dict[str, Any]], include_extras: bool = False
) -> List[Dict[str, Any]]:
    normalized_rows: List[Dict[str, Any]] = []

    for row in rows:
        normalized_row: Dict[str, Any] = {
            "fen": row["fen"],
            "san": row["san"],
            "color": row["color"],
            "classification": row["classification"],
            "cp_loss": row["cp_loss"],
            "ep_loss": row.get("ep_loss", 0),
            "eval_cp": row.get("eval_cp", 0),
            "eval_mate": row.get("eval_mate"),
            "best_move_san": row.get("best_move_san"),
            "best_move_uci": row.get("best_move_uci"),
        }

        # Sandbox extras: forwarded only when the route asked for them, so
        # the flag-off response keeps the historical field set exactly.
        if include_extras:
            for key in (
                "fen_before",
                "player_rating",
                "raw_ep_loss",
                "second_best_cp",
                "second_best_move_uci",
                "second_best_move_san",
                "second_best_pv_uci",
            ):
                if key in row:
                    normalized_row[key] = row[key]

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
    # Declared first on purpose: FastAPI resolves dependencies in signature
    # order, so a missing user is rejected before limit_by_clerk_user_id can
    # fall back to the client IP.
    _clerk_id: str = Depends(require_clerk_user_id),
    _ip: None = Depends(limit_by_ip(limit=5, window=60)),
    _user: None = Depends(limit_by_clerk_user_id(limit=5, window=60)),
):
    pgn = body.pgn.strip()
    if not pgn:
        raise HTTPException(status_code=400, detail="Missing PGN")

    # Deterministic mode is bounded by the 240s proxy ceiling; the old
    # behavior (flag off) has no ply cap, matching the pre-cap route.
    deterministic = review_deterministic_enabled()
    max_plies = review_max_plies()
    if max_plies is not None:
        plies = _mainline_plies(pgn)
        if plies > max_plies:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Game too long for review: {plies} plies "
                    f"(max {max_plies})"
                ),
            )

    # Long-lived singleton (booted at startup) instead of a fresh Stockfish
    # subprocess per review -- spawning + UCI handshake cost ~0.3-0.7s before
    # the first evaluation even started.
    explainer = _build_explainer()
    log.info("Selected review explainer: %s", explainer.__class__.__name__)

    try:
        engine = get_review_stockfish(depth=int(os.getenv("REVIEW_DEPTH", "18")))
        log.info("Review deterministic mode: %s", deterministic)
        analyzer = GameAnalyzer(
            engine=engine,
            explainer=explainer,
            book_lookup=is_book_move,
            # MultiPV=2 gives the Great-move check the second-best line
            # ("only good move") without a second search per position.
            multipv=REVIEW_MULTIPV,
            deterministic=deterministic,
        )
        extras = deterministic
        review_rows = analyzer.analyze_full_game(
            pgn, target_color=body.target_color, include_extras=extras
        )
        return _normalize_review_rows(review_rows, include_extras=extras)
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


@router.post("/review/live", response_model=SandboxMoveResponse)
def review_live(
    body: SandboxMoveRequest,
    _clerk_id: str = Depends(require_clerk_user_id),
    _ip: None = Depends(limit_by_ip(limit=30, window=60)),
    _user: None = Depends(limit_by_clerk_user_id(limit=60, window=60)),
):
    """Label one explored move with the batch review's exact settings.

    Deterministic fixed-N search, same classifier, same mode string. Results
    are cached per (mode, fen, move, rating), so re-exploring a line is free.
    Disabled (403) unless REVIEW_DETERMINISTIC is on.
    """
    if not review_deterministic_enabled():
        raise HTTPException(status_code=403, detail="Sandbox is disabled")

    try:
        board = chess.Board(body.fen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid FEN: {exc}") from exc
    try:
        move = _resolve_sandbox_move(board, body.move)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    mode = current_mode_string(
        engine_name=get_review_engine_name(),
        multipv=REVIEW_MULTIPV,
        nodes=review_nodes(),
    )
    result_key = f"{mode}|{body.player_rating}|{body.fen}|{move.uci()}"
    cached = _cache_get(_SANDBOX_RESULT_CACHE, result_key)
    if cached is not None:
        response = dict(cached)
        response["cached"] = True
        return SandboxMoveResponse(**response)

    try:
        engine = get_review_stockfish(depth=int(os.getenv("REVIEW_DEPTH", "18")))
        analyzer = GameAnalyzer(
            engine=engine,
            explainer=MockExplainer(),
            book_lookup=is_book_move,
            multipv=REVIEW_MULTIPV,
            deterministic=True,
        )
        eval_key = f"{mode}|{body.fen}"
        eval_before = _cache_get(_SANDBOX_EVAL_CACHE, eval_key)
        if eval_before is None:
            eval_before = analyzer.evaluate_position(board)
            _cache_put(_SANDBOX_EVAL_CACHE, eval_key, eval_before)
        row, _, _ = analyzer.analyze_ply(
            chess.Board(body.fen),
            move,
            eval_before,
            is_book_move=is_book_move(board, move),
            player_rating=body.player_rating,
            include_extras=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (chess.engine.EngineError, RuntimeError) as exc:
        reset_review_stockfish()
        log.exception("Sandbox live engine failed")
        raise HTTPException(status_code=500, detail="Failed to analyze move") from exc

    before = chess.Board(body.fen)
    response = SandboxMoveResponse(
        classification=row["classification"],
        cp_loss=row["cp_loss"],
        ep_loss=row["ep_loss"],
        eval_cp=row["eval_cp"],
        eval_mate=row["eval_mate"],
        color=row["color"],
        fen_before=row["fen_before"],
        fen=row["fen"],
        move_san=row["san"],
        move_uci=move.uci(),
        best=_line_from_eval(before, eval_before),
        second_best=_second_line_from_eval(before, eval_before),
        mode=mode,
    )
    _cache_put(
        _SANDBOX_RESULT_CACHE,
        result_key,
        response.model_dump(exclude={"cached"}),
    )
    return response


@router.get("/review/capabilities")
def review_capabilities(
    _clerk_id: str = Depends(require_clerk_user_id),
):
    """Capability signal for the sandbox UI: flag state and mode fingerprint.

    The frontend hides the lens when sandbox_enabled is false; the mode
    string lets the client echo it back so a stale review (deployed under a
    different engine/classifier/book revision) can be detected.
    """
    deterministic = review_deterministic_enabled()
    return {
        "sandbox_enabled": deterministic,
        "mode": current_mode_string(
            engine_name=get_review_engine_name(),
            multipv=REVIEW_MULTIPV,
            nodes=review_nodes(),
        ),
    }
