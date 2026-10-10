"""
My-puzzles endpoints: extract personal puzzles from the user's own games,
practice them with server-side grading, and report bad ones.

  POST /api/my-puzzles/extract          -> start extraction job (202)
  GET  /api/my-puzzles/extract/{job_id} -> job status (poll ~2s)
  GET  /api/my-puzzles/queue            -> due cards + full puzzle payload
  GET  /api/my-puzzles/count            -> {"due_count": N}
  POST /api/my-puzzles/attempts         -> grade one completed line
  POST /api/my-puzzles/feedback         -> "bad puzzle" button

Separate tables/queue from the Lichess puzzle + Woodpecker queues (see the
migration comment): these are positions from the user's games, graded
server-side against a stored engine line. Entries are created only by the
extraction job;
there is no public /entries endpoint, so the queue cannot be fabricated.
FSRS scheduling mirrors routers/woodpecker.py; reviews never touch ratings.
"""

import logging

import chess
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request
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
from schemas.user_puzzle_schemas import (
    UserPuzzleAttemptRequest,
    UserPuzzleAttemptResponse,
    UserPuzzleCountResponse,
    UserPuzzleExtractRequest,
    UserPuzzleExtractStartResponse,
    UserPuzzleFeedbackRequest,
    UserPuzzleFeedbackResponse,
    UserPuzzleJobResponse,
    UserPuzzleQueueEntry,
)
from services.user_puzzle_extract import (
    create_user_puzzle_job,
    get_user_puzzle_job,
    run_user_puzzle_job,
)

router = APIRouter()
log = logging.getLogger(__name__)

_DUE_PREDICATE = (
    "e.user_id = %s AND e.is_mastered = FALSE AND e.due <= NOW() "
    "AND p.excluded = FALSE"
)

_MAX_SOLUTION_PLIES = 6
_PAWN_VALUE = 1.0
_PIECE_VALUES = {
    chess.PAWN: 1.0,
    chess.KNIGHT: 3.0,
    chess.BISHOP: 3.0,
    chess.ROOK: 5.0,
    chess.QUEEN: 9.0,
}

_ENTRY_SELECT = """
    SELECT e.id,
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
           e.last_review,
           p.fen_before,
           p.best_move_uci,
           p.best_move_san,
           p.best_pv_uci,
           p.played_move_san,
           p.game_url,
           p.move_number,
           p.color
    FROM user_puzzle_entries e
    JOIN user_puzzles p ON p.id = e.puzzle_id
"""


def _clerk_id(request: Request) -> str:
    user_id = request.headers.get("X-Clerk-User-Id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return user_id


def _material_for(board: chess.Board, color: chess.Color) -> float:
    score = 0.0
    for piece_type, value in _PIECE_VALUES.items():
        score += value * len(board.pieces(piece_type, color))
        score -= value * len(board.pieces(piece_type, not color))
    return score


def _solution_line(entry) -> list[str]:
    """Return a short forcing line, or just the best move for quiet positions.

    Stage 2 already stores the engine PV. Keep at most three player decisions
    and extend only when the line reaches mate or leaves the player up at
    least a pawn after the opponent's reply. This avoids turning a positional
    best move into an arbitrary engine-PV quiz.
    """
    best_move_uci = (entry["best_move_uci"] or "").strip().lower()
    if not best_move_uci:
        return []

    try:
        board = chess.Board(entry["fen_before"])
        best_move = chess.Move.from_uci(best_move_uci)
    except (ValueError, TypeError):
        return [best_move_uci]
    if best_move not in board.legal_moves:
        return [best_move_uci]

    raw_pv = (entry.get("best_pv_uci") or "").split()
    if not raw_pv or raw_pv[0].lower() != best_move_uci:
        raw_pv = [best_move_uci]

    solver_color = board.turn
    initial_material = _material_for(board, solver_color)
    line: list[str] = []
    for token in raw_pv[:_MAX_SOLUTION_PLIES]:
        uci = token.lower()
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break

        board.push(move)
        line.append(uci)
        if board.is_checkmate():
            if board.turn != solver_color:
                return line
            break
        if board.is_game_over(claim_draw=False):
            break

        # Check material only after a complete opponent reply, so a temporary
        # capture that can immediately be recaptured does not end the puzzle.
        if len(line) >= 2 and len(line) % 2 == 0:
            if _material_for(board, solver_color) - initial_material >= _PAWN_VALUE:
                return line

    return [best_move_uci]


def _expected_move_san(
    entry, solution_line: list[str], user_moves: list[str]
) -> str | None:
    """Find the SAN move expected at the first point where the line diverges."""
    try:
        board = chess.Board(entry["fen_before"])
        for index, attempted_uci in enumerate(user_moves):
            line_index = index * 2
            if line_index >= len(solution_line):
                return None
            expected = chess.Move.from_uci(solution_line[line_index])
            expected_san = board.san(expected)
            if attempted_uci != solution_line[line_index]:
                return expected_san
            board.push(expected)
            opponent_index = line_index + 1
            if index + 1 < len(user_moves) and opponent_index < len(solution_line):
                board.push_uci(solution_line[opponent_index])
    except (ValueError, TypeError):
        return None
    return None


def _validate_user_line(
    entry, solution_line: list[str], user_moves: list[str]
) -> tuple[bool, str | None]:
    """Validate legality and return whether the complete player line is right."""
    try:
        board = chess.Board(entry["fen_before"])
        expected_user_moves = solution_line[::2]
        if len(user_moves) > len(expected_user_moves):
            raise HTTPException(status_code=400, detail="Too many moves in puzzle attempt")

        all_match = True
        for index, uci in enumerate(user_moves):
            if index >= len(expected_user_moves):
                raise HTTPException(status_code=400, detail="Too many moves in puzzle attempt")
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                raise HTTPException(status_code=400, detail="Malformed move in moves_uci")
            if move not in board.legal_moves:
                raise HTTPException(status_code=400, detail="Illegal move in puzzle attempt")

            expected_uci = expected_user_moves[index]
            if uci != expected_uci:
                all_match = False
                if index != len(user_moves) - 1:
                    raise HTTPException(
                        status_code=400,
                        detail="Moves after the first incorrect move are not accepted",
                    )
                break

            board.push(move)
            opponent_index = index * 2 + 1
            if index + 1 < len(user_moves) and opponent_index < len(solution_line):
                board.push_uci(solution_line[opponent_index])

        solved = all_match and len(user_moves) == len(expected_user_moves)
        return solved, _expected_move_san(entry, solution_line, user_moves)
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail="Stored puzzle line is invalid") from exc


