"""Blind Calculation Woodpecker queue: review due cards, count, attempt.

Cards are created ONLY by the server on a FAILED POST /submit (routers/
calc.py) — there is no client-callable endpoint that creates entries.
Reviews replay the captured exercise (same segment/offset/n) under a
single-use review token; FSRS scheduling uses the pure functions in
core/fsrs.py (solved -> Good, failed -> Again). Reviews never touch
calc_ratings or calc_attempts.

The due predicate (not mastered AND due <= now, oldest due first) is the
same gate as the endgame queue and its badge.
"""

from __future__ import annotations

import logging
import time

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor

from core.database import get_db
from core.fsrs import (
    card_from_row,
    is_lapse,
    is_mastered,
    now_utc,
    rating_for,
    scheduler,
)
from core.rate_limit import limit_by_clerk_user_id
from schemas.calc_schemas import (
    CalcSubmitResponse,
    CalcWoodpeckerAttemptRequest,
    CalcWoodpeckerCountResponse,
    CalcWoodpeckerQueueItem,
)
from services.calc_grading import (
    exercise_fen,
    exercise_line_tokens,
    grade,
    solver_score,
)
from services.calc_tokens import (
    BadTokenError,
    ExpiredTokenError,
    ForeignTokenError,
    issue_review_token,
    verify_review_token,
)

router = APIRouter()
log = logging.getLogger(__name__)

# The due gate, same predicate as the endgame queue/badge: unmastered
# cards whose FSRS due timestamp has passed.
_DUE_PREDICATE = "e.user_id = %s AND e.is_mastered = FALSE AND e.due <= NOW()"


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


def _fen_for(entry) -> str:
    return exercise_fen(entry["segment_fen"], entry["line_uci"],
                        entry["offset_solver_moves"])


@router.get("/queue", response_model=list[CalcWoodpeckerQueueItem])
def get_queue(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """Due review cards, oldest due first. Never includes the line."""
    clerk_id = _require_clerk_id(request)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT e.id, e.offset_solver_moves, e.n, s.segment_fen,"
            " s.line_uci FROM calc_woodpecker_entries e"
            " JOIN calc_segments s ON s.id = e.segment_id"
            " WHERE " + _DUE_PREDICATE +
            " ORDER BY e.due ASC LIMIT %s",
            (clerk_id, limit),
        )
        items = []
        for row in cur.fetchall():
            fen = _fen_for(row)
            issued = issue_review_token(clerk_id, str(row["id"]))
            items.append(CalcWoodpeckerQueueItem(
                entry_id=row["id"],
                fen=fen,
                side_to_move="white" if chess.Board(fen).turn else "black",
                n=row["n"],
                review_token=issued["token"],
            ))
        return items
    finally:
        cur.close()


@router.get("/count", response_model=CalcWoodpeckerCountResponse)
def get_due_count(
    request: Request,
    _: None = Depends(limit_by_clerk_user_id(limit=120, window=60)),
    conn=Depends(get_db),
):
    """Due-now card count for the badge: same predicate as /queue."""
    clerk_id = _require_clerk_id(request)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT COUNT(*) AS due_count FROM calc_woodpecker_entries e"
            " WHERE " + _DUE_PREDICATE,
            (clerk_id,),
        )
        return CalcWoodpeckerCountResponse(
            due_count=cur.fetchone()["due_count"])
    finally:
        cur.close()


@router.post("/attempt", response_model=CalcSubmitResponse)
def review_attempt(
    request: Request,
    body: CalcWoodpeckerAttemptRequest,
    _: None = Depends(limit_by_clerk_user_id(limit=60, window=60)),
    conn=Depends(get_db),
):
    """Grade one review replay; schedule FSRS. Never touches ratings."""
    clerk_id = _require_clerk_id(request)
    try:
        token = verify_review_token(body.review_token, clerk_id)
    except ForeignTokenError:
        raise HTTPException(status_code=403, detail="Invalid review token")
    except (BadTokenError, ExpiredTokenError):
        raise HTTPException(status_code=400, detail="Invalid review token")
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # FOR UPDATE serializes concurrent replays of this card, so two
        # in-flight attempts cannot both schedule FSRS from one state
        # (same pattern as the endgame review endpoint).
        cur.execute(
            "SELECT e.*, s.segment_fen, s.line_uci, s.solver_move_count,"
            " s.status AS segment_status FROM calc_woodpecker_entries e"
            " JOIN calc_segments s ON s.id = e.segment_id"
            " WHERE e.id = %s::uuid AND e.user_id = %s"
            " FOR UPDATE OF e",
            (token["entry_id"], clerk_id),
        )
        entry = cur.fetchone()
        if entry is None:
            conn.rollback()
            raise HTTPException(status_code=404,
                                detail="Review entry not found")
        n, offset = entry["n"], entry["offset_solver_moves"]
        if len(body.moves) != 2 * n - 1:
            conn.rollback()
            raise HTTPException(
                status_code=422,
                detail=f"moves must have exactly {2 * n - 1} plies "
                       f"for n={n}",
            )
        for uci in body.moves:
            try:
                chess.Move.from_uci(uci.strip().lower())
            except ValueError:
                conn.rollback()
                raise HTTPException(status_code=422,
                                    detail=f"bad UCI format: {uci!r}")
        cur.execute(
            "SELECT 1 FROM calc_woodpecker_attempts WHERE review_nonce = %s",
            (token["nonce"],),
        )
        if cur.fetchone() is not None:
            conn.rollback()
            raise HTTPException(status_code=409,
                                detail="Review already submitted")
        expected = exercise_line_tokens(entry["line_uci"], offset, n)
        result = grade(expected, body.moves)
        score = solver_score(n, result["first_divergence_ply"],
                             result["diverged_on"], result["solved"])
        review_at = now_utc()
        card = card_from_row(entry)
        prior_state = card.state
        rating = rating_for(result["solved"])
        reviewed_card, _ = scheduler.review_card(
            card=card, rating=rating, review_datetime=review_at)
        lapse = is_lapse(prior_state, rating)
        mastered = is_mastered(reviewed_card)
        try:
            cur.execute(
                """UPDATE calc_woodpecker_entries
                   SET due = %s, stability = %s, difficulty = %s, state = %s,
                       step = %s, last_review = %s,
                       reps = reps + 1""" +
                (" , lapses = lapses + 1" if lapse else "") + """,
                       is_mastered = %s, mastered_at = %s
                   WHERE id = %s AND user_id = %s""",
                (reviewed_card.due, reviewed_card.stability,
                 reviewed_card.difficulty, int(reviewed_card.state),
                 reviewed_card.step, review_at, mastered,
                 review_at if mastered else None, token["entry_id"],
                 clerk_id),
            )
            elapsed_ms = int((time.time() - token["issued_at"]) * 1000)
            cur.execute(
                "INSERT INTO calc_woodpecker_attempts (entry_id, user_id,"
                " solved, first_divergence_ply, diverged_on, score,"
                " elapsed_ms, review_nonce)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (token["entry_id"], clerk_id, result["solved"],
                 result["first_divergence_ply"], result["diverged_on"],
                 score, elapsed_ms, token["nonce"]),
            )
            conn.commit()
        except pg_errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409,
                                detail="Review already submitted")
        return CalcSubmitResponse(
            solved=result["solved"],
            first_divergence_ply=result["first_divergence_ply"],
            diverged_on=result["diverged_on"],
            correct_prefix_plies=result["correct_prefix_plies"],
            continuation=result["continuation"],
            rating_before=None,
            rating_after=None,
            delta=0,
            rated=False,
        )
    finally:
        cur.close()
