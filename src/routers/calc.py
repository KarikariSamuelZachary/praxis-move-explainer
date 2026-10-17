"""Blind Calculation trainer: selection + grading + per-depth rating.

Serving rules: active, non-trivial segments picked within the user's rating
window at depth n (shared window math, widened stepwise), excluding every
segment attempted in the last SEGMENT_EXCLUSION_DAYS days (solved or
failed — failure reveals the line; failed segments return via the
Woodpecker queue, not /next).

Exercises are bearer tokens: /next issues an HMAC-bound
{user, segment, offset, n, nonce} token and /submit redeems it. One attempt
per token (409 on replay, no reveal). Elapsed time is server-side.

Rating is per (user, depth n): fractional-score Elo against the segment's
puzzle rating, applied only on the user's FIRST attempt on a segment (any
offset/n, ever) with the ratings row locked in the same transaction.
"""

from __future__ import annotations

import logging
import time

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor

from core.database import get_db
from core.rate_limit import limit_by_clerk_user_id
from core.rating import (
    CALC_K_FACTOR,
    CALC_RATING_MAX,
    CALC_RATING_MIN,
    CALC_WINDOW_SPREADS,
    calc_clamp_rating,
    calc_expected_score,
    calc_seed_rating,
    calculate_fractional_rating_change,
    rating_window,
)
from schemas.calc_schemas import (
    CalcHintRequest,
    CalcHintResponse,
    CalcNextResponse,
    CalcRatingsResponse,
    CalcRetryRequest,
    CalcRetryResponse,
    CalcSubmitRequest,
    CalcSubmitResponse,
)
from services.calc_grading import (
    choose_offset,
    exercise_fen,
    exercise_line_tokens,
    grade,
    solver_score,
)
from services.calc_tokens import (
    BadTokenError,
    ExpiredTokenError,
    ForeignTokenError,
    issue_token,
    verify_review_token,
    verify_token,
)

router = APIRouter()
log = logging.getLogger(__name__)

# Attempts on a segment (solved or failed) hide it from /next for this long.
# Failed segments come back through the Woodpecker queue, not through /next.
SEGMENT_EXCLUSION_DAYS = 30


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


def _get_or_seed_rating(cur, clerk_id: str, n: int) -> dict:
    """Fetch the (user, n) ratings row, creating it on first use. The caller
    must hold no lock requirements; creation races are absorbed by the PK."""
    cur.execute("SELECT rating, attempts FROM calc_ratings"
                " WHERE user_id = %s AND n = %s", (clerk_id, n))
    row = cur.fetchone()
    if row is not None:
        return {"rating": row["rating"], "attempts": row["attempts"]}
    cur.execute("SELECT tactical_rating FROM users WHERE clerk_id = %s",
                (clerk_id,))
    user = cur.fetchone()
    seed = calc_seed_rating(user["tactical_rating"] if user else None)
    cur.execute("INSERT INTO calc_ratings (user_id, n, rating)"
                " VALUES (%s, %s, %s) ON CONFLICT (user_id, n) DO NOTHING",
                (clerk_id, n, seed))
    cur.execute("SELECT rating, attempts FROM calc_ratings"
                " WHERE user_id = %s AND n = %s", (clerk_id, n))
    row = cur.fetchone()
    return {"rating": row["rating"], "attempts": row["attempts"]}


