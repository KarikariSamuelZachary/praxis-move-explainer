"""HTTP router for the Repertoire trainer.

Pattern parity with `routers/woodpecker.py` (do not reinvent):
  * Raw psycopg2 + RealDictCursor, no SQLAlchemy.
  * Auth via the `X-Clerk-User-Id` request header — `_get_user_id`
    mirrors woodpecker's helper verbatim (same name, same 400 detail
    string).
  * FSRS reconstruction + scheduling flow on the review endpoint
    mirrors woodpecker's `record_attempt`: SELECT ... FOR UPDATE,
    reconstruct a py-fsrs Card from the row, call
    `scheduler.review_card`, persist the updated fields, and return a
    `{position, scheduling}` envelope that matches woodpecker's
    `{attempt, scheduling}` envelope field-for-field.
  * `conn=Depends(get_db)` provides the conn AND the transaction
    boundary; `get_db()` rolls back on exception, so we explicitly
    `conn.commit()` only on the success path (identical to woodpecker).

Two deliberate adaptations from the woodpecker pattern, both caused by
schema differences documented in the migration and already handled in
`core/fsrs.py`:

  1. `repertoire_positions.state` is TEXT ('Learning' / 'Review' /
     'Relearning'), not INTEGER like woodpecker_entries' FSRS state.
     We therefore use `card_from_repertoire_position_row` (name
     lookup) instead of `card_from_row` (value lookup), and persist
     `reviewed_card.state.name` (string) instead of
     `int(reviewed_card.state)`.

  2. `repertoire_positions` has no `is_mastered` / `mastered_at`
     columns (woodpecker_entries does). The "> 60-day interval means
     mastered" rule is therefore NOT persisted on the row — it is
     recomputed from the freshly-scheduled card via `is_mastered()`
     (reusing `core.fsrs.MASTERED_INTERVAL_DAYS`, no second copy of
     "60") and surfaced only in the response `scheduling` envelope.

Ownership convention (per spec):
  * Endpoint attempts to act on a repertoire/position that does not
    exist -> 404.
  * Endpoint attempts to act on a repertoire/position that exists but
    belongs to a DIFFERENT clerk user -> 403.
  The 404 detail strings are deliberately generic ("repertoire not
  found" / "repertoire position not found") so they don't reveal
  whether the resource exists for someone else; the 403 path is the
  explicit existence-leak the spec asked for.
"""
from typing import List, Optional
from uuid import UUID

import chess
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core.database import get_db
from core.fsrs import (
    card_from_repertoire_position_row,
    is_lapse,
    is_mastered,
    now_utc,
    rating_for,
    scheduler,
)
from schemas.repertoire_schemas import (
    Repertoire,
    RepertoirePosition,
    RepertoireSummary,
    RepertoireTrainingSession,
)
from services.repertoire_gaps import find_repertoire_gaps
from services.repertoire_service import (
    IllegalRepertoireMoveError,
    RepertoireNotFoundError,
    upsert_repertoire_positions,
)
from services.repertoire_tree import (
    RepertoireTreeRow,
    classify_repertoire_lines,
    count_descendants,
)

router = APIRouter()


# ---------------------------------------------------------------------
# Request bodies.
# ---------------------------------------------------------------------


class CreateRepertoireBody(BaseModel):
    name: str
    color: str  # validated against ('white', 'black') below


# Mirrors woodpecker's RecordAttemptBody shape exactly. The position
# being reviewed is identified by the URL path (`{position_id}`), not
# by a body field — so the body drops woodpecker's `entry_id` and
# keeps its `solved_correctly: bool` + `time_taken_ms: int` fields.
# `time_taken_ms` is accepted for parity but, just like in woodpecker,
# is NOT fed into the FSRS scheduler (FSRS uses the binary
# solved/not-solved signal via `rating_for`); it would only be used if
# we persisted a per-attempt log row, which repertoire_positions has
# no table for (the migration deliberately omitted
# repertoire_attempts — adding one is a separate task).
class ReviewPositionBody(BaseModel):
    solved_correctly: bool
    time_taken_ms: int


class UpsertPositionsBody(BaseModel):
    uci_moves: List[str]
    start_fen: Optional[str] = None


class StartSessionBody(BaseModel):
    # Mirrors the migration's CHECK (mode IN ('review', 'train')) —
    # validated against ('review', 'train') below.
    mode: str
    # Only meaningful when mode='train': filter the full position set
    # to main-line rows only (via classify_repertoire_lines). For
    # mode='review' this flag is accepted but ignored — review mode
    # is always the due-queue, and "main lines only" has no clean
    # meaning for a spaced-review pass.
    main_lines_only: bool = False


class CompleteSessionBody(BaseModel):
    # The client tallies this from the individual /review responses
    # during the session and reports the final count here. Validated
    # against the session's own positions_total in the complete handler
    # (0 <= positions_correct <= positions_total) so a client can't
    # report 47/18.
    positions_correct: int


# ---------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------


