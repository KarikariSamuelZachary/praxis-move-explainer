import logging
import time
from typing import Any, Dict, Literal, Optional

import chess
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Request

from core import database
from core.rate_limit import limit_by_clerk_user_id
from engines.maia_engine import MaiaUnavailableError, get_maia3, is_maia_available
from engines.stockfish_engine import StockfishEngine
from schemas.train_schemas import (
    OpponentAnalysisStatusResponse,
    OpponentDataClearResponse,
    OpponentImportJobResponse,
    OpponentImportRequest,
    OpponentImportStartResponse,
    OpponentProfileInfoResponse,
    OpponentProfileListResponse,
    OpponentProfileResponse,
    SparringMoveRequest,
    SparringMoveResponse,
    WeaknessProfileJobResponse,
    WeaknessProfileRequest,
    WeaknessProfileStartResponse,
)
from services.opponent_game_analysis import get_opponent_analysis_status
from services.opponent_import import (
    clear_opponent_data,
    create_opponent_import_job,
    get_opponent_import_job,
    run_opponent_import_job,
)
from services.opponent_repertoire import (
    ensure_opponent_repertoire,
    get_opponent_rating,
    list_opponent_profiles,
    pick_near_repertoire_moves,
    pick_repertoire_move,
)
from services.opponent_style import compute_opponent_style
from services.opponent_style_reranker import rerank_candidates
from services.opponent_traps import compute_exploitable_traps
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