@router.get("/next", response_model=CalcNextResponse)
def get_next_exercise(
    request: Request,
    n: int = Query(3, ge=3, le=12),
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """One unattempted exercise in the user's window + its bearer token."""
    clerk_id = _require_clerk_id(request)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        rated = _get_or_seed_rating(cur, clerk_id, n)
        conn.commit()
        for spread in CALC_WINDOW_SPREADS:
            lo, hi = rating_window(rated["rating"], floor=CALC_RATING_MIN,
                                   ceiling=CALC_RATING_MAX, spread=spread)
            cur.execute(
                "SELECT id, segment_fen, line_uci, solver_move_count, rating"
                " FROM calc_segments s"
                " WHERE status = 'active' AND trivially_forced = false"
                " AND solver_move_count >= %s"
                " AND rating BETWEEN %s AND %s"
                " AND NOT EXISTS (SELECT 1 FROM calc_attempts a"
                " WHERE a.user_id = %s AND a.segment_id = s.id"
                " AND a.created_at > NOW() - make_interval(days => %s))"
                " ORDER BY random() LIMIT 1",
                (n, lo, hi, clerk_id, SEGMENT_EXCLUSION_DAYS),
            )
            seg = cur.fetchone()
            if seg is not None:
                break
        else:
            seg = None
        if seg is None:
            raise HTTPException(status_code=404,
                                detail="No unsolved exercise found")
        offset = choose_offset(seg["solver_move_count"], n)
        issued = issue_token(clerk_id, str(seg["id"]), offset, n)
        fen = exercise_fen(seg["segment_fen"], seg["line_uci"], offset)
        return CalcNextResponse(
            segment_id=seg["id"],
            offset=offset,
            n=n,
            fen=fen,
            side_to_move="white" if chess.Board(fen).turn else "black",
            rating=seg["rating"],
            exercise_token=issued["token"],
        )
    finally:
        cur.close()


@router.get("/ratings", response_model=CalcRatingsResponse)
def get_ratings(
    request: Request,
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """The user's per-depth ratings. Rows exist only for used depths."""
    clerk_id = _require_clerk_id(request)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT n, rating, attempts FROM calc_ratings"
                    " WHERE user_id = %s ORDER BY n", (clerk_id,))
        return CalcRatingsResponse(ratings=[
            {"n": r["n"], "rating": r["rating"], "attempts": r["attempts"]}
            for r in cur.fetchall()
        ])
    finally:
        cur.close()


@router.post("/submit", response_model=CalcSubmitResponse)
def submit_exercise(
    request: Request,
    body: CalcSubmitRequest,
    _: None = Depends(limit_by_clerk_user_id(limit=60, window=60)),
    conn=Depends(get_db),
):
    """Grade the moves bound to an exercise token; rate first attempts."""
    clerk_id = _require_clerk_id(request)
    try:
        token = verify_token(body.exercise_token, clerk_id)
    except ForeignTokenError:
        raise HTTPException(status_code=403, detail="Invalid exercise token")
    except (BadTokenError, ExpiredTokenError):
        raise HTTPException(status_code=400, detail="Invalid exercise token")
    n, offset = token["n"], token["offset"]
    if not 3 <= n <= 12:
        raise HTTPException(status_code=400, detail="Invalid exercise token")
    hints_used = body.hints_used or 0
    if hints_used < 0:
        raise HTTPException(status_code=400,
                            detail="hints_used cannot be negative")
    if len(body.moves) != 2 * n - 1:
        raise HTTPException(
            status_code=422,
            detail=f"moves must have exactly {2 * n - 1} plies "
                   f"for n={n}",
        )
    for uci in body.moves:
        try:
            chess.Move.from_uci(uci.strip().lower())
        except ValueError:
            raise HTTPException(status_code=422,
                                detail=f"bad UCI format: {uci!r}")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT 1 FROM calc_attempts WHERE exercise_nonce = %s",
            (token["nonce"],),
        )
        if cur.fetchone() is not None:
            # 409 with no grading fields and no reveal.
            raise HTTPException(status_code=409,
                                detail="Exercise already submitted")
        cur.execute(
            "SELECT id, segment_fen, line_uci, solver_move_count, status,"
            " rating FROM calc_segments WHERE id = %s",
            (token["segment_id"],),
        )
        seg = cur.fetchone()
        if seg is None or seg["status"] != "active":
            raise HTTPException(status_code=404,
                                detail="Unknown or retired segment")
        if offset + n > seg["solver_move_count"]:
            raise HTTPException(status_code=422,
                                detail="Exercise out of segment range")
        expected = exercise_line_tokens(seg["line_uci"], offset, n)
        result = grade(expected, body.moves)
        score = solver_score(n, result["first_divergence_ply"],
                             result["diverged_on"], result["solved"])
        # First-ever attempt on this segment (any offset/n) is rated;
        # later attempts are stored unrated. A hinted attempt never rates
        # (endgame neutralization): it still grades, stores, and queues.
        # The ratings row is locked in the same transaction as the attempt
        # insert.
        cur.execute("SELECT 1 FROM calc_attempts"
                    " WHERE user_id = %s AND segment_id = %s",
                    (clerk_id, token["segment_id"]))
        first = cur.fetchone() is None
        rated = first and hints_used == 0
        rating_before = rating_after = expected_score = None
        k_factor = CALC_K_FACTOR
        delta = 0
        if rated:
            _get_or_seed_rating(cur, clerk_id, n)
            cur.execute("SELECT rating, attempts FROM calc_ratings"
                        " WHERE user_id = %s AND n = %s FOR UPDATE",
                        (clerk_id, n))
            locked = cur.fetchone()
            rating_before = locked["rating"]
            expected_score = calc_expected_score(rating_before,
                                                 seg["rating"])
            delta = calculate_fractional_rating_change(
                rating_before, seg["rating"], score, k=k_factor)
            rating_after = calc_clamp_rating(rating_before + delta)
            cur.execute("UPDATE calc_ratings SET rating = %s, attempts ="
                        " attempts + 1, updated_at = NOW()"
                        " WHERE user_id = %s AND n = %s",
                        (rating_after, clerk_id, n))
        try:
            cur.execute(
                "INSERT INTO calc_attempts (user_id, segment_id,"
                " offset_solver_moves, n, submitted_line,"
                " first_divergence_ply, diverged_on, solved, time_taken_ms,"
                " exercise_nonce, rating_before, rating_after,"
                " expected_score, k_factor, score, rated)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " RETURNING id",
                (clerk_id, token["segment_id"], offset, n,
                 " ".join(body.moves), result["first_divergence_ply"],
                 result["diverged_on"], result["solved"],
                 int((time.time() - token["issued_at"]) * 1000),
                 token["nonce"], rating_before, rating_after,
                 expected_score, k_factor, score, rated),
            )
            attempt_id = cur.fetchone()["id"]
            if not result["solved"]:
                # Server-side capture only (no client endpoint creates
                # entries): a failed exercise becomes a review card whether
                # or not the attempt was rated. No caps (mirrors endgames).
                cur.execute(
                    "INSERT INTO calc_woodpecker_entries (user_id,"
                    " segment_id, offset_solver_moves, n, source_attempt_id)"
                    " VALUES (%s,%s,%s,%s,%s)"
                    " ON CONFLICT (user_id, segment_id, offset_solver_moves,"
                    " n) WHERE is_mastered = FALSE DO NOTHING",
                    (clerk_id, token["segment_id"], offset, n,
                     str(attempt_id)),
                )
            conn.commit()
        except pg_errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409,
                                detail="Exercise already submitted")
        return CalcSubmitResponse(
            solved=result["solved"],
            first_divergence_ply=result["first_divergence_ply"],
            diverged_on=result["diverged_on"],
            correct_prefix_plies=result["correct_prefix_plies"],
            continuation=result["continuation"],
            rating_before=rating_before,
            rating_after=rating_after,
            delta=delta,
            rated=rated,
            attempt_id=attempt_id,
        )
    finally:
        cur.close()


