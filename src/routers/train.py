import logging

import chess
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request

from core.rate_limit import limit_by_clerk_user_id
from engines.maia_engine import get_maia3
from engines.stockfish_engine import StockfishEngine
from schemas.train_schemas import (
    OpponentImportJobResponse,
    OpponentImportRequest,
    OpponentImportStartResponse,
    OpponentProfileListResponse,
    OpponentProfileResponse,
    SparringMoveRequest,
    SparringMoveResponse,
    WeaknessProfileJobResponse,
    WeaknessProfileRequest,
    WeaknessProfileStartResponse,
)
from services.opponent_import import (
    create_opponent_import_job,
    get_opponent_import_job,
    run_opponent_import_job,
)
from services.opponent_repertoire import (
    ensure_opponent_repertoire,
    get_opponent_rating,
    list_opponent_profiles,
    pick_repertoire_move,
)
from services.weakness_profile import (
    create_weakness_profile_job,
    get_weakness_profile_job,
    run_weakness_profile_job,
)

router = APIRouter()
log = logging.getLogger(__name__)


@router.post(
    "/train/opponent-import",
    response_model=OpponentImportStartResponse,
    status_code=202,
)
def start_opponent_import(
    body: OpponentImportRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    _: None = Depends(limit_by_clerk_user_id(limit=5, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    if not (body.lichess_username or body.chesscom_username):
        raise HTTPException(
            status_code=400,
            detail="Provide a Lichess username, Chess.com username, or both.",
        )

    job = create_opponent_import_job(
        requested_by_user_id=clerk_id,
        lichess_username=body.lichess_username,
        chesscom_username=body.chesscom_username,
        limit=body.limit,
    )
    background_tasks.add_task(run_opponent_import_job, job["job_id"])

    return OpponentImportStartResponse(
        job_id=job["job_id"],
        status="queued",
        lichess_username=job["lichess_username"],
        chesscom_username=job["chesscom_username"],
        limit=job["requested_limit"],
    )


@router.get(
    "/train/opponent-import/{job_id}",
    response_model=OpponentImportJobResponse,
)
def get_opponent_import_status(
    request: Request,
    job_id: str = Path(..., min_length=1),
    _: None = Depends(limit_by_clerk_user_id(limit=30, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    job = get_opponent_import_job(job_id=job_id, requested_by_user_id=clerk_id)
    if not job:
        raise HTTPException(status_code=404, detail="Opponent import job not found")

    return OpponentImportJobResponse(**job)


@router.post(
    "/train/weakness-profile",
    response_model=WeaknessProfileStartResponse,
    status_code=202,
)
def start_weakness_profile(
    body: WeaknessProfileRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    _: None = Depends(limit_by_clerk_user_id(limit=3, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    job = create_weakness_profile_job(
        requested_by_user_id=clerk_id,
        source_type=body.source_type,
        provider=body.provider,
        opponent_username=body.opponent_username,
        limit=body.limit,
    )
    background_tasks.add_task(run_weakness_profile_job, job["job_id"])

    return WeaknessProfileStartResponse(
        job_id=job["job_id"],
        status="queued",
        source_type=job["source_type"],
        provider=job["provider"],
        opponent_username=job["opponent_username"],
        limit=job["requested_limit"],
    )


@router.get(
    "/train/weakness-profile/{job_id}",
    response_model=WeaknessProfileJobResponse,
)
def get_weakness_profile_status(
    request: Request,
    job_id: str = Path(..., min_length=1),
    _: None = Depends(limit_by_clerk_user_id(limit=30, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    job = get_weakness_profile_job(job_id=job_id, requested_by_user_id=clerk_id)
    if not job:
        raise HTTPException(status_code=404, detail="Weakness profile job not found")

    return WeaknessProfileJobResponse(**job)


@router.get(
    "/train/opponents",
    response_model=OpponentProfileListResponse,
)
def list_train_opponents(
    request: Request,
    _: None = Depends(limit_by_clerk_user_id(limit=30, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    return OpponentProfileListResponse(
        opponents=[
            OpponentProfileResponse(**profile)
            for profile in list_opponent_profiles(requested_by_user_id=clerk_id)
        ]
    )


@router.post(
    "/train/sparring-move",
    response_model=SparringMoveResponse,
)
def get_sparring_move(
    body: SparringMoveRequest,
    request: Request,
    _: None = Depends(limit_by_clerk_user_id(limit=20, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    try:
        board = chess.Board(body.fen)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid FEN") from exc

    bot_color = chess.WHITE if body.bot_color == "white" else chess.BLACK
    if board.turn != bot_color:
        raise HTTPException(status_code=409, detail="It is not the bot's turn")

    opponent_elo = get_opponent_rating(
        requested_by_user_id=clerk_id,
        provider=body.provider,
        opponent_username=body.opponent_username,
    )
    if opponent_elo is None:
        raise HTTPException(status_code=404, detail="Opponent profile not found")

    ensure_opponent_repertoire(
        requested_by_user_id=clerk_id,
        provider=body.provider,
        opponent_username=body.opponent_username,
    )

    repertoire_choice = pick_repertoire_move(
        requested_by_user_id=clerk_id,
        provider=body.provider,
        opponent_username=body.opponent_username,
        board=board,
    )

    source = "in_book"
    repertoire_frequency = None
    move_uci = ""
    move_san = ""

    if repertoire_choice:
        move_uci = repertoire_choice["move_uci"]
        move_san = repertoire_choice["move_san"]
        repertoire_frequency = repertoire_choice["frequency"]

    try:
        candidate_move = chess.Move.from_uci(move_uci) if move_uci else None
    except ValueError:
        candidate_move = None

    if candidate_move not in board.legal_moves:
        maia_result = get_maia3().best_move(
            board,
            elo=opponent_elo,
            temperature=body.maia_temperature,
        )
        move_uci = maia_result.get("best_move_uci") or ""
        move_san = maia_result.get("best_move_san") or ""
        source = "playing_naturally"
        repertoire_frequency = None
        try:
            candidate_move = chess.Move.from_uci(move_uci)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Maia-3 returned an invalid move") from exc

    if candidate_move not in board.legal_moves:
        raise HTTPException(status_code=502, detail="Engine returned an illegal move")

    try:
        with StockfishEngine() as stockfish:
            eval_before = stockfish.evaluate(board, pov=bot_color)
            candidate_board = board.copy(stack=False)
            candidate_board.push(candidate_move)
            eval_after = stockfish.evaluate(candidate_board, pov=bot_color)
            cp_loss = max(0, round(eval_before.score_cp - eval_after.score_cp))

            if cp_loss >= body.catastrophic_loss_cp and eval_before.best_move_uci:
                stockfish_move = chess.Move.from_uci(eval_before.best_move_uci)
                if stockfish_move in board.legal_moves:
                    candidate_move = stockfish_move
                    move_uci = eval_before.best_move_uci
                    move_san = eval_before.best_move_san
                    source = "correcting_blunder"
    except Exception as exc:  # noqa: BLE001
        log.exception("Sparring move safety check failed")
        raise HTTPException(status_code=502, detail=f"Stockfish safety check failed: {exc}") from exc

    return SparringMoveResponse(
        move_uci=move_uci,
        move_san=move_san or board.san(candidate_move),
        source=source,
        opponent_elo=opponent_elo,
        repertoire_frequency=repertoire_frequency,
        cp_loss=cp_loss,
        best_move_uci=eval_before.best_move_uci,
        best_move_san=eval_before.best_move_san,
    )