@router.delete(
    "/train/opponent-data",
    response_model=OpponentDataClearResponse,
)
def clear_opponent_sparring_data(
    request: Request,
    provider: Optional[Literal["lichess", "chesscom"]] = Query(
        None,
        description="If set with opponent_username, clear only that opponent. "
                    "If omitted (with opponent_username also omitted), clear "
                    "ALL opponent sparring data for the user.",
    ),
    opponent_username: Optional[str] = Query(
        None,
        min_length=1,
        max_length=100,
        description="If set with provider, clear only that opponent. If "
                    "omitted (with provider also omitted), clear ALL "
                    "opponent sparring data for the user.",
    ),
    _: None = Depends(limit_by_clerk_user_id(limit=5, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    # Per-opponent clear requires BOTH identifiers; a partial pair is a
    # client bug. Full clear requires NEITHER. Anything else is ambiguous.
    if (provider is None) != (opponent_username is None):
        raise HTTPException(
            status_code=400,
            detail="Pass both provider and opponent_username for a "
                   "per-opponent clear, or neither for a full clear.",
        )

    result = clear_opponent_data(
        requested_by_user_id=clerk_id,
        provider=provider,
        opponent_username=opponent_username,
    )
    # Drop the matching style/traps cache entry so the next sparring move
    # recomputes against the freshly-cleared data instead of a stale entry.
    _invalidate_style_traps_cache(
        requested_by_user_id=clerk_id,
        provider=provider,
        opponent_username=opponent_username,
    )
    return OpponentDataClearResponse(**result)


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


# --- Opponent profile info (avatar + verified) -----------------------------
#
# Lazy-fetch endpoint that backs the Opponent Preparation page's profile
# card. Not part of the profile list response because (a) it's a per-card
# detail and would inflate the list payload if every imported opponent
# were expanded, and (b) the upstream providers expose avatar/verified
# ONLY through the public profile API (chess.com) or not at all (lichess).
#
# In-process TTL cache keyed by (provider, username). 1 hour is short
# enough to pick up an avatar change the player makes (rare) but long
# enough that re-mounting the sparring page within a session doesn't
# re-hit chess.com. Cache lives in module scope so every FastAPI worker
# gets its own copy — fine for a non-critical display hint.
_CHESSCOM_PROFILE_TTL_SECONDS = 3600
_chesscom_profile_cache: dict[tuple[str, str], tuple[float, Dict[str, Any]]] = {}


# --- style + traps in-memory cache (sparring hot path) --------------------
#
# compute_opponent_style (~2.4s cold-cache for a 500-game opponent at
# ~4.8ms/game PGN-parsing cost; ~960ms for the previous 200-game cap) and
# compute_exploitable_traps (~6ms) are recomputed on every out-of-book
# sparring move. The opponent's imported games never change mid-session,
# so both are cached together in one process-local entry and reused until
# the TTL expires. Same pattern as _chesscom_profile_cache above: module
# scope, no cross-worker sharing, time-based invalidation only.
#
# Cache entry shape: (cached_at_unix, style, exploitable_trap_keys,
# traps_ok). style is only ever stored when compute_opponent_style
# succeeded (it always returns a dict on success, so a stored style is
# never None). traps_ok is False when traps was NOT successfully computed
# (either it failed, or style was insufficient so it was never needed); a
# hit then reuses the expensive style profile but re-runs traps rather
# than trusting a poisoned None.
_SPARRING_STYLE_TRAPS_TTL_SECONDS = 1800  # 30 minutes
# Cache key includes the sparring time control so a session that switches
# speed (e.g. bullet then rapid vs the same opponent) does not reuse a
# style profile computed under the wrong TC weighting. The key stores the
# raw normalized TC string (not the resolved bucket) so two
# differently-spelled-but-equivalent labels are conservative cache misses
# (recompute) rather than a stale-bucket correctness risk.
_sparring_style_traps_cache: dict[
    tuple[str, str, str, str],
    tuple[float, Optional[Dict[str, Any]], Optional[set], bool],
] = {}


def _sparring_style_traps_cache_key(
    requested_by_user_id: str,
    provider: str,
    opponent_username: str,
    time_control: Optional[str] = None,
) -> tuple[str, str, str, str]:
    """Canonical cache key. Username is lowercased to match the SQL LOWER()
    the style/traps queries use, so a casing change in the request can't
    fragment the entry. The time-control string (lowercased) is the 4th
    element so different sparring speeds get separate cache entries."""
    return (
        requested_by_user_id,
        provider,
        (opponent_username or "").strip().lower(),
        (time_control or "").strip().lower(),
    )


def _invalidate_style_traps_cache(
    requested_by_user_id: str,
    provider: Optional[str] = None,
    opponent_username: Optional[str] = None,
) -> None:
    """Drop cached style/traps for one opponent, or the whole user.

    Best-effort nice-to-have: TTL is the primary invalidation mechanism.
    """
    if provider and opponent_username:
        # Drop ALL time-control variants for this opponent (the TC element
        # varies across cache entries for one opponent, so a single pop on
        # the 3-element prefix would miss; iterate the matching prefix).
        prefix = (
            requested_by_user_id,
            provider,
            (opponent_username or "").strip().lower(),
        )
        for key in [
            k for k in _sparring_style_traps_cache if k[:3] == prefix
        ]:
            _sparring_style_traps_cache.pop(key, None)
        return
    for key in [
        k for k in _sparring_style_traps_cache if k[0] == requested_by_user_id
    ]:
        _sparring_style_traps_cache.pop(key, None)


def _chesscom_profile_cached(username: str) -> Optional[Dict[str, Any]]:
    """Fetch + cache the chess.com profile payload for `username`.

    Returns None on any upstream failure (404, network error, malformed
    body) so the frontend always gets a response and can fall back to
    an initials avatar + no verified badge — never an HTTP error. Cache
    misses go to the network; cache hits return the stored dict without
    touching chess.com.
    """
    import time as _time

    key = (username.strip().lower(),)
    # newer Python: dict tuple key uses (,) for one element; the real
    # cache key below is (provider, username). Re-key to the provider-
    # aware form to keep callers from colliding across providers in
    # future if Lichess ever adds avatars.
    now = _time.time()
    cache_key = ("chesscom", username.strip().lower())
    cached = _chesscom_profile_cache.get(cache_key)
    if cached is not None:
        cached_at, payload = cached
        if now - cached_at < _CHESSCOM_PROFILE_TTL_SECONDS:
            return payload
        # stale: drop and refetch below.
        _chesscom_profile_cache.pop(cache_key, None)
    try:
        from integrations.chess_com import fetch_chesscom_profile

        payload = fetch_chesscom_profile(username)
    except Exception as exc:  # noqa: BLE001 -- intentionally broad
        log.warning("chess.com profile fetch failed for %s: %s", username, exc)
        return None
    _chesscom_profile_cache[cache_key] = (now, payload)
    return payload


@router.get(
    "/train/opponent-profile-info",
    response_model=OpponentProfileInfoResponse,
)
def get_opponent_profile_info(
    request: Request,
    provider: str = Query(..., min_length=1),
    opponent_username: str = Query(..., min_length=1, max_length=100),
    _: None = Depends(limit_by_clerk_user_id(limit=30, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    if provider not in ("lichess", "chesscom"):
        raise HTTPException(status_code=400, detail="provider must be 'lichess' or 'chesscom'")

    if provider == "lichess":
        # Lichess does NOT expose an avatar URL or verified flag on the
        # public profile endpoint — return the explicit null/false so
        # the frontend renders the initials fallback and omits the
        # check badge without hitting the network.
        return OpponentProfileInfoResponse(
            provider="lichess",
            opponent_username=opponent_username,
            avatar_url=None,
            verified=False,
        )

    payload = _chesscom_profile_cached(opponent_username) or {
        "avatar": None,
        "verified": False,
    }
    return OpponentProfileInfoResponse(
        provider="chesscom",
        opponent_username=opponent_username,
        avatar_url=payload.get("avatar"),
        verified=bool(payload.get("verified")),
    )


@router.get(
    "/train/opponent-analysis",
    response_model=OpponentAnalysisStatusResponse,
)
def get_opponent_analysis(
    request: Request,
    provider: str = Query(..., min_length=1),
    opponent_username: str = Query(..., min_length=1, max_length=100),
    _: None = Depends(limit_by_clerk_user_id(limit=30, window=60)),
):
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")

    if provider not in ("lichess", "chesscom"):
        raise HTTPException(status_code=400, detail="provider must be 'lichess' or 'chesscom'")

    status = get_opponent_analysis_status(
        requested_by_user_id=clerk_id,
        provider=provider,
        opponent_username=opponent_username,
    )
    if not status:
        raise HTTPException(
            status_code=404,
            detail="No analysis job found for this opponent",
        )

    return OpponentAnalysisStatusResponse(**status)


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
            # Three failure axes get separate try/excepts (one failure
            # axis per try/except, so a trap-computation failure can't
            # mask a style-computation failure or vice versa):
            #   * compute_opponent_style() reads + python-chess-parses the
            #     opponent's full PGN corpus from the DB -- it can fail in
            #     ways unrelated to Maia (corrupt PGN rows, transient DB
            #     errors). A failure here degrades to unbiased Maia.
            #   * compute_exploitable_traps() queries the
            #     opponent_game_blunders + opponent_games tables to find
            #     positions the opponent has blundered in >=2 distinct
            #     games across >=5 total games (trap-mode, decision (6) in
            #     opponent_style_reranker's module docstring). It can fail
            #     independently of the style layer (transient DB errors,
            #     pool checkout failure). A failure here degrades to
            #     mirror-mode (exploitable_trap_keys=None) -- the existing
            #     sac/qt/setup/castle biasing still runs; only the trap
            #     boost is lost. Do NOT let a trap-computation failure
            #     prevent the existing style biasing from running.
            #   * rerank_candidates() is pure-Python (board copies +
            #     per-candidate sac detection); if it raises it degrades to
            #     the unbiased best_move() call.
            # best_move_candidates() is itself a Maia call -- its
            # MaiaUnavailableError propagates up to the outer handler (503),
            # correctly distinguishing "engine down" from "style layer
            # hiccupped".
            #
            # LATENCY SURFACE: compute_opponent_style replays every imported
            # PGN on each call; for a 500-game opponent that is ~2.4s per
            # out-of-book move. compute_exploitable_traps adds ~6ms. Both
            # are now cached together in _sparring_style_traps_cache (see
            # the module-level helper) so the per-move cost collapses to a
            # dict lookup after the first call of a session. A miss runs the
            # exact three-try/except path below; only successful results are
            # stored.
            style = None
            style_ok = False
            exploitable_trap_keys = None
            traps_ok = False
            cache_hit = False
            cache_key = _sparring_style_traps_cache_key(
                clerk_id, body.provider, body.opponent_username,
                body.time_control,
            )
            cached_entry = _sparring_style_traps_cache.get(cache_key)
            if cached_entry is not None and (
                time.time() - cached_entry[0] < _SPARRING_STYLE_TRAPS_TTL_SECONDS
            ):
                cache_hit = True
                style = cached_entry[1]
                exploitable_trap_keys = cached_entry[2]
                traps_ok = cached_entry[3]
                style_ok = style is not None
                log.info(
                    "style/traps cache HIT: opponent=%s/%s "
                    "style_cached=%s traps_cached=%s",
                    body.provider, body.opponent_username, style_ok, traps_ok,
                )
            else:
                if cached_entry is not None:
                    # expired -> drop and recompute below
                    _sparring_style_traps_cache.pop(cache_key, None)
                log.info(
                    "style/traps cache MISS: opponent=%s/%s — computing fresh",
                    body.provider, body.opponent_username,
                )

            if not style_ok:
                try:
                    style = compute_opponent_style(
                        requested_by_user_id=clerk_id,
                        provider=body.provider,
                        opponent_username=body.opponent_username,
                        sparring_time_control=body.time_control,
                    )
                    style_ok = True
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "compute_opponent_style failed for %s/%s — falling "
                        "back to unbiased Maia. Underlying: %s",
                        body.provider, body.opponent_username, exc,
                    )
                    style = None
                    style_ok = False

            move_uci = ""
            move_san = ""

            if style is not None and style.get("sufficient"):
                candidates = maia_engine.best_move_candidates(
                    board,
                    multipv=5,
                    self_elo=opponent_elo,
                    oppo_elo=opponent_elo,
                )
                # --- trap-mode data (decision (6)) -----------------------
                # compute_exploitable_traps returns the bare set of
                # exploitable position_keys (applying TRAP_MIN_HITS /
                # TRAP_MIN_GAMES gates). None/empty = mirror-mode (today's
                # reranker, unchanged). Reused from the cache on a fresh
                # traps_ok hit; otherwise computed fresh (the conn is
                # checked out from the pool and returned in a finally --
                # matching list_opponent_profiles' connection-acquisition
                # pattern, the same pattern the sibling
                # compute_opponent_traps call uses for the Opponent Prep
                # page's "Traps He's Fallen For" UI). On any failure the
                # trap set is None so the reranker stays in mirror-mode;
                # the existing sac/qt/setup/castle biasing is NOT blocked.
                if not traps_ok:
                    exploitable_trap_keys = None
                    try:
                        if database.connection_pool is None:
                            raise RuntimeError(
                                "Database connection pool is not initialized"
                            )
                        conn = database.connection_pool.getconn()
                        try:
                            exploitable_trap_keys = compute_exploitable_traps(
                                conn,
                                requested_by_user_id=clerk_id,
                                provider=body.provider,
                                opponent_username=body.opponent_username,
                                sparring_time_control=body.time_control,
                            )
                        finally:
                            database.connection_pool.putconn(conn)
                        traps_ok = True
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "compute_exploitable_traps failed for %s/%s — "
                            "falling back to mirror-mode (no trap bias). "
                            "Underlying: %s",
                            body.provider, body.opponent_username, exc,
                        )
                        exploitable_trap_keys = None
                        traps_ok = False

                # --- near-book repertoire data (feature D) -----------------
                # SEQUENCING RULE: this is reached ONLY when
                # pick_repertoire_move returned no exact book move (the
                # in-book branch above returns the book move and never
                # enters the Maia/style block). So near-book similarity is
                # structurally skipped whenever an exact book hit exists --
                # it is not evaluated, scored, or allowed to compete.
                #
                # pick_near_repertoire_moves returns a recency-weighted
                # {move_uci: weight} map of the opponent's moves from
                # positions NEAR the live one (same color, +/- ply window),
                # reading the SAME weighted repertoire table the exact-book
                # path reads. None/empty = no near-book signal -> the
                # reranker's near_book_mult is 1.0 for every candidate
                # (mirror-mode unchanged). Computed per out-of-book move (it
                # depends on the live ply) -- a single cheap indexed query,
                # so it is NOT added to the session cache. On any failure it
                # degrades to None and mirror-mode is preserved.
                near_book_weights = None
                try:
                    near_book_weights = pick_near_repertoire_moves(
                        requested_by_user_id=clerk_id,
                        provider=body.provider,
                        opponent_username=body.opponent_username,
                        board=board,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "pick_near_repertoire_moves failed for %s/%s — "
                        "falling back to mirror-mode (no near-book bias). "
                        "Underlying: %s",
                        body.provider, body.opponent_username, exc,
                    )
                    near_book_weights = None

                try:
                    rerank = rerank_candidates(
                        candidates=candidates,
                        style=style,
                        board=board,
                        exploitable_trap_keys=exploitable_trap_keys,
                        near_book_weights=near_book_weights,
                    )
                    move_uci = rerank.get("chosen_move_uci", "") or ""
                    if move_uci:
                        move_san = board.san(chess.Move.from_uci(move_uci))
                        log.info(
                            "style-bias re-ranker invoked: opponent=%s/%s "
                            "sacrifice_frequency=%s applied_bias=%s "
                            "source=%s base=%s "
                            "setup_present=%s setup_family=%s "
                            "setup_family_confidence=%s "
                            "setup_filtered_count=%s "
                            "trap_mode_active=%s trap_candidate_count=%s "
                            "near_book_active=%s near_book_candidate_count=%s "
                            "signals=%s chose=%s",
                            body.provider, body.opponent_username,
                            rerank.get("sacrifice_frequency"),
                            rerank.get("applied_bias"),
                            rerank.get("source"),
                            rerank.get("base_source"),
                            rerank.get("setup_present"),
                            rerank.get("setup_family"),
                            rerank.get("setup_family_confidence"),
                            rerank.get("setup_filtered_count"),
                            rerank.get("trap_mode_active"),
                            rerank.get("trap_candidate_count"),
                            rerank.get("near_book_active"),
                            rerank.get("near_book_candidate_count"),
                            (
                                rerank.get("bias_breakdown", {}) or {}
                            ).get("signals_applied") if rerank.get("applied_bias") else None,
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

            # Cache the freshly computed results (miss path only, so the TTL
            # is measured from the actual compute time). Only successful
            # results are stored: style is cached only when
            # compute_opponent_style did not raise (style_ok), and traps only
            # when it was computed successfully (traps_ok) -- a failed traps
            # computation leaves traps_ok=False so the next move re-runs
            # traps instead of trusting a poisoned None. A failed style
            # computation stores nothing at all, so the next move retries it.
            if not cache_hit and style_ok:
                _sparring_style_traps_cache[cache_key] = (
                    time.time(),
                    style,
                    exploitable_trap_keys if traps_ok else None,
                    traps_ok,
                )
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