@router.post("/extract", response_model=UserPuzzleExtractStartResponse, status_code=202)
def start_extract(
    body: UserPuzzleExtractRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    _: None = Depends(limit_by_clerk_user_id(limit=5, window=60)),
):
    clerk_id = _clerk_id(request)
    lichess = (body.lichess_username or "").strip() or None
    chesscom = (body.chesscom_username or "").strip() or None
    if not lichess and not chesscom:
        raise HTTPException(
            status_code=400,
            detail="Provide a Lichess username, Chess.com username, or both.",
        )
    job = create_user_puzzle_job(
        requested_by_user_id=clerk_id,
        lichess_username=lichess,
        chesscom_username=chesscom,
        limit=body.limit,
    )
    background_tasks.add_task(run_user_puzzle_job, job["job_id"])
    return UserPuzzleExtractStartResponse(job_id=job["job_id"], status="queued")


@router.get("/extract/{job_id}", response_model=UserPuzzleJobResponse)
def extract_status(
    request: Request,
    job_id: str = Path(..., min_length=1),
    _: None = Depends(limit_by_clerk_user_id(limit=90, window=60)),
):
    clerk_id = _clerk_id(request)
    job = get_user_puzzle_job(job_id=job_id, requested_by_user_id=clerk_id)
    if not job:
        log.warning("extract job %s not found for user %s", job_id, clerk_id)
        raise HTTPException(status_code=404, detail="Extract job not found")
    return UserPuzzleJobResponse(**job)


@router.get("/queue", response_model=list[UserPuzzleQueueEntry])
def get_queue(request: Request, conn=Depends(get_db)):
    clerk_id = _clerk_id(request)
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
    out = []
    for row in rows:
        side = "black" if row["fen_before"].split(" ")[1:2] == ["b"] else "white"
        out.append(
            UserPuzzleQueueEntry(
                id=row["id"],
                puzzle_id=row["puzzle_id"],
                due=row["due"].isoformat() if row["due"] else None,
                state=row["state"],
                reps=row["reps"],
                lapses=row["lapses"],
                puzzle={
                    "id": row["puzzle_id"],
                    "fen_before": row["fen_before"],
                    "best_move_uci": row["best_move_uci"],
                    "best_move_san": row["best_move_san"],
                    "solution_moves_uci": _solution_line(row),
                    "played_move_san": row["played_move_san"],
                    "game_url": row["game_url"],
                    "move_number": row["move_number"],
                    "color": row["color"],
                    "side_to_move": side,
                },
            )
        )
    return out


