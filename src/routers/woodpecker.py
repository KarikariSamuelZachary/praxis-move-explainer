from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core.database import get_db

router = APIRouter()

VALID_SOURCE_REASONS = {
    "wrong_answer",
    "slow_solution",
    "hint_used",
    "coach_recommended",
}


class CreateSetBody(BaseModel):
    name: str


class AddEntryBody(BaseModel):
    set_id: UUID
    puzzle_id: str
    theme: str
    source_reason: Optional[str] = None


class RecordAttemptBody(BaseModel):
    entry_id: UUID
    cycle_number: int
    solved_correctly: bool
    time_taken_ms: int


def _get_user_id(request: Request) -> str:
    user_id = request.headers.get("X-Clerk-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return user_id


@router.post("/sets")
def create_set(request: Request, body: CreateSetBody, conn=Depends(get_db)):
    user_id = _get_user_id(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Set name cannot be empty")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO woodpecker_sets (user_id, name)
            VALUES (%s, %s)
            RETURNING id, user_id, name, created_at, status, cycle_number
            """,
            (user_id, name),
        )
        row = cur.fetchone()
    conn.commit()

    return row


@router.get("/sets")
def list_sets(request: Request, conn=Depends(get_db)):
    user_id = _get_user_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                s.id,
                s.user_id,
                s.name,
                s.created_at,
                s.status,
                s.cycle_number,
                COUNT(e.id) AS entry_count,
                COUNT(e.id) FILTER (WHERE e.is_mastered) AS mastered_count
            FROM woodpecker_sets s
            LEFT JOIN woodpecker_entries e ON e.set_id = s.id
            WHERE s.user_id = %s
            GROUP BY s.id
            ORDER BY s.created_at DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    return rows


@router.get("/sets/{set_id}")
def get_set(set_id: UUID, request: Request, conn=Depends(get_db)):
    user_id = _get_user_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, user_id, name, created_at, status, cycle_number
            FROM woodpecker_sets
            WHERE id = %s AND user_id = %s
            """,
            (str(set_id), user_id),
        )
        woodpecker_set = cur.fetchone()
        if woodpecker_set is None:
            raise HTTPException(status_code=404, detail="Woodpecker set not found")

        cur.execute(
            """
            SELECT
                id,
                set_id,
                user_id,
                puzzle_id,
                theme,
                added_at,
                mastered_at,
                is_mastered,
                source_reason
            FROM woodpecker_entries
            WHERE set_id = %s AND user_id = %s
            ORDER BY added_at ASC
            """,
            (str(set_id), user_id),
        )
        entries = cur.fetchall()

    return {"set": woodpecker_set, "entries": entries}


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
        cur.execute(
            """
            SELECT id, status
            FROM woodpecker_sets
            WHERE id = %s AND user_id = %s
            """,
            (str(body.set_id), user_id),
        )
        woodpecker_set = cur.fetchone()
        if woodpecker_set is None:
            raise HTTPException(status_code=404, detail="Woodpecker set not found")
        if woodpecker_set["status"] != "active":
            raise HTTPException(status_code=400, detail="Woodpecker set is not active")

        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM woodpecker_entries
            WHERE set_id = %s
              AND user_id = %s
              AND theme = %s
              AND is_mastered = FALSE
            """,
            (str(body.set_id), user_id, theme),
        )
        unmastered_theme_count = cur.fetchone()["total"]
        if unmastered_theme_count >= 2:
            return {"skipped": True, "reason": "theme_cap_reached"}

        cur.execute(
            """
            INSERT INTO woodpecker_entries (
                set_id,
                user_id,
                puzzle_id,
                theme,
                source_reason
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING
                id,
                set_id,
                user_id,
                puzzle_id,
                theme,
                added_at,
                mastered_at,
                is_mastered,
                source_reason
            """,
            (str(body.set_id), user_id, puzzle_id, theme, body.source_reason),
        )
        row = cur.fetchone()
    conn.commit()

    return row


@router.post("/attempts")
def record_attempt(request: Request, body: RecordAttemptBody, conn=Depends(get_db)):
    user_id = _get_user_id(request)
    if body.cycle_number < 1:
        raise HTTPException(status_code=400, detail="cycle_number must be positive")
    if body.time_taken_ms < 0:
        raise HTTPException(status_code=400, detail="time_taken_ms cannot be negative")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id
            FROM woodpecker_entries
            WHERE id = %s AND user_id = %s
            """,
            (str(body.entry_id), user_id),
        )
        entry = cur.fetchone()
        if entry is None:
            raise HTTPException(status_code=404, detail="Woodpecker entry not found")

        cur.execute(
            """
            INSERT INTO woodpecker_attempts (
                entry_id,
                user_id,
                cycle_number,
                solved_correctly,
                time_taken_ms
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING
                id,
                entry_id,
                user_id,
                cycle_number,
                solved_correctly,
                time_taken_ms,
                attempted_at
            """,
            (
                str(body.entry_id),
                user_id,
                body.cycle_number,
                body.solved_correctly,
                body.time_taken_ms,
            ),
        )
        attempt = cur.fetchone()

        cur.execute(
            """
            SELECT solved_correctly, time_taken_ms
            FROM (
                SELECT *
                FROM (
                    SELECT DISTINCT ON (cycle_number)
                        cycle_number,
                        solved_correctly,
                        time_taken_ms,
                        attempted_at
                    FROM woodpecker_attempts
                    WHERE entry_id = %s
                    ORDER BY cycle_number, attempted_at DESC
                ) latest_by_cycle
                ORDER BY cycle_number DESC
                LIMIT 3
            ) recent_cycles
            ORDER BY cycle_number DESC
            """,
            (str(body.entry_id),),
        )
        recent_attempts = cur.fetchall()
        mastered = len(recent_attempts) == 3 and all(
            row["solved_correctly"] and row["time_taken_ms"] <= 20000
            for row in recent_attempts
        )

        if mastered:
            cur.execute(
                """
                UPDATE woodpecker_entries
                SET is_mastered = TRUE,
                    mastered_at = NOW()
                WHERE id = %s AND user_id = %s
                """,
                (str(body.entry_id), user_id),
            )

    conn.commit()

    return {"attempt": attempt, "mastered": mastered}


@router.get("/queue")
def get_queue(request: Request, conn=Depends(get_db)):
    user_id = _get_user_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                e.id,
                e.set_id,
                e.user_id,
                e.puzzle_id,
                e.theme,
                e.added_at,
                e.mastered_at,
                e.is_mastered,
                e.source_reason,
                s.name AS set_name,
                s.cycle_number
            FROM woodpecker_entries e
            JOIN woodpecker_sets s ON s.id = e.set_id
            WHERE e.user_id = %s
              AND e.is_mastered = FALSE
              AND s.status = 'active'
            ORDER BY e.added_at ASC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    return rows
