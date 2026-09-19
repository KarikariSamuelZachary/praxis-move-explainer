"""
Endgame Woodpecker queue capture: create the review-queue card for a
FAILED Endgame Trainer drill.

WHERE THIS SITS
===============
The FAILED response from POST /api/endgames/move already carries an
EndgameReviewCapture block (position id, theme, source_reason, failure
category, timestamp). This module turns that block into a row in
endgame_woodpecker_entries -- the endgame-specific mirror of
woodpecker_entries -- on the SAME transaction as the rating write, so the
user's FAILED response and the queued card are atomic: either both
persist or the move can be retried with nothing half-written.

CARD INITIALIZATION
===================
A fresh card is exactly the puzzle queue's fresh miss: all FSRS columns
at their defaults (state=1 Learning, step NULL, stability/difficulty
NULL, reps=0, lapses=0, due=NOW(), last_review NULL). No two cards for
the same (user, position) are active at once: the duplicate guard returns
the existing active card instead of inserting a second one (an
unguarded repeat failure of the same drill must not multiply the queue).
If the existing card was mastered, the insert proceeds and opens a fresh
one -- the woodpecker_entries is_mastered = FALSE guard, mirrored. The
partial unique index in core/migrations.py is the DB-layer backstop.

The trainer route holds the user's row lock (FOR UPDATE on users) from
before grading until the commit that includes this insert, so two
concurrent failures for the same user cannot both pass the guard.

NO COMMIT HERE
==============
The caller owns the transaction (same contract as endgame_library /
repertoire_service): this module only executes SQL. Rolling back the
trainer's transaction rolls the card back with the rating write.
"""
from typing import Optional
from uuid import UUID

from psycopg2.extras import RealDictCursor
from pydantic import BaseModel


class QueuedEntry(BaseModel):
    """Outcome of the capture: the active card's id, and whether THIS call
    created it (False = a duplicate guard returned an existing card)."""

    created: bool
    entry_id: str


def queue_failed_drill(
    conn,
    *,
    clerk_id: str,
    position_id: str,
    theme: str,
    source_reason: Optional[str] = "wrong_answer",
) -> QueuedEntry:
    """Insert (or find) the active endgame review card for a failed drill.

    Raises ValueError before touching the DB when position_id is not a
    UUID (programming error -- the capture block is server-built, so this
    can only fire on a code mistake) or when theme is empty. A real DB
    failure propagates: the caller's transaction aborts and the trainer
    move stays unwritten, which is the retryable failure the endpoint
    contract promises.
    """
    if not theme:
        raise ValueError("theme cannot be empty")
    try:
        parsed_position_id = UUID(str(position_id))
    except ValueError as exc:
        raise ValueError(f"position_id {position_id!r} is not a valid UUID") from exc

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # ON CONFLICT covers the (theoretically impossible under the user
        # row lock) insert race; DO NOTHING + the SELECT fallback gives the
        # idempotent "return the existing card" result either way.
        cur.execute(
            """
            INSERT INTO endgame_woodpecker_entries (
                user_id,
                position_id,
                theme,
                source_reason
            )
            VALUES (%s, %s::uuid, %s, %s)
            ON CONFLICT (user_id, position_id) WHERE is_mastered = FALSE
            DO NOTHING
            RETURNING id
            """,
            (clerk_id, str(parsed_position_id), theme, source_reason),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            return QueuedEntry(created=True, entry_id=str(inserted["id"]))

        cur.execute(
            """
            SELECT id
            FROM endgame_woodpecker_entries
            WHERE user_id = %s
              AND position_id = %s::uuid
              AND is_mastered = FALSE
            """,
            (clerk_id, str(parsed_position_id)),
        )
        existing = cur.fetchone()

    if existing is None:
        # The conflict target matched but no active row is visible: only
        # possible if the row was mastered between INSERT and SELECT on
        # another connection, which the caller's user lock rules out.
        raise RuntimeError(
            "endgame woodpecker entry conflicted but no active row found"
        )
    return QueuedEntry(created=False, entry_id=str(existing["id"]))
