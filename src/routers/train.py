import logging

import chess
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request

from core.rate_limit import limit_by_clerk_user_id
from engines.maia_engine import MaiaUnavailableError, get_maia3, is_maia_available
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
from services.opponent_style import compute_opponent_style
from services.opponent_style_reranker import rerank_candidates
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
        # About to fall back to Maia-3. is_maia_available() does a real
        # subprocess liveness check (not just a boot-time flag), so it
        # returns False both when Maia never started at boot AND when it
        # crashed mid-session. Short-circuit here with a clear typed 503
        # rather than letting an unhandled chess.engine exception fall
        # through into the Stockfish `except` block below (which mislabels
        # everything as a Stockfish failure). 503 = the request was fine;
        # the dependency is down.
        if not is_maia_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Maia-3 is unavailable — the opponent has no book move "
                    "for this position and the human-like model is not "
                    "running. See /api/debug/maia-health for status."
                ),
            )

        try:
            maia_engine = get_maia3()

            # --- Style-bias re-ranker (progressive enhancement) ------------
            # When the opponent has >= MIN_STYLE_GAMES imported, we lean
            # Maia's candidate distribution toward the opponent's observed
            # playing style (sacrifice frequency) via rerank_candidates().
            # When the style profile is insufficient (or the style layer
            # itself fails), we silently fall back to the original
            # best_move() path -- per the wire-up contract the request MUST
            # NOT block or fail because the style layer is unavailable.
            #
            # Two failure axes get separate try/excepts:
            #   * compute_opponent_style() reads + python-chess-parses the
            #     opponent's full PGN corpus from the DB -- it can fail in
            #     ways unrelated to Maia (corrupt PGN rows, transient DB
            #     errors). A failure here degrades to unbiased Maia.
            #   * rerank_candidates() is pure-Python (board copies +
            #     per-candidate sac detection); if it raises it degrades to
            #     the unbiased best_move() call.
            # best_move_candidates() is itself a Maia call -- its
            # MaiaUnavailableError propagates up to the outer handler (503),
            # correctly distinguishing "engine down" from "style layer
            # hiccupped".
            #
            # LATENCY SURFACE: compute_opponent_style replays every imported
            # PGN on each call (no cache yet). For a 200-game opponent this
            # adds ~1-2s to every out-of-book sparring move. Memoization is
            # a deliberate future task; the wire-up intentionally pays the
            # cost up front so the bias can be live-verified.
            try:
                style = compute_opponent_style(
                    requested_by_user_id=clerk_id,
                    provider=body.provider,
                    opponent_username=body.opponent_username,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "compute_opponent_style failed for %s/%s — falling "
                    "back to unbiased Maia. Underlying: %s",
                    body.provider, body.opponent_username, exc,
                )
                style = None

            move_uci = ""
            move_san = ""

            if style is not None and style.get("sufficient"):
                candidates = maia_engine.best_move_candidates(
                    board,
                    multipv=5,
                    self_elo=opponent_elo,
                    oppo_elo=opponent_elo,
                )
                try:
                    rerank = rerank_candidates(
                        candidates=candidates,
                        style=style,
                        board=board,
                    )
                    move_uci = rerank.get("chosen_move_uci", "") or ""
                    if move_uci:
                        move_san = board.san(chess.Move.from_uci(move_uci))
                        log.info(
                            "style-bias re-ranker invoked: opponent=%s/%s "
                            "sacrifice_frequency=%s applied_bias=%s "
                            "source=%s chose=%s",
                            body.provider, body.opponent_username,
                            rerank.get("sacrifice_frequency"),
                            rerank.get("applied_bias"),
                            rerank.get("source"),
                            move_uci,
                        )
                    else:
                        log.debug(
                            "rerank_candidates returned no move "
                            "(source=%s) — falling back to best_move()",
                            rerank.get("source"),
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "rerank_candidates failed for %s/%s — falling "
                        "back to unbiased Maia. Underlying: %s",
                        body.provider, body.opponent_username, exc,
                    )
                    move_uci = ""
                    move_san = ""

            if not move_uci:
                # Fallback: insufficient data, reranker failure, or no
                # candidates produced a usable move. Uses best_move() with
                # the configured temperature -- the sparring flow's
                # pre-wiring behavior. maia_engine is the same singleton as
                # best_move_candidates above; if it had died mid-request the
                # prior call already raised MaiaUnavailableError.
                maia_result = maia_engine.best_move(
                    board,
                    elo=opponent_elo,
                    temperature=body.maia_temperature,
                )
                move_uci = maia_result.get("best_move_uci") or ""
                move_san = maia_result.get("best_move_san") or ""
        except MaiaUnavailableError as exc:
            # Typed "engine unavailable" — covers never-started, mid-session
            # crash (best_move() marks the subprocess dead and re-raises this
            # type), and the defensive pre-check branch above. Always 503:
            # the request was fine, the dependency is down.
            raise HTTPException(
                status_code=503,
                detail=(
                    "Maia-3 is unavailable — the opponent has no book move "
                    "for this position and the human-like model is not "
                    "running. See /api/debug/maia-health for status."
                ),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # Broader fallback — matches the maia_debug endpoint's handling.
            # Anything unexpected (e.g. an asyncio timeout, a chess.Board
            # edge case) must NOT surface as an unhandled 500; it surfaces as
            # a 502 with the underlying error so the sparring UI can show a
            # clear message and the operator can triage from the log line.
            log.exception("Unexpected Maia-3 failure in sparring flow")
            raise HTTPException(
                status_code=502,
                detail=f"Maia-3 inference failed: {exc}",
            ) from exc

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
