"""POST /api/calc/why: engine-derived consequence of a wrong solver move.

Everything derives server-side: for kind='solve' from the calc_attempts
row (segment, offset, n, submitted line, divergence ply); for kind='review'
from the woodpecker review attempt's entry (same exercise shape) and its
source attempt's submitted line. The client supplies only ids — never a FEN
or a move. Defender-reply divergences return defender_misjudged with no
engine call. Results are stored per attempt id (idempotent) and cached by
(position_key, W, solution continuation) across users; the cache is
bypassed when the stored engine version or node budget differs from the
current ones. At most one search runs per process; the endpoint 429s when
busy.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import RealDictCursor

from core.database import get_db
from core.rate_limit import limit_by_clerk_user_id
from schemas.calc_schemas import CalcWhyRequest, CalcWhyResponse
from services import calc_why_engine as engine
from services.calc_why import classify, derive_inputs
from services.calc_grading import exercise_line_tokens

router = APIRouter()
log = logging.getLogger(__name__)


def _require_clerk_id(request: Request) -> str:
    clerk_id = request.headers.get("X-Clerk-User-Id")
    if not clerk_id:
        raise HTTPException(status_code=400, detail="Missing X-Clerk-User-Id header")
    return clerk_id


def _to_response(result: dict) -> CalcWhyResponse:
    return CalcWhyResponse(
        type=result["type"],
        headline=result["headline"],
        line_uci=result.get("line_uci") or [],
        eval_before=result.get("eval_before"),
        eval_after=result.get("eval_after"),
        truncated_at_ply=result.get("truncated_at_ply"),
        params=result.get("params") or {},
    )


def _load_solve_attempt(cur, attempt_id: str, clerk_id: str) -> dict | None:
    cur.execute(
        "SELECT a.id, a.user_id, a.segment_id, a.offset_solver_moves,"
        " a.n, a.submitted_line, a.first_divergence_ply, a.diverged_on,"
        " a.solved, a.mode, s.segment_fen, s.line_uci, s.solver_move_count"
        " FROM calc_attempts a JOIN calc_segments s"
        " ON s.id = a.segment_id WHERE a.id = %s",
        (attempt_id,),
    )
    row = cur.fetchone()
    if row is None or row["user_id"] != clerk_id:
        return None
    return {
        "key": ("solve", attempt_id),
        "segment_fen": row["segment_fen"],
        "line_uci": row["line_uci"],
        "offset": row["offset_solver_moves"],
        "n": row["n"],
        "submitted_line": row["submitted_line"],
        "ply": row["first_divergence_ply"],
        "diverged_on": row["diverged_on"],
        "solved": row["solved"],
        "mode": row.get("mode") or "lines",
    }


def _load_review_attempt(cur, attempt_id: str, clerk_id: str) -> dict | None:
    cur.execute(
        "SELECT r.id, r.user_id, r.entry_id, r.solved,"
        " r.first_divergence_ply, r.diverged_on, r.submitted_line,"
        " r.mode, e.segment_id,"
        " e.offset_solver_moves, e.n, s.segment_fen,"
        " s.line_uci, s.solver_move_count"
        " FROM calc_woodpecker_attempts r"
        " JOIN calc_woodpecker_entries e ON e.id = r.entry_id"
        " JOIN calc_segments s ON s.id = e.segment_id"
        " WHERE r.id = %s",
        (attempt_id,),
    )
    row = cur.fetchone()
    if row is None or row["user_id"] != clerk_id:
        return None
    # W comes from the review attempt's OWN submitted line at its own
    # divergence ply (same exercise shape, 2n-1 plies).
    return {
        "key": ("review", attempt_id),
        "segment_fen": row["segment_fen"],
        "line_uci": row["line_uci"],
        "offset": row["offset_solver_moves"],
        "n": row["n"],
        "submitted_line": row["submitted_line"],
        "ply": row["first_divergence_ply"],
        "diverged_on": row["diverged_on"],
        "solved": row["solved"],
        "mode": row.get("mode") or "lines",
    }


@router.post("/why", response_model=CalcWhyResponse)
def explain_attempt(
    request: Request,
    body: CalcWhyRequest,
    _: None = Depends(limit_by_clerk_user_id(limit=60, window=60)),
    conn=Depends(get_db),
):
    clerk_id = _require_clerk_id(request)
    kind = body.kind
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        loader = _load_solve_attempt if kind == "solve" else _load_review_attempt
        row = loader(cur, str(body.attempt_id), clerk_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Attempt not found")
        if row["solved"]:
            raise HTTPException(status_code=409,
                                detail="Attempt was solved: nothing to explain")
        ply = row["ply"]
        if row["diverged_on"] == "defender":
            # No engine call: the stored reply is the verified best defense.
            expected = exercise_line_tokens(row["line_uci"], row["offset"],
                                            row["n"])
            stored_best = expected[ply]
            return _to_response({
                "type": "defender_misjudged",
                "headline": f"The stored defense {stored_best} holds here.",
                "line_uci": expected[ply:],
                "eval_before": None, "eval_after": None,
                "truncated_at_ply": None,
                "params": {"stored_best_reply": stored_best},
            })
        if row["diverged_on"] != "solver" or ply is None:
            raise HTTPException(status_code=409,
                                detail="Attempt has no solver divergence")
        if row["mode"] != "lines":
            # No premove submission path exists yet; the mapping is
            # implemented (derive_inputs) but unreachable until one does.
            raise HTTPException(status_code=409,
                                detail="Explanation only supports lines mode")
        if row["submitted_line"] is None:
            # Legacy review row (predates stored lines): nothing to derive
            # W from.
            raise HTTPException(status_code=409,
                                detail="Review has no stored line")
        # Idempotent: stored result wins, no new search.
        kind_col = ("attempt_id" if kind == "solve" else "review_attempt_id")
        cur.execute(f"SELECT result FROM calc_why WHERE {kind_col} = %s",
                    (str(body.attempt_id),))
        stored = cur.fetchone()
        if stored is not None:
            return _to_response(stored["result"])
        try:
            derived = derive_inputs(
                row["segment_fen"], row["line_uci"], row["offset"], row["n"],
                row["submitted_line"], ply, mode=row["mode"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        expected = exercise_line_tokens(row["line_uci"], row["offset"],
                                        row["n"])
        solution = expected[ply:]
        solution_uci = " ".join(solution)
        key = engine.position_key(derived["fen_before"])
        # Cross-user cache, bypassed when the stored engine version or node
        # budget differs from the current ones. The solution continuation is
        # part of the key: miss results replay it, and transposing segments
        # can share a position without sharing a line.
        try:
            current_version = engine.current_engine_version()
        except (RuntimeError, TimeoutError) as exc:
            raise HTTPException(status_code=503,
                                detail="Explanation engine unavailable")
        cur.execute("SELECT result, engine_version, nodes FROM calc_why_cache"
                    " WHERE position_key = %s AND w_uci = %s"
                    " AND solution_uci = %s",
                    (key, derived["w_uci"], solution_uci))
        cached = cur.fetchone()
        if (cached is not None
                and cached["engine_version"] == current_version
                and cached["nodes"] == engine.WHY_NODES):
            if kind == "solve":
                cur.execute("INSERT INTO calc_why (attempt_id, result,"
                            " engine_version, nodes) VALUES (%s,%s,%s,%s)"
                            " ON CONFLICT (attempt_id) WHERE attempt_id IS NOT NULL DO NOTHING",
                            (str(body.attempt_id),
                             json.dumps(cached["result"]),
                             cached["engine_version"], cached["nodes"]))
            else:
                cur.execute("INSERT INTO calc_why (review_attempt_id,"
                            " result, engine_version, nodes)"
                            " VALUES (%s,%s,%s,%s)"
                            " ON CONFLICT (review_attempt_id) WHERE review_attempt_id IS NOT NULL DO NOTHING",
                            (str(body.attempt_id),
                             json.dumps(cached["result"]),
                             cached["engine_version"], cached["nodes"]))
            conn.commit()
            return _to_response(cached["result"])
        if not engine.try_acquire_search():
            raise HTTPException(status_code=429,
                                detail="Explanation engine is busy")
        try:
            baseline = engine.analyse(derived["fen_before"],
                                      engine.WHY_NODES, 1)[0]
            reply = engine.analyse(derived["fen_after"],
                                    engine.WHY_NODES, 1)[0]
            version = engine.current_engine_version()
            result = classify(derived["fen_before"], derived["fen_after"],
                              derived["w_uci"], baseline, reply,
                              engine.WHY_NODES, solution)
            result["params"] = {**result.get("params", {}),
                                "fen_before": derived["fen_before"]}
            cur.execute("INSERT INTO calc_why_cache (position_key, w_uci,"
                        " solution_uci, result, engine_version, nodes)"
                        " VALUES (%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT (position_key, w_uci, solution_uci)"
                        " DO UPDATE SET result = EXCLUDED.result,"
                        " engine_version = EXCLUDED.engine_version,"
                        " nodes = EXCLUDED.nodes",
                        (key, derived["w_uci"], solution_uci,
                         json.dumps(result), version, engine.WHY_NODES))
            if kind == "solve":
                cur.execute("INSERT INTO calc_why (attempt_id, result,"
                            " engine_version, nodes) VALUES (%s,%s,%s,%s)"
                            " ON CONFLICT (attempt_id) WHERE attempt_id IS NOT NULL DO NOTHING",
                            (str(body.attempt_id), json.dumps(result),
                             version, engine.WHY_NODES))
            else:
                cur.execute("INSERT INTO calc_why (review_attempt_id,"
                            " result, engine_version, nodes)"
                            " VALUES (%s,%s,%s,%s)"
                            " ON CONFLICT (review_attempt_id) WHERE review_attempt_id IS NOT NULL DO NOTHING",
                            (str(body.attempt_id), json.dumps(result),
                             version, engine.WHY_NODES))
            conn.commit()
            return _to_response(result)
        finally:
            engine.release_search()
    finally:
        cur.close()