@router.get("/count", response_model=UserPuzzleCountResponse)
def get_due_count(request: Request, conn=Depends(get_db)):
    clerk_id = _clerk_id(request)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*) AS due_count
            FROM user_puzzle_entries e
            JOIN user_puzzles p ON p.id = e.puzzle_id
            WHERE {_DUE_PREDICATE}
            """,
            (clerk_id,),
        )
        due_count = cur.fetchone()[0]
    return UserPuzzleCountResponse(due_count=due_count)


@router.post("/attempts", response_model=UserPuzzleAttemptResponse)
def record_attempt(body: UserPuzzleAttemptRequest, request: Request, conn=Depends(get_db)):
    """Grade one full puzzle line server-side against the stored engine PV."""
    clerk_id = _clerk_id(request)
    if body.time_taken_ms < 0:
        raise HTTPException(status_code=400, detail="time_taken_ms cannot be negative")
    if body.hints_used < 0:
        raise HTTPException(status_code=400, detail="hints_used cannot be negative")
    user_moves = [move.strip().lower() for move in body.moves_uci]
    if len(user_moves) > _MAX_SOLUTION_PLIES // 2:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Too many moves in puzzle attempt")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            {_ENTRY_SELECT}
            WHERE e.id = %s::uuid AND e.user_id = %s
              AND e.is_mastered = FALSE AND e.due <= NOW()
              AND p.excluded = FALSE
            FOR UPDATE OF e
            """,
            (str(body.entry_id), clerk_id),
        )
        entry = cur.fetchone()
    if entry is None:
        conn.rollback()
        raise HTTPException(status_code=404, detail="Puzzle review entry not found")

    try:
        chess.Board(entry["fen_before"])
    except (ValueError, TypeError):
        conn.rollback()
        raise HTTPException(status_code=500, detail="Stored puzzle position is invalid")
    solution_line = _solution_line(entry)
    if not solution_line:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Stored puzzle has no legal solution move")
    try:
        solved, expected_move_san = _validate_user_line(entry, solution_line, user_moves)
    except HTTPException:
        conn.rollback()
        raise
    if not solved and not body.hints_used:
        expected_user_moves = solution_line[::2]
        prefix_is_correct = user_moves == expected_user_moves[: len(user_moves)]
        if prefix_is_correct:
            conn.rollback()
            raise HTTPException(status_code=400, detail="Puzzle line is incomplete")

    review_at = now_utc()
    card = card_from_row(entry)
    prior_state = card.state
    clean_solve = solved and body.hints_used == 0
    rating = rating_for(clean_solve)
    reviewed_card, _ = scheduler.review_card(card=card, rating=rating, review_datetime=review_at)
    lapse = is_lapse(prior_state, rating)
    mastered = is_mastered(reviewed_card)
    increment_lapses = ", lapses = lapses + 1" if lapse else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE user_puzzle_entries
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
        cur.execute(
            """
            INSERT INTO user_puzzle_attempts (
                entry_id, user_id, move_uci, moves_uci, solved_correctly,
                time_taken_ms, hints_used
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(body.entry_id),
                clerk_id,
                user_moves[-1] if user_moves else "",
                " ".join(user_moves),
                solved,
                body.time_taken_ms,
                body.hints_used,
            ),
        )
    conn.commit()
    return UserPuzzleAttemptResponse(
        entry_id=body.entry_id,
        solved=solved,
        best_move_uci=entry["best_move_uci"],
        best_move_san=entry["best_move_san"],
        expected_move_san=expected_move_san,
        scheduling={
            "rating": int(rating),
            "new_state": int(reviewed_card.state),
            "due": reviewed_card.due.isoformat(),
            "is_mastered": mastered,
            "reps": entry["reps"] + 1,
            "lapses": entry["lapses"] + (1 if lapse else 0),
        },
    )


@router.post("/feedback", response_model=UserPuzzleFeedbackResponse)
def report_bad_puzzle(body: UserPuzzleFeedbackRequest, request: Request, conn=Depends(get_db)):
    """'Bad puzzle' button: exclude the puzzle and retire its active cards."""
    clerk_id = _clerk_id(request)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT e.id, e.puzzle_id
            FROM user_puzzle_entries e
            WHERE e.id = %s::uuid AND e.user_id = %s
            """,
            (str(body.entry_id), clerk_id),
        )
        entry = cur.fetchone()
        if entry is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Puzzle review entry not found")
        cur.execute(
            """
            INSERT INTO user_puzzle_feedback (user_id, puzzle_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, puzzle_id) DO NOTHING
            """,
            (clerk_id, str(entry["puzzle_id"])),
        )
        cur.execute(
            """
            UPDATE user_puzzles
            SET bad_count = bad_count + 1, excluded = TRUE
            WHERE id = %s AND requested_by_user_id = %s
            """,
            (str(entry["puzzle_id"]), clerk_id),
        )
        cur.execute(
            """
            UPDATE user_puzzle_entries
            SET is_mastered = TRUE, mastered_at = NOW()
            WHERE puzzle_id = %s AND user_id = %s AND is_mastered = FALSE
            """,
            (str(entry["puzzle_id"]), clerk_id),
        )
    conn.commit()
    log.info("bad-puzzle feedback entry=%s puzzle=%s", body.entry_id, entry["puzzle_id"])
    return UserPuzzleFeedbackResponse(entry_id=body.entry_id, excluded=True)