def _get_user_id(request: Request) -> str:
    user_id = request.headers.get("X-Clerk-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return user_id


def _load_owned_repertoire(cur, repertoire_id: str, user_id: str):
    """SELECT a repertoire row by id. Raises 404 if no such row exists,
    403 if it exists but belongs to a different user. Returns the raw
    dict on success.

    Uses a plain SELECT (no FOR UPDATE) — callers that need a write
    lock should re-SELECT ... FOR UPDATE on the row they intend to
    mutate. This keeps the ownership pre-check cheap and reusable
    across read endpoints (GET queue) and write endpoints (POST
    positions, POST review).
    """
    cur.execute(
        """
        SELECT id, user_id, name, color, created_at, updated_at
        FROM repertoires
        WHERE id = %s
        """,
        (repertoire_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="repertoire not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="repertoire not owned by user")
    return row


def _load_owned_position_for_update(cur, position_id: str, user_id: str):
    """SELECT a repertoire_positions row by id with FOR UPDATE, joining
    up to repertoires to verify ownership in the same round-trip.

    Raises 404 if no such position exists, 403 if its repertoire
    belongs to a different user. Returns the joined row dict (which
    includes the FSRS scheduling fields the caller needs to
    reconstruct a py-fsrs Card AND the `repertoire_id` / `user_id`
    columns the caller needs for the UPDATE WHERE clause).

    FOR UPDATE locks the position row for the duration of the
    transaction, matching woodpecker's record_attempt lock strategy —
    so two concurrent reviews of the same position can't race on
    stale FSRS state.
    """
    cur.execute(
        """
        SELECT
            p.id,
            p.repertoire_id,
            p.fen,
            p.move,
            p.due,
            p.stability,
            p.difficulty,
            p.state,
            p.step,
            p.reps,
            p.lapses,
            p.last_review,
            p.created_at,
            p.updated_at,
            r.user_id
        FROM repertoire_positions p
        JOIN repertoires r ON p.repertoire_id = r.id
        WHERE p.id = %s
        FOR UPDATE
        """,
        (position_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="repertoire position not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="repertoire not owned by user")
    return row


def _load_owned_session_for_update(cur, session_id: str, user_id: str):
    """SELECT a repertoire_training_sessions row by id with FOR UPDATE,
    joining up to repertoires to verify ownership in the same round-trip.

    Raises 404 if no such session exists, 403 if its repertoire belongs
    to a different user. Returns the joined row dict (which includes
    the `user_id` column from repertoires + the session's own columns
    the complete handler needs: positions_total, completed_at, mode,
    positions_correct, started_at, repertoire_id).

    FOR UPDATE locks the session row for the duration of the
    transaction — so two concurrent /complete calls on the same
    session can't race: the second blocks until the first commits
    (and then sees completed_at IS NOT NULL and returns 400). Matches
    _load_owned_position_for_update's lock strategy exactly.
    """
    cur.execute(
        """
        SELECT
            s.id,
            s.repertoire_id,
            s.mode,
            s.positions_total,
            s.positions_correct,
            s.started_at,
            s.completed_at,
            r.user_id
        FROM repertoire_training_sessions s
        JOIN repertoires r ON s.repertoire_id = r.id
        WHERE s.id = %s
        FOR UPDATE
        """,
        (session_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="training session not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="repertoire not owned by user")
    return row


# ---------------------------------------------------------------------
# Column lists shared between RETURNING clauses and RepertoirePosition
# schema construction. Kept in sync with the migration's column list
# and the Pydantic model's field list by construction — if you add a
# column to the migration, add it here AND to the schema.
# ---------------------------------------------------------------------

_POSITION_COLUMNS = """
    id,
    repertoire_id,
    fen,
    move,
    due,
    stability,
    difficulty,
    state,
    step,
    reps,
    lapses,
    last_review,
    created_at,
    updated_at
"""

# Session columns shared between INSERT RETURNING and
# RepertoireTrainingSession schema construction. Kept in sync with the
# migration's repertoire_training_sessions column list and the
# Pydantic model's field list by construction.
_SESSION_COLUMNS = """
    id,
    repertoire_id,
    mode,
    positions_total,
    positions_correct,
    started_at,
    completed_at
"""


# ---------------------------------------------------------------------
# Endpoints.
# ---------------------------------------------------------------------


@router.post("")
def create_repertoire(request: Request, body: CreateRepertoireBody, conn=Depends(get_db)):
    user_id = _get_user_id(request)

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name cannot be empty")
    if body.color not in ("white", "black"):
        raise HTTPException(status_code=400, detail="color must be 'white' or 'black'")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO repertoires (user_id, name, color)
            VALUES (%s, %s, %s)
            RETURNING id, user_id, name, color, created_at, updated_at
            """,
            (user_id, name, body.color),
        )
        row = cur.fetchone()
    conn.commit()

    return Repertoire(**row)


@router.get("")
def list_repertoires(request: Request, conn=Depends(get_db)):
    """List all repertoires belonging to the authenticated user, each
    annotated with its most recent COMPLETED training session.

    For each repertoire the response includes the plain repertoire
    fields plus three derived columns:
      * `last_trained_at`  — completed_at of the latest session with
        completed_at IS NOT NULL (null if none).
      * `times_trained`   — count of completed sessions.
      * `last_score_percent` — positions_correct / positions_total *
        100 from that latest session (null if no completed session).

    A repertoire with zero completed sessions is returned with
    last_trained_at=null, times_trained=0, last_score_percent=null
    — it is NOT excluded. The two LATERAL subqueries below match the
    `idx_repertoire_training_sessions_repertoire_completed` index
    shape (repertoire_id, completed_at DESC) so the latest-session
    lookup and the count are index-served rather than re-scanning.

    Read-only path: no conn.commit() (matches get_queue / get_gaps).
    """
    user_id = _get_user_id(request)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                r.id,
                r.name,
                r.color,
                r.created_at,
                r.updated_at,
                s.completed_at AS last_trained_at,
                COALESCE(c.times_trained, 0) AS times_trained,
                CASE
                    WHEN s.id IS NULL THEN NULL
                    ELSE (s.positions_correct * 100.0 / s.positions_total)
                END AS last_score_percent
            FROM repertoires r
            LEFT JOIN LATERAL (
                SELECT id, completed_at, positions_correct, positions_total
                FROM repertoire_training_sessions
                WHERE repertoire_id = r.id
                  AND completed_at IS NOT NULL
                ORDER BY completed_at DESC
                LIMIT 1
            ) s ON TRUE
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS times_trained
                FROM repertoire_training_sessions
                WHERE repertoire_id = r.id
                  AND completed_at IS NOT NULL
            ) c ON TRUE
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    # No conn.commit() — read-only path. get_db() returns the conn to
    # the pool on generator close.
    return [RepertoireSummary(**r) for r in rows]


@router.get("/{repertoire_id}")
def get_repertoire(
    request: Request,
    repertoire_id: UUID,
    conn=Depends(get_db),
):
    """Fetch a single repertoire by id.

    Returns the plain `Repertoire` row shape (id, user_id, name, color,
    created_at, updated_at) — NOT `RepertoireSummary`: this is the
    detail-page view, not the list-page view, so the
    last_trained_at / times_trained / last_score_percent aggregates the
    LIST endpoint computes are not surfaced here (a client that wants
    them already has the list-row cached; that's not this endpoint's
    job).

    Ownership is verified via the same `_load_owned_repertoire`
    pre-check every other single-repertoire endpoint uses (404 if the
    row is missing, 403 if it belongs to a different user). The helper
    SELECTs exactly the columns `Repertoire` carries, so we just return
    the row directly — no separate query, no second round-trip.

    Read-only: no conn.commit().
    """
    user_id = _get_user_id(request)
    rid = str(repertoire_id)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        row = _load_owned_repertoire(cur, rid, user_id)

    # No conn.commit() — read-only path. get_db() returns the conn to
    # the pool on generator close. (Matches get_queue / get_gaps /
    # list_positions.)
    return Repertoire(**row)


@router.delete("/{repertoire_id}")
def delete_repertoire(
    request: Request,
    repertoire_id: UUID,
    conn=Depends(get_db),
):
    """Delete a repertoire and everything it owns.

    The migration's `ON DELETE CASCADE` on both
    `repertoire_positions.repertoire_id` and
    `repertoire_training_sessions.repertoire_id` does the cascading
    work in the DB: a single `DELETE FROM repertoires WHERE id = %s`
    also drops every position and every training-session row for that
    repertoire, with no extra statement here. (Verify the CASCADE
    clauses are present in `src/core/migrations.py` before relying on
    this — they are, as of the original repertoires / positions
    migration and the training-sessions migration.)

    Ownership is verified via the same `_load_owned_repertoire`
    pre-check every other endpoint uses (404/403; 404 detail is the
    deliberately-generic "repertoire not found" so a caller can't
    probe whether someone else's repertoire exists). The pre-check is
    a plain SELECT (no FOR UPDATE); the DELETE itself is atomic
    within the transaction and targets the row by id, so a concurrent
    delete would simply delete 0 rows the second time (RETURNING
    returns None -> we surface 404, matching the pre-check semantics).

    Returns the deleted `Repertoire` row (same shape POST "" returns)
    so the client can confirm what was removed without a separate
    fetch that would now 404.
    """
    user_id = _get_user_id(request)
    rid = str(repertoire_id)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Ownership pre-check: 404/403 if the repertoire is missing or
        # not the caller's. Runs before the DELETE so a 403 deny
        # doesn't mutate anything.
        _load_owned_repertoire(cur, rid, user_id)

        cur.execute(
            """
            DELETE FROM repertoires
            WHERE id = %s
            RETURNING id, user_id, name, color, created_at, updated_at
            """,
            (rid,),
        )
        row = cur.fetchone()

    conn.commit()

    return Repertoire(**row)


@router.post("/{repertoire_id}/positions")
def add_positions(
    request: Request,
    repertoire_id: UUID,
    body: UpsertPositionsBody,
    conn=Depends(get_db),
):
    user_id = _get_user_id(request)
    rid = str(repertoire_id)

    if not body.uci_moves:
        # Let the empty list flow through to the service — it returns
        # [] cleanly. We do NOT raise 400 here: an empty upsert is a
        # legal no-op (e.g. a client pre-registering a repertoire
        # before adding lines). The service contract spells this out.
        pass

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Ownership pre-check. Raises 404/403 on miss; on hit, drops
        # the row (we only need the existence + user_id check here —
        # the service re-fetches the repertoire to read `color` for
        # the replay-and-plan step).
        _load_owned_repertoire(cur, rid, user_id)

    try:
        # Resolve Optional[str] -> str. Passing body.start_fen through
        # as-is would send None to the service when the client omitted
        # the field, and None does NOT mean "use your default" to
        # python-chess: `chess.Board(None).fen()` returns the EMPTY
        # board ('8/8/8/8/8/8/8/8 w - - 0 1'), not the standard
        # starting position. Every position-less upsert would then
        # fail at ply 0 (e.g. 'e2e4' is illegal on an empty board).
        # Resolve to the service's real default here instead.
        start_fen = body.start_fen if body.start_fen is not None else chess.STARTING_FEN
        written = upsert_repertoire_positions(
            conn,
            repertoire_id=rid,
            uci_moves=body.uci_moves,
            start_fen=start_fen,
        )
    except IllegalRepertoireMoveError as exc:
        # Surface the offending ply index + move in the response body
        # so the client can locate the bad input in its move list.
        # 400 (not 422) — the body parsed fine; the *moves* are
        # semantically invalid for the replay.
        raise HTTPException(
            status_code=400,
            detail={
                "error": "illegal_uci_move",
                "ply_index": exc.ply_index,
                "uci_move": exc.uci_move,
                "reason": exc.reason,
            },
        ) from exc
    except RepertoireNotFoundError as exc:
        # The service re-fetches the repertoire and raises this if it
        # vanished between our ownership pre-check and the service's
        # own fetch. Extremely unlikely (same transaction), but mapped
        # to 404 for completeness.
        raise HTTPException(status_code=404, detail="repertoire not found") from exc

    conn.commit()
    return written


@router.get("/{repertoire_id}/positions")
def list_positions(request: Request, repertoire_id: UUID, conn=Depends(get_db)):
    """Return EVERY stored position for this repertoire, in storage order.

    Distinct from GET /queue (which is due-filtered for the spaced-
    review queue). The detail page needs the full set so it can show
    "My saved moves" for any position the user has prepared, even one
    whose FSRS `due` is still in the future — `due` is a review-
    scheduling concept and has nothing to do with whether a move has
    actually been chosen at a position.

    Same path as POST /{id}/positions but different HTTP method — FastAPI
    dispatches by method, so both routes coexist without collision.

    Ownership: 404/403 via the same `_load_owned_repertoire` pre-check
    every other endpoint uses. Read-only: no conn.commit().
    """
    user_id = _get_user_id(request)
    rid = str(repertoire_id)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Ownership pre-check: 404/403 if the repertoire is missing or
        # not the caller's. Reuses the same helper as the read/write
        # endpoints so existence/ownership semantics are identical
        # across the surface.
        _load_owned_repertoire(cur, rid, user_id)

        cur.execute(
            f"""
            SELECT {_POSITION_COLUMNS}
            FROM repertoire_positions
            WHERE repertoire_id = %s
            ORDER BY created_at ASC
            """,
            (rid,),
        )
        rows = cur.fetchall()

    # No conn.commit() — read-only path. get_db() returns the conn to
    # the pool on generator close. (Matches get_queue / get_gaps.)
    return [RepertoirePosition(**r) for r in rows]


@router.get("/{repertoire_id}/queue")
def get_queue(request: Request, repertoire_id: UUID, conn=Depends(get_db)):
    user_id = _get_user_id(request)
    rid = str(repertoire_id)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Ownership pre-check: 404/403 if the repertoire is missing or
        # not the caller's. Reuses the same helper as the write
        # endpoints so the existence/ownership semantics are
        # identical across the surface.
        _load_owned_repertoire(cur, rid, user_id)

        cur.execute(
            f"""
            SELECT {_POSITION_COLUMNS}
            FROM repertoire_positions
            WHERE repertoire_id = %s
              AND due <= NOW()
            ORDER BY due ASC
            """,
            (rid,),
        )
        rows = cur.fetchall()

    # No conn.commit() — read-only path. get_db() returns the conn to
    # the pool on generator close. (Woodpecker's get_queue likewise
    # doesn't commit.)
    return [RepertoirePosition(**r) for r in rows]


@router.get("/{repertoire_id}/gaps")
def get_gaps(request: Request, repertoire_id: UUID, conn=Depends(get_db)):
    """Find positions where the opponent has common replies the user
    has no prepared response to. See `services/repertoire_gaps.py` for
    the algorithm. Returns a `RepertoireGapReport` with two lists:

      * `gaps`: unprepared opponent replies above the frequency
        threshold, in storage order.
      * `unchecked_positions`: rows the gap-finder skipped (Lichess
        Explorer failure or stale stored move) — surfaced rather than
        dropped silently.

    Ownership is verified via the same `_load_owned_repertoire`
    pre-check the other endpoints use (404/403). The gap analysis
    itself is read-only relative to the DB; no commit is issued.
    """
    user_id = _get_user_id(request)
    rid = str(repertoire_id)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Ownership pre-check raises 404/403 before any external API
        # call goes out — so a 403 deny doesn't leak Lichess Explorer
        # traffic for repertoires the caller can't read.
        _load_owned_repertoire(cur, rid, user_id)

    # find_repertoire_gaps opens its own cursor on `conn`; that's fine
    # — psycopg2 supports multiple concurrent cursors on a single conn,
    # and `get_db()` returns the conn to the pool on generator close
    # (no commit needed; gap analysis reads only).
    return find_repertoire_gaps(conn, repertoire_id=rid)


@router.post("/{repertoire_id}/sessions/start")
def start_session(
    request: Request,
    repertoire_id: UUID,
    body: StartSessionBody,
    conn=Depends(get_db),
):
    """Start a new training session against this repertoire and return
    the list of positions the client should now present.

    Selector by mode (mirrors the migration's
    CHECK (mode IN ('review', 'train'))):
      * 'review' — same query as GET /queue: positions at this
        repertoire whose due <= NOW(), ordered by due ASC.
      * 'train' — ALL repertoire_positions for this repertoire. If
        body.main_lines_only is true, additionally filter to only
        main-line rows by calling classify_repertoire_lines on the
        (id, fen, move, created_at) projection.

    If the selected position set is empty, return 400 — do NOT insert
    a session row. The migration's
    CHECK (positions_total > 0) would reject a zero-total INSERT
    anyway; surfacing the empty case as 400 BEFORE the INSERT gives
    the client a clean "nothing to train" signal and keeps the
    CHECK constraint as a backstop rather than the user-facing error.

    Returns the new session (`RepertoireTrainingSession`, with
    completed_at=null) plus the full list of `RepertoirePosition`
    rows to train — same shape GET /queue returns. The client is
    responsible for walking that list, calling
    /positions/{position_id}/review on each one (unchanged — that
    endpoint is not session-aware), tallying positions_correct, and
    finally calling /sessions/{session_id}/complete.
    """
    user_id = _get_user_id(request)
    rid = str(repertoire_id)

    if body.mode not in ("review", "train"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'review' or 'train'",
        )

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Ownership pre-check: 404/403 if the repertoire is missing
        # or not the caller's. Runs before any position fetch so a
        # 403 deny doesn't reveal position counts for someone else's
        # repertoire.
        _load_owned_repertoire(cur, rid, user_id)

        if body.mode == "review":
            # Same query as GET /queue — due <= NOW(), due ASC. The
            # SELECT column list is shared via _POSITION_COLUMNS so
            # the row dict lands cleanly into RepertoirePosition.
            cur.execute(
                f"""
                SELECT {_POSITION_COLUMNS}
                FROM repertoire_positions
                WHERE repertoire_id = %s
                  AND due <= NOW()
                ORDER BY due ASC
                """,
                (rid,),
            )
            rows = cur.fetchall()
        else:
            # mode == 'train': all rows for this repertoire. If the
            # client also asked for main_lines_only, filter after the
            # SELECT via the pure tree-walk classifier — same module
            # already used for read-side classification, imported
            # here as a pure function so we don't need a second
            # schema-aware conn cursor.
            cur.execute(
                f"""
                SELECT {_POSITION_COLUMNS}
                FROM repertoire_positions
                WHERE repertoire_id = %s
                ORDER BY created_at ASC
                """,
                (rid,),
            )
            rows = cur.fetchall()

            if body.main_lines_only and rows:
                # classify_repertoire_lines takes the minimal
                # (id, fen, move, created_at) projection; we already
                # have that as a subset of _POSITION_COLUMNS. Build
                # RepertoireTreeRow objects and run the classifier,
                # then keep only rows the classifier marked main-line.
                tree_rows = [
                    RepertoireTreeRow(
                        id=r["id"],
                        fen=r["fen"],
                        move=r["move"],
                        created_at=r["created_at"],
                    )
                    for r in rows
                ]
                main_line = classify_repertoire_lines(tree_rows)
                rows = [r for r in rows if main_line.get(r["id"], True)]

        if not rows:
            # Nothing to train. The migration's
            # CHECK (positions_total > 0) would reject a zero-total
            # INSERT — surface the empty case as 400 BEFORE the
            # INSERT so the client gets a clean signal rather than a
            # Postgres constraint-violation 500.
            raise HTTPException(
                status_code=400,
                detail="no positions to train for this mode",
            )

        # Insert the session row. positions_total is set now (not
        # updated as the client reviews); positions_correct starts at
        # 0 and is updated by /complete. completed_at is NULL — the
        # client will set it via /complete once it has tallied the
        # final score. RETURNING gives us the default-populated row
        # (id from gen_random_uuid(), started_at from NOW(),
        # positions_correct from the column DEFAULT 0) without a
        # second SELECT.
        cur.execute(
            f"""
            INSERT INTO repertoire_training_sessions (
                repertoire_id,
                mode,
                positions_total,
                positions_correct,
                completed_at
            )
            VALUES (%s, %s, %s, 0, NULL)
            RETURNING {_SESSION_COLUMNS}
            """,
            (rid, body.mode, len(rows)),
        )
        session_row = cur.fetchone()

    conn.commit()

    return {
        "session": RepertoireTrainingSession(**session_row),
        "positions": [RepertoirePosition(**r) for r in rows],
    }


@router.post("/sessions/{session_id}/complete")
def complete_session(
    request: Request,
    session_id: UUID,
    body: CompleteSessionBody,
    conn=Depends(get_db),
):
    """Mark a training session complete with the client's tally of
    positions_correct.

    The client is responsible for walking the /start response's
    position list, calling /positions/{position_id}/review on each
    one (unchanged — that endpoint is not session-aware and updates
    the position's FSRS state independently), and tallying
    positions_correct itself from those /review responses. This
    endpoint is just the final commit: set completed_at = NOW() and
    positions_correct = body.positions_correct.

    Validation:
      * 404/403 if the session doesn't exist or belongs to a
        different user (via the same JOIN-to-repertoires pattern
        _load_owned_position_for_update uses).
      * 400 if positions_correct is outside
        [0, session.positions_total] — a client can't report 47/18.
      * 400 if completed_at is already set — a session can only be
        completed once. We do NOT silently overwrite; re-completing
        would silently change the GET /api/repertoires
        last_score_percent / last_trained_at aggregates, which the
        client is caching, so reject instead.

    Returns the updated `RepertoireTrainingSession` (completed_at now
    set, positions_correct now the client's tally).
    """
    user_id = _get_user_id(request)
    sid = str(session_id)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # SELECT ... FOR UPDATE + ownership check in one round-trip.
        # Raises 404 (missing) / 403 (not owner). Lock held until
        # COMMIT so two concurrent /complete calls on the same
        # session can't race — the second blocks and then sees
        # completed_at IS NOT NULL and returns 400.
        row = _load_owned_session_for_update(cur, sid, user_id)

        if row["completed_at"] is not None:
            # Already complete — reject rather than silently
            # overwriting. The 400 detail names the condition
            # explicitly so the client can distinguish "already
            # done" from any other validation failure.
            raise HTTPException(
                status_code=400,
                detail="training session already completed",
            )

        if body.positions_correct < 0 or body.positions_correct > row["positions_total"]:
            # Inclusive bounds: 0 ..= positions_total. A client
            # reporting 47/18 is rejected before any UPDATE touches
            # the row.
            raise HTTPException(
                status_code=400,
                detail=(
                    f"positions_correct must be between 0 and "
                    f"{row['positions_total']} inclusive, got "
                    f"{body.positions_correct}"
                ),
            )

        cur.execute(
            f"""
            UPDATE repertoire_training_sessions
            SET completed_at      = NOW(),
                positions_correct = %s
            WHERE id = %s
            RETURNING {_SESSION_COLUMNS}
            """,
            (body.positions_correct, sid),
        )
        updated = cur.fetchone()

    conn.commit()

    return RepertoireTrainingSession(**updated)


@router.delete("/positions/{position_id}")
def delete_position(
    request: Request,
    position_id: UUID,
    conn=Depends(get_db),
):
    """Delete a single repertoire_positions row.

    Distinct from DELETE /repertoires/{repertoire_id} (which cascades
    to every position + session row): this endpoint removes ONE
    prepared move at a time, leaving the rest of the repertoire
    intact. The detail page's "My saved moves" trash icon wires to
    this.

    Cascade-or-orphan? The schema has no FK edges between rows
    (the tree is derived via FEN-replay in `services/repertoire_tree.py`,
    not via parent_id columns), so deleting a position that has
    descendants further down the line would silently orphan them:
    the descendant rows would still exist in the DB but become
    unreachable from the tree-walk (no parent row points to them
    anymore — FENs alone aren't edges). Silently cascade-deleting
    descendants would be far riskier than the user asked for (a
    single trash-icon click would wipe a multi-ply line). Silently
    leaving orphans would surface later as a confusing
    "this position exists but the gap-finder can't reach it"
    downstream. So: detect the case up front and refuse with 409.

    Detection: `count_descendants(rows, position_id)` in
    `services/repertoire_tree.py` walks the stored rows forward from
    the target's reconstructed board state using the same replay
    semantics the rest of the tree module uses. If the count is > 0,
    we 409 with the count in the detail so the UI can say "remove
    those first." If 0, we DELETE and return the deleted row.
    `count_descendants` is exercised by the existing test file
    `services/repertoire_tree_test.py` (the fork-below-parent and
    transposition-dedup cases are the off-by-one / double-count
    categories that are easy to get subtly backwards); fixing
    `count_descendants` requires updating those tests in lockstep.

    Ownership is verified via the same `_load_owned_position_for_update`
    JOIN-to-repertoires pre-check `review_position` uses (404 if the
    position is missing, 403 if its repertoire belongs to a different
    user). The FOR UPDATE lock this helper acquires covers the TARGET
    row only: it serializes a concurrent double-delete of this same
    position (see the RETURNING note below) and a concurrent
    /review on this same position (which would also FOR-UPDATE the
    row) — so a delete can't race with an in-flight review that's
    still computing FSRS state from the pre-delete row.

    Snapshot-consistency caveat worth knowing (the descendant
    count's SELECT does NOT take a row lock): count_descendants
    reads EVERY row of this repertoire via a plain SELECT
    (not FOR UPDATE) inside this same transaction. Under Postgres's
    default READ COMMITTED isolation, that snapshot is a
    per-statement point-in-time view of the table, NOT a
    transaction-consistent lock on every other row in the
    repertoire. If a SECOND request mutates a DIFFERENT position in
    this repertoire between our count-SELECT and our COMMIT, the
    descendant count we acted on can be stale by the time the DELETE
    commits:
      * A concurrent delete of one of the counted descendants: we
        over-counted, but our parent delete is still SAFE in that
        direction — fewer descendants exist post-commit than we
        thought, so no orphan risk.
      * A concurrent POST `/repertoires/{id}/positions` upsert that
        adds a NEW descendant row beneath the target (an upsert of
        a row whose owner FEN sits below the target's subtree): the
        worst-case direction. We computed "0 descendants",
        proceeded to delete the parent, and an orphan row is left
        behind. Future tree-walks (`classify_repertoire_lines`,
        `count_descendants`, the gap-finder) will mark that orphan
        unreachable with a WARNING — matching the existing
        unreachable-row contract — but the orphan we set out to
        refuse to create will exist anyway.
    Closing this properly would require either SERIALIZABLE
    isolation (with retry-on-serialization-failure — heavyweight for
    one endpoint) or a range/table lock during the count (also
    heavyweight). The window is narrow (between our count-SELECT and
    our DELETE-then-COMMIT — milliseconds) and the triggering
    request — "user adds a move to the subtree beneath a position
    they're concurrently deleting" — is outside normal interactive
    single-user flow. We deliberately accept the residual window
    rather than back this endpoint with SERIALIZABLE; this caveat
    is spelled out so readers know what the lock above does and
    doesn't guarantee, rather than trusting a confident framing
    that covered only the target row.

    RETURNING (same pattern as `delete_repertoire`): the deleted row
    so the client can confirm what was removed. A concurrent second
    DELETE of THIS SAME position between the ownership pre-check and
    our actual DELETE would either (a) block on the FOR UPDATE lock
    above and then see RETURNING -> None on our side (we 404), or
    (b) have already committed and removed the row before our SELECT
    FOR UPDATE even started — the pre-check itself would have
    returned None and 404'd. Either way a double-delete of THIS row
    is told apart from a real 404 the same way `delete_repertoire`
    tells it apart. (Concurrent deletes of OTHER positions in the
    same repertoire are NOT serialized by the lock above — see the
    snapshot-consistency caveat.)
    """
    user_id = _get_user_id(request)
    pid = str(position_id)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # SELECT ... FOR UPDATE + ownership check in one round-trip.
        # Raises 404 (missing) / 403 (not owner). The FOR UPDATE lock
        # is held until COMMIT, so a concurrent review on the same
        # position can't slip in between our descendant-count and our
        # DELETE (the review would see either the row still present
        # and serialized behind our DELETE, or absent entirely).
        row = _load_owned_position_for_update(cur, pid, user_id)
        rid = row["repertoire_id"]

        # SELECT every row of this repertoire as the tree walker
        # expects. We only need (id, fen, move, created_at) — the
        # minimal projection `RepertoireTreeRow` carries — plus
        # repertoire_id for the WHERE clause. Limit by repertoire_id
        # (NOT by user_id) since the FOR UPDATE above already proved
        # ownership of this position's repertoire; the rest of those
        # rows belong to the same user by transitivity (the
        # repertoire_id is the same row we just locked).
        #
        # This is a plain SELECT, NOT FOR UPDATE — the snapshot-
        # consistency caveat in the docstring above applies here:
        # a concurrent mutation of another position in this
        # repertoire between this SELECT and our COMMIT can make
        # `count_descendants`'s count stale by the time the DELETE
        # runs. The accepted narrow window is documented above.
        cur.execute(
            """
            SELECT id, fen, move, created_at
            FROM repertoire_positions
            WHERE repertoire_id = %s
            """,
            (rid,),
        )
        all_rows = cur.fetchall()
        tree_rows = [
            RepertoireTreeRow(
                id=r["id"],
                fen=r["fen"],
                move=r["move"],
                created_at=r["created_at"],
            )
            for r in all_rows
        ]

        descendant_count = count_descendants(tree_rows, pid)
        if descendant_count > 0:
            # 409 Conflict — the target has prepared responses beneath
            # it that would orphan on delete. We deliberately do NOT
            # cascade-delete and do NOT silently proceed. The detail
            # payload names the count so the client UI can prompt the
            # user to remove those first. 409 (not 400) because the
            # request was well-formed; the conflict is with the
            # current state of the resource (its descendant subtree).
            raise HTTPException(
                status_code=409,
                detail=(
                    f"this position has {descendant_count} prepared "
                    f"response{'s' if descendant_count != 1 else ''} "
                    f"beneath it — remove those first"
                ),
            )

        cur.execute(
            f"""
            DELETE FROM repertoire_positions
            WHERE id = %s
            RETURNING {_POSITION_COLUMNS}
            """,
            (pid,),
        )
        deleted = cur.fetchone()

    conn.commit()

    # deleted is None only if a concurrent DELETE slipped in between
    # our ownership pre-check (which holds FOR UPDATE until COMMIT)
    # and our actual DELETE — the lock would have serialized them,
    # so this is essentially unreachable in practice. We surface 404
    # to match `delete_repertoire`'s "tell apart a real 404 from a
    # concurrent double-delete" contract.
    if deleted is None:
        raise HTTPException(status_code=404, detail="repertoire position not found")

    return RepertoirePosition(**deleted)


@router.post("/positions/{position_id}/review")
def review_position(
    request: Request,
    position_id: UUID,
    body: ReviewPositionBody,
    conn=Depends(get_db),
):
    user_id = _get_user_id(request)
    pid = str(position_id)

    if body.time_taken_ms < 0:
        # Matches woodpecker's record_attempt validation exactly.
        raise HTTPException(status_code=400, detail="time_taken_ms cannot be negative")

    review_at = now_utc()

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # SELECT ... FOR UPDATE + ownership check in one round-trip.
        # Raises 404 (missing) / 403 (not owner). Lock held until
        # COMMIT so a concurrent review of the same position sees the
        # updated FSRS state, not the pre-review snapshot.
        row = _load_owned_position_for_update(cur, pid, user_id)

        # --- FSRS scheduling ---
        # Mirrors woodpecker's record_attempt line for line, EXCEPT:
        #   * card_from_repertoire_position_row (TEXT-state lookup)
        #     instead of card_from_row (INT-state lookup).
        #   * persisted `state` is the FSRS State enum NAME, not int
        #     (the column is TEXT in this schema).
        #   * no `is_mastered` / `mastered_at` persistence (no such
        #     columns); `mastered` is computed for the response only.
        card = card_from_repertoire_position_row(row)
        prior_state = card.state
        rating = rating_for(body.solved_correctly)
        reviewed_card, _ = scheduler.review_card(
            card=card, rating=rating, review_datetime=review_at
        )

        lapse = is_lapse(prior_state, rating)
        mastered = is_mastered(reviewed_card)

        # Persist the updated FSRS state. `state` is the enum NAME
        # (string), so the migration's
        # `CHECK (state IN ('Learning', 'Review', 'Relearning'))`... —
        # well, the migration doesn't CHECK on repertoire_positions
        # (it does CHECK on woodpecker_entries' INTEGER state), but
        # the column DEFAULT is `'Learning'` and every writer is
        # expected to use one of the three State names. We rely on
        # py-fsrs's State enum name set being exactly that triple.
        increment_lapses = ", lapses = lapses + 1" if lapse else ""
        cur.execute(
            f"""
            UPDATE repertoire_positions
            SET due          = %s,
                stability    = %s,
                difficulty   = %s,
                state        = %s,
                step         = %s,
                last_review  = %s,
                updated_at   = NOW(),
                reps         = reps + 1{increment_lapses}
            WHERE id = %s
            RETURNING {_POSITION_COLUMNS}
            """,
            (
                reviewed_card.due,
                reviewed_card.stability,
                reviewed_card.difficulty,
                reviewed_card.state.name,
                reviewed_card.step,
                review_at,
                pid,
            ),
        )
        updated = cur.fetchone()

    conn.commit()

    return {
        "position": RepertoirePosition(**updated),
        "scheduling": {
            # State in this schema is TEXT (enum name), not int — so
            # the envelope surfaces string state values to match what
            # the row actually stores. Woodpecker's envelope uses
            # int(state) because woodpecker_entries.state is INTEGER.
            "prior_state": prior_state.name,
            "rating": int(rating),
            "new_state": reviewed_card.state.name,
            "due": reviewed_card.due.isoformat(),
            "stability": reviewed_card.stability,
            "difficulty": reviewed_card.difficulty,
            "step": reviewed_card.step,
            "reps": row["reps"] + 1,
            "lapses": row["lapses"] + (1 if lapse else 0),
            # Recomputed via the >60-day rule reused from
            # core.fsrs.is_mastered — no second copy of "60".
            "is_mastered": mastered,
        },
    }