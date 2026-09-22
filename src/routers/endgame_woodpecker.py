"""
Endgame Woodpecker review queue endpoints (separate from the puzzle queue).

The endgame queue mirrors the puzzle queue's read/write surface
(routers/woodpecker.py) but is a genuinely separate queue -- separate
tables, separate endpoints, never returned by the puzzle queue:

  GET  /api/endgames/woodpecker/queue    -> due cards + full drill payload
  GET  /api/endgames/woodpecker/count    -> {"due_count": N} for the badge
  POST /api/endgames/woodpecker/attempts -> grade one review move

Cards are created only by POST /api/endgames/move on a FAILED drill
(services/endgame_woodpecker.py); there is no public /entries endpoint here,
so the queue cannot be fabricated by a client. The empty table question
(separate table vs. a nullable endgame_position_id on woodpecker_entries)
is answered in core/migrations.py's comment block: every puzzle route keys
off `puzzle_id` and the attempt contract is client-asserted, so one shared
table would force a type branch into all of them and buy nothing.

SAME FULL-RESOLUTION RULE AS THE TRAINER
========================================
A review is NOT a lighter single-move check. Each user move of the replay
is graded by services/endgame_session.evaluate_endgame_move() with the same
drill-type guard and the same python-chess resolution detection the trainer
uses; the card only resolves (and FSRS only advances) on actual checkmate /
legitimate board draw / a real tablebase failure, exactly like the trainer.
The server also generates the defender's replies past the stored line
(opponent_reply_for_move(), same as POST /api/endgames/move), because a
full-resolution replay has to be playable past the end of the seeded line.

The structural difference from the puzzle queue -- flagged, not hidden --
is that one endgame review attempt spans N HTTP requests (one per user
move), because the resolution rule must be enforced server-side and the
route is stateless per move like the trainer. The FSRS write and the
attempt log row happen only on the resolving request:
  * intermediate moves return status=in_progress with no attempt/scheduling
    and write nothing (the entry row lock is released via rollback);
  * the resolving move writes the FSRS state and exactly one
    endgame_woodpecker_attempts row, with solved_correctly derived from the
    server verdict -- never asserted by the client (unlike the puzzle
    attempt endpoint);
  * the client accumulates `time_taken_ms` across the replay and sends it
    on the resolving move.
That is the same per-move contract the trainer uses, so a review client is
just the drill screen pointed at this endpoint.

HINT-ASSISTED PASSES DO NOT GRADUATE
====================================
The queue exists to confirm the user has genuinely learned the technique
unaided, so a replay that needed a hint must not advance the card the way a
clean pass does. The resolving move carries the client-owned hints_used
count; when it is > 0 the FSRS write takes the SAME path a real failed
replay already uses -- rating_for(solved=False) -> Again, i.e. Review ->
Relearning with a lapse, a short due date and is_mastered = FALSE -- instead
of Good. Concretely, "doesn't graduate" means: no state promotion, no
mastery, and the card comes back soon. It is deliberately not a silent
no-op: leaving the card untouched would keep it due forever and teach the
scheduler nothing, and reusing the failure path keeps exactly one
not-clean story in FSRS.

The attempt row stays factual: solved_correctly is the BOARD verdict
(True), and hints_used records how it was reached, so the log can
distinguish clean solves from assisted ones without re-deriving anything.

REVIEWS DO NOT TOUCH THE TRAINER RATING
=======================================
Unlike POST /api/endgames/move, a review grades with no rating arguments,
so no users.endgame_trainer_rating write happens. FSRS is the review's
outcome, matching the puzzle queue (a Woodpecker review never moves
tactical_rating).

BADGE SEMANTICS
===============
There is no separate puzzle badge endpoint in this codebase: the puzzle
tab derives "anything to do" from GET /api/woodpecker/queue, whose only
gate is `is_mastered = FALSE AND due <= NOW()`. "Due now per FSRS", not
"any entries at all". GET /count here uses the identical predicate (and
GET /api/woodpecker/count was added with the same SQL) so the two tabs
cannot develop subtly different notions of "there is something here".
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
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
from schemas.endgame_schemas import (
    EndgameOpponentReply,
    EndgamePositionResponse,
    EndgameWoodpeckerAttemptRequest,
    EndgameWoodpeckerAttemptResponse,
    EndgameWoodpeckerCountResponse,
    EndgameWoodpeckerQueueEntry,
)
from services.endgame_reply import (
    OpponentReplyUnavailableError,
    TerminalPositionError,
    opponent_reply_for_move,
)
from services.endgame_session import (
    STATE_CONFLICT_PHRASES,
    EndgameStatus,
    attach_common_mistake,
    evaluate_endgame_move,
)
from services.tablebase import TablebaseUnavailableError

router = APIRouter()
log = logging.getLogger(__name__)

# The due/count gate, byte-for-byte the predicate of the puzzle queue
# (routers/woodpecker.py GET /queue and GET /count): unmastered cards whose
# FSRS due timestamp has passed. Both endgame readers below build their
# WHERE clause from this single string so they cannot drift from each other.
_DUE_PREDICATE = "e.user_id = %s AND e.is_mastered = FALSE AND e.due <= NOW()"

# Entry + drill context, shared by the queue reader and the attempt grader.
# The position payload the queue returns is built from exactly these
# columns, and the grader needs fen/is_winning/source_moves/topic_id.
_ENTRY_SELECT = """
    SELECT e.id,
           e.user_id,
           e.position_id,
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
           e.last_review,
           p.fen,
           p.is_winning,
           p.source_puzzle_id,
           t.id AS topic_id,
           t.name AS topic_name,
           t.category AS topic_category,
           t.difficulty_rating AS topic_difficulty_rating,
           z.moves AS source_moves
    FROM endgame_woodpecker_entries e
    JOIN endgame_positions p ON p.id = e.position_id
    JOIN endgame_topics t ON t.id = p.topic_id
    LEFT JOIN puzzles z ON z.id = p.source_puzzle_id
"""


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


def _position_payload(row) -> EndgamePositionResponse:
    """The drill payload in the exact shape GET /api/endgames/next returns.

    Same field mapping as routers/endgames.py's response builder: the
    stored FEN is already after the puzzle's setup move, so `moves` drops
    the first (opponent) stored ply.
    """
    source_moves = (row.pop("source_moves", None) or "").split()
    return EndgamePositionResponse(
        id=str(row["position_id"]),
        fen=row["fen"],
        moves=source_moves[1:],
        rating=row["topic_difficulty_rating"],
        themes=[row["topic_category"], row["topic_name"]],
        gameUrl=(
            f"https://lichess.org/training/{row['source_puzzle_id']}"
            if row["source_puzzle_id"]
            else None
        ),
        is_winning=row["is_winning"],
        topic_id=str(row["topic_id"]),
        topic_name=row["topic_name"],
        topic_category=row["topic_category"],
        source_puzzle_id=row["source_puzzle_id"],
    )


def _queue_entry(row) -> EndgameWoodpeckerQueueEntry:
    position = _position_payload(row)
    return EndgameWoodpeckerQueueEntry(
        id=str(row["id"]),
        user_id=row["user_id"],
        position_id=str(row["position_id"]),
        theme=row["theme"],
        added_at=row["added_at"],
        mastered_at=row["mastered_at"],
        is_mastered=row["is_mastered"],
        source_reason=row["source_reason"],
        due=row["due"],
        state=row["state"],
        step=row["step"],
        stability=row["stability"],
        difficulty=row["difficulty"],
        reps=row["reps"],
        lapses=row["lapses"],
        last_review=row["last_review"],
        position=position,
    )


@router.get("/queue", response_model=List[EndgameWoodpeckerQueueEntry])
def get_queue(request: Request, conn=Depends(get_db)):
    """Unmastered cards due now, oldest due first -- the puzzle queue's
    shape with the drill payload nested so the review screen can render."""
    clerk_id = _require_clerk_id(request)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            {_ENTRY_SELECT}
            WHERE {_DUE_PREDICATE}
            ORDER BY e.due ASC
            """,
            (clerk_id,),
        )
        rows = cur.fetchall()
    return [_queue_entry(row) for row in rows]


@router.get("/count", response_model=EndgameWoodpeckerCountResponse)
def get_due_count(request: Request, conn=Depends(get_db)):
    """The endgame tab's badge: due-now cards only, same predicate as
    GET /api/woodpecker/count."""
    clerk_id = _require_clerk_id(request)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*) AS due_count
            FROM endgame_woodpecker_entries e
            WHERE {_DUE_PREDICATE}
            """,
            (clerk_id,),
        )
        due_count = cur.fetchone()[0]
    return EndgameWoodpeckerCountResponse(due_count=due_count)


@router.post("/attempts", response_model=EndgameWoodpeckerAttemptResponse)
def record_attempt(
    body: EndgameWoodpeckerAttemptRequest,
    request: Request,
    conn=Depends(get_db),
):
    """Grade one move of a review replay; schedule FSRS on resolution.

    Error contract mirrors the trainer's move endpoint:
      * unknown entry / unknown user           -> 404
      * malformed move, illegal move or FEN    -> 400
      * stale/wrong drill state (terminal
        fen_before, non-winning fen_before)    -> 409
      * tablebase cannot grade right now       -> 503 (nothing written)
      * opponent reply cannot be generated     -> 503 (nothing written)
      * time_taken_ms < 0                      -> 400
      * hints_used < 0                         -> 400
    """
    clerk_id = _require_clerk_id(request)
    if body.time_taken_ms < 0:
        raise HTTPException(status_code=400, detail="time_taken_ms cannot be negative")
    if body.hints_used < 0:
        raise HTTPException(status_code=400, detail="hints_used cannot be negative")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # FOR UPDATE serializes concurrent submissions for this card, so
        # two in-flight resolving moves cannot both schedule FSRS from the
        # same prior state.
        cur.execute(
            f"""
            {_ENTRY_SELECT}
            WHERE e.id = %s::uuid AND e.user_id = %s
            FOR UPDATE OF e
            """,
            (str(body.entry_id), clerk_id),
        )
        entry = cur.fetchone()
    if entry is None:
        conn.rollback()
        raise HTTPException(status_code=404, detail="Endgame review entry not found")

    try:
        result = evaluate_endgame_move(
            body.fen_before,
            body.move,
            body.fen_after,
            drill_is_winning=entry["is_winning"],
        )
    except TablebaseUnavailableError as exc:
        conn.rollback()
        log.error("endgame review ungradeable (tablebase unavailable): %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "This review move could not be verified right now. "
                "Please retry the move."
            ),
        ) from exc
    except ValueError as exc:
        conn.rollback()
        status_code = (
            409
            if any(phrase in str(exc) for phrase in STATE_CONFLICT_PHRASES)
            else 400
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    if result.status == EndgameStatus.FAILED:
        result = attach_common_mistake(conn, result, str(entry["topic_id"]))

    opponent_reply = None
    if result.status == EndgameStatus.IN_PROGRESS:
        source_moves = (
            entry["source_moves"].split() if entry["source_moves"] else None
        )
        try:
            generated = opponent_reply_for_move(
                entry["fen"], source_moves, body.fen_after
            )
        except TerminalPositionError as exc:
            conn.rollback()
            log.error("review reply requested for a terminal position: %s", exc)
            raise HTTPException(
                status_code=500, detail="Internal drill state error"
            ) from exc
        except OpponentReplyUnavailableError as exc:
            conn.rollback()
            log.error("review opponent reply unavailable for %s: %s", body.fen_after, exc)
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not generate the opponent's reply right now. "
                    "Please retry the move."
                ),
            ) from exc
        if generated is not None:
            opponent_reply = EndgameOpponentReply(
                move_uci=generated.move_uci,
                move_san=generated.move_san,
                fen_after=generated.fen_after,
                source=generated.source,
            )

    attempt = None
    scheduling = None
    if result.status == EndgameStatus.IN_PROGRESS:
        # Intermediate move of the replay: release the entry lock without
        # writing anything (mirrors the trainer's in_progress rollback).
        conn.rollback()
    elif body.retry:
        # A RETRY replays a card whose outcome was already recorded: the
        # move is graded for the verdict, but the card is not re-scheduled
        # and no second attempt row is written. Retry is practice.
        conn.rollback()
    else:
        # --- FSRS scheduling (same block as routers/woodpecker.py) --------
        review_at = now_utc()
        card = card_from_row(entry)
        prior_state = card.state
        solved = result.status == EndgameStatus.SOLVED
        # A hint-assisted pass is not a clean solve (module docstring
        # HINT-ASSISTED PASSES DO NOT GRADUATE): it must not advance the
        # card, so it takes the same FSRS path as a real failed replay --
        # Again, never Good. The attempt row below still records the board
        # verdict and the hint count.
        clean_solve = solved and body.hints_used == 0
        rating = rating_for(clean_solve)
        reviewed_card, _ = scheduler.review_card(
            card=card, rating=rating, review_datetime=review_at
        )

        lapse = is_lapse(prior_state, rating)
        mastered = is_mastered(reviewed_card)

        increment_lapses = ", lapses = lapses + 1" if lapse else ""
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                UPDATE endgame_woodpecker_entries
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
                    clerk_id,
                ),
            )
            # One attempt row per COMPLETED replay, with the server's own
            # verdict -- the client never asserts solved_correctly here.
            # solved_correctly is the board verdict; hints_used is the
            # client-owned count, kept so an auditor can tell why a solved
            # replay was scheduled as Again.
            cur.execute(
                """
                INSERT INTO endgame_woodpecker_attempts (
                    entry_id,
                    user_id,
                    solved_correctly,
                    time_taken_ms,
                    hints_used,
                    resolution,
                    failure_category
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING
                    id,
                    entry_id,
                    user_id,
                    solved_correctly,
                    time_taken_ms,
                    hints_used,
                    resolution,
                    failure_category,
                    attempted_at
                """,
                (
                    str(body.entry_id),
                    clerk_id,
                    solved,
                    body.time_taken_ms,
                    body.hints_used,
                    result.resolution.value if result.resolution else None,
                    (
                        result.failure_category.value
                        if result.failure_category
                        else None
                    ),
                ),
            )
            attempt = cur.fetchone()
        conn.commit()

        scheduling = {
            "prior_state": int(prior_state),
            "rating": int(rating),
            "new_state": int(reviewed_card.state),
            "due": reviewed_card.due.isoformat(),
            "stability": reviewed_card.stability,
            "difficulty": reviewed_card.difficulty,
            "step": reviewed_card.step,
            "reps": entry["reps"] + 1,
            "lapses": entry["lapses"] + (1 if lapse else 0),
            "is_mastered": mastered,
        }
        log.info(
            "endgame review resolved entry=%s status=%s rating=%s lapse=%s hints=%s",
            body.entry_id,
            result.status.value,
            int(rating),
            lapse,
            body.hints_used,
        )

    return EndgameWoodpeckerAttemptResponse(
        entry_id=str(body.entry_id),
        status=result.status,
        failure_category=result.failure_category,
        resolution=result.resolution,
        outcome_before=result.outcome_before,
        outcome_after=result.outcome_after,
        dtz_before=result.dtz_before,
        dtz_after=result.dtz_after,
        common_mistake=result.common_mistake,
        hints_used=body.hints_used,
        opponent_reply=opponent_reply,
        attempt=attempt,
        scheduling=scheduling,
    )