@router.post("/retry", response_model=CalcRetryResponse)
def retry_exercise(
    request: Request,
    body: CalcRetryRequest,
    _: None = Depends(limit_by_clerk_user_id(limit=60, window=60)),
    conn=Depends(get_db),
):
    """Re-issue a fresh token for the same exercise (segment/offset/n).

    Powers practice retries: the old token may already be spent (nonce
    consumption is checked at /submit, not here). The retry submit grades
    normally but stores unrated — a prior attempt on the segment always
    exists by then, so the first-ever rule neutralizes it.
    """
    clerk_id = _require_clerk_id(request)
    try:
        token = verify_token(body.exercise_token, clerk_id)
    except ForeignTokenError:
        raise HTTPException(status_code=403, detail="Invalid exercise token")
    except (BadTokenError, ExpiredTokenError):
        raise HTTPException(status_code=400, detail="Invalid exercise token")
    n, offset = token["n"], token["offset"]
    if not 3 <= n <= 12:
        raise HTTPException(status_code=400, detail="Invalid exercise token")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT solver_move_count, status FROM calc_segments WHERE id = %s",
            (token["segment_id"],),
        )
        seg = cur.fetchone()
        if seg is None or seg["status"] != "active":
            raise HTTPException(status_code=404,
                                detail="Unknown or retired segment")
        if offset + n > seg["solver_move_count"]:
            raise HTTPException(status_code=422,
                                detail="Exercise out of segment range")
        issued = issue_token(clerk_id, token["segment_id"], offset, n)
        return CalcRetryResponse(exercise_token=issued["token"])
    finally:
        cur.close()


