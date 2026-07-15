from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core.database import get_db
from core.fsrs import (
    card_from_row,
    is_lapse,
    is_mastered,
    now_utc,
    rating_for,
    scheduler,
)

router = APIRouter()

VALID_SOURCE_REASONS = {
    "wrong_answer",
    "slow_solution",
    "hint_used",
    "coach_recommended",
}


class AddEntryBody(BaseModel):
    puzzle_id: str
    theme: str
    source_reason: Optional[str] = None


class RecordAttemptBody(BaseModel):
    entry_id: UUID
    solved_correctly: bool
    time_taken_ms: int


def _get_user_id(request: Request) -> str:
    user_id = request.headers.get("X-Clerk-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return user_id


@router.post("/entries")
def add_entry(request: Request, body: AddEntryBody, conn=Depends(get_db)):
    user_id = _get_user_id(request)
    puzzle_id = body.puzzle_id.strip()
    theme = body.theme.strip()

    if not puzzle_id:
        raise HTTPException(status_code=400, detail="puzzle_id cannot be empty")
    if not theme:
        raise HTTPException(status_code=400, detail="theme cannot be empty")
    if body.source_reason is not None and body.source_reason not in VALID_SOURCE_REASONS:
        raise HTTPException(status_code=400, detail="Invalid source_reason")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Duplicate guard: if a non-mastered entry for the same puzzle already
        # exists for this user, return it instead of creating a second one.
        cur.execute(
            """
            SELECT
                id,
                user_id,
                puzzle_id,
                theme,
                added_at,
                mastered_at,
                is_mastered,
                source_reason,
                due,
                state,
                step,
                stability,
                difficulty,
                reps,
                lapses,
                last_review
            FROM woodpecker_entries
            WHERE user_id = %s
              AND puzzle_id = %s
              AND is_mastered = FALSE
            """,
            (user_id, puzzle_id),
        )
        existing = cur.fetchone()
        if existing is not None:
            return {"skipped": True, "reason": "duplicate_puzzle", "entry": existing}

        # Daily entry cap for free users
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM woodpecker_entries
            WHERE user_id = %s
            AND DATE(added_at) = CURRENT_DATE
            """,
            (user_id,),
        )
        daily_count = cur.fetchone()["total"]
        if daily_count >= 10:
            return {"skipped": True, "reason": "daily_cap_reached"}

        # Active queue cap for free users
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM woodpecker_entries
            WHERE user_id = %s
            AND is_mastered = FALSE
            """,
            (user_id,),
        )
        active_count = cur.fetchone()["total"]
        if active_count >= 20:
            return {"skipped": True, "reason": "active_cap_reached"}

        cur.execute(
            """
            INSERT INTO woodpecker_entries (
                user_id,
                puzzle_id,
                theme,
                source_reason
            )
            VALUES (%s, %s, %s, %s)
            RETURNING
                id,
                user_id,
                puzzle_id,
                theme,
                added_at,
                mastered_at,
                is_mastered,
                source_reason,
                due,
                state,
                step,
                stability,
                difficulty,
                reps,
                lapses,
                last_review
            """,
            (user_id, puzzle_id, theme, body.source_reason),
        )
        row = cur.fetchone()
    conn.commit()

    return row


@router.post("/attempts")
def record_attempt(request: Request, body: RecordAttemptBody, conn=Depends(get_db)):
    user_id = _get_user_id(request)
    if body.time_taken_ms < 0:
        raise HTTPException(status_code=400, detail="time_taken_ms cannot be negative")

    review_at = now_utc()

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                id,
                user_id,
                state,
                step,
                stability,
                difficulty,
                due,
                last_review,
                reps,
                lapses
            FROM woodpecker_entries
            WHERE id = %s AND user_id = %s
            FOR UPDATE
            """,
            (str(body.entry_id), user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Woodpecker entry not found")

        # --- FSRS scheduling ---
        card = card_from_row(row)
        prior_state = card.state
        rating = rating_for(body.solved_correctly)
        reviewed_card, _ = scheduler.review_card(
            card=card, rating=rating, review_datetime=review_at
        )

        lapse = is_lapse(prior_state, rating)
        mastered = is_mastered(reviewed_card)

        # Persist the updated FSRS state and bookkeeping counters back onto the
        # entry row.
        increment_lapses = ", lapses = lapses + 1" if lapse else ""
        cur.execute(
            f"""
            UPDATE woodpecker_entries
            SET due          = %s,
                stability    = %s,
                difficulty   = %s,
                state        = %s,
                step         = %s,
                last_review  = %s,
                reps         = reps + 1{increment_lapses},
                is_mastered  = %s,
                mastered_at  = %s
            WHERE id = %s AND user_id = %s
            """,
            (
                reviewed_card.due,
                reviewed_card.stability,
                reviewed_card.difficulty,
                int(reviewed_card.state),
                reviewed_card.step,
                review_at,
                mastered,
                review_at if mastered else None,
                str(body.entry_id),
                user_id,
            ),
        )

        # Record the attempt log row (structure unchanged apart from the
        # removed cycle_number column).
        cur.execute(
            """
            INSERT INTO woodpecker_attempts (
                entry_id,
                user_id,
                solved_correctly,
                time_taken_ms
            )
            VALUES (%s, %s, %s, %s)
            RETURNING
                id,
                entry_id,
                user_id,
                solved_correctly,
                time_taken_ms,
                attempted_at
            """,
            (
                str(body.entry_id),
                user_id,
                body.solved_correctly,
                body.time_taken_ms,
            ),
        )
        attempt = cur.fetchone()

    conn.commit()

    return {
        "attempt": attempt,
        "scheduling": {
            "prior_state": int(prior_state),
            "rating": int(rating),
            "new_state": int(reviewed_card.state),
            "due": reviewed_card.due.isoformat(),
            "stability": reviewed_card.stability,
            "difficulty": reviewed_card.difficulty,
            "step": reviewed_card.step,
            "reps": row["reps"] + 1,
            "lapses": row["lapses"] + (1 if lapse else 0),
            "is_mastered": mastered,
        },
    }


@router.get("/queue")
def get_queue(request: Request, conn=Depends(get_db)):
    user_id = _get_user_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                e.id,
                e.user_id,
                e.puzzle_id,
                e.theme,
                e.added_at,
                e.mastered_at,
                e.is_mastered,
                e.source_reason,
                e.due,
                e.state,
                e.step,
                e.stability,
                e.difficulty,
                e.reps,
                e.lapses,
                e.last_review
            FROM woodpecker_entries e
            WHERE e.user_id = %s
              AND e.is_mastered = FALSE
              AND e.due <= NOW()
            ORDER BY e.due ASC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    return rows