@router.post("/hint", response_model=CalcHintResponse)
def hint_next_ply(
    request: Request,
    body: CalcHintRequest,
    _: None = Depends(limit_by_clerk_user_id(limit=60, window=60)),
    conn=Depends(get_db),
):
    """Reveal the single next expected ply for an exercise token.

    The client is blind by design (it never holds the line), so even the
    highlight needs the server. Returns expected[plies_entered]; writes
    nothing — the client reports hints_used at submit, and any hinted
    attempt stores unrated (endgame neutralization). Calling hint never
    consumes the token: grading still happens exactly once at /submit.

    Review flow: pass review_token (a woodpecker review token) instead of
    exercise_token; it resolves to the entry's segment/offset/n the same
    way and is equally read-only.
    """
    clerk_id = _require_clerk_id(request)
    use_review = body.review_token is not None
    if use_review == (body.exercise_token is not None):
        raise HTTPException(status_code=400,
                            detail="exactly one of exercise_token,"
                                   " review_token is required")
    n = offset = segment_id = None
    if use_review:
        try:
            rtoken = verify_review_token(body.review_token, clerk_id)
        except ForeignTokenError:
            raise HTTPException(status_code=403,
                                detail="Invalid review token")
        except (BadTokenError, ExpiredTokenError):
            raise HTTPException(status_code=400,
                                detail="Invalid review token")
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute(
                "SELECT e.n, e.offset_solver_moves, s.id,"
                " s.segment_fen, s.line_uci, s.solver_move_count,"
                " s.status FROM calc_woodpecker_entries e"
                " JOIN calc_segments s ON s.id = e.segment_id"
                " WHERE e.id = %s::uuid AND e.user_id = %s",
                (rtoken["entry_id"], clerk_id),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404,
                                    detail="Review entry not found")
            n, offset = row["n"], row["offset_solver_moves"]
            seg = row
        finally:
            cur.close()
    else:
        try:
            token = verify_token(body.exercise_token, clerk_id)
        except ForeignTokenError:
            raise HTTPException(status_code=403, detail="Invalid exercise token")
        except (BadTokenError, ExpiredTokenError):
            raise HTTPException(status_code=400, detail="Invalid exercise token")
        n, offset = token["n"], token["offset"]
        segment_id = token["segment_id"]
        seg = None
    if not 3 <= n <= 12:
        raise HTTPException(status_code=400, detail="Invalid exercise token")
    if not 0 <= body.plies_entered < 2 * n - 1:
        raise HTTPException(status_code=422,
                            detail=f"plies_entered must be within"
                                   f" [0, {2 * n - 2}] for n={n}")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if seg is None:
            cur.execute(
                "SELECT segment_fen, line_uci, solver_move_count, status"
                " FROM calc_segments WHERE id = %s",
                (segment_id,),
            )
            seg = cur.fetchone()
        if seg is None or seg["status"] != "active":
            raise HTTPException(status_code=404,
                                detail="Unknown or retired segment")
        if offset + n > seg["solver_move_count"]:
            raise HTTPException(status_code=422,
                                detail="Exercise out of segment range")
        expected = exercise_line_tokens(seg["line_uci"], offset, n)
        return CalcHintResponse(uci=expected[body.plies_entered],
                                ply=body.plies_entered)
    finally:
        cur.close()
