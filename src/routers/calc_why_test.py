"""HTTP tests for POST /api/calc/why (routers/calc_why.py).

Each engine type has one hand-verified fixture (SF19, 500k nodes):
  material_loss   Bxf7+?? hangs the bishop (drop -3, truncate t=1)
  mate_against    g4?? allows Qh4# (mate in 1)
  stalemate_or_draw  Qb8+?? Kxb8 leaves bare kings (insufficient, t=1)
  slower_win      Qe8+ keeps mate but 1 -> 2
  also_fine       Nc3 keeps the win (drop < 0.05, replays the solution)
  miss/defense    h4?? unpunished and not winning, solution replayed
Pure classify() checks cover miss mate/material/keep/defense/also_fine
and punishment-over-miss ordering.
plus: defender_misjudged with zero engine calls, foreign attempt 404,
solved attempt 409, repeat returns stored output without a new search,
cache reuse across users, rate limit, determinism.

Run with: cd src && ../venv/bin/python routers/calc_why_test.py
Requires: DATABASE_URL / INTERNAL_SECRET, SF19 binary resolvable, calc
tables migrated (segments fixture rows are inserted by this file).
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi.testclient import TestClient

import main as app_module
from core import database
from core.migrations import run_migrations
from services import calc_why_engine as engine

USER_A = "calc-why-test"
USER_B = "calc-why-test-two"


def _q(sql, params=()):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        database.connection_pool.putconn(conn)


def _exec(sql, params=()):
    conn = database.connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        database.connection_pool.putconn(conn)


def _seq_fen(moves):
    b = chess.Board()
    for u in moves:
        b.push(chess.Move.from_uci(u))
    return b.fen()


# (key, segment_fen, stored_line, submitted, ply, diverged_on, solved)
FIXTURES = [
    ("material",
     "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
     ["c4b5", "c6d4", "b5a4"], ["c4f7"], 0, "solver", False),
    ("mate",
     _seq_fen(["f2f3", "e7e5"]),
     ["g2g3", "e5e4", "f1g2"], ["g2g4"], 0, "solver", False),
    ("stale",
     "k7/8/1K1Q4/8/8/8/8/8 w - - 0 1",
     ["d6e6", "a8b8", "e6e8"], ["d6b8"], 0, "solver", False),
    ("slower",
     "7k/8/5KQ1/8/8/8/8/8 w - - 0 1",
     ["g6g7"], ["g6e8"], 0, "solver", False),
    ("fine",
     _seq_fen(["e2e4", "e7e5"]),
     ["b1c3", "b8c6", "c3d5"], ["g1f3"], 0, "solver", False),
    ("slips",
     _seq_fen(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3",
               "f8c5"]),
     ["c2c3", "d7d6", "d1e2"], ["h2h4"], 0, "solver", False),
    ("defender",
     _seq_fen(["e2e4", "e7e5"]),
     ["g1f3", "b8c6", "f3e5"], ["g1f3", "b8a6", "f3e5"], 1, "defender",
     False),
    ("solved",
     _seq_fen(["e2e4", "e7e5"]),
     ["g1f3", "b8c6", "f3e5"], ["g1f3", "b8c6", "f3e5"], 0, "solver",
     True),
]


def _check_line_legal(fen, toks):
    b = chess.Board(fen)
    for u in toks:
        mv = chess.Move.from_uci(u)
        assert mv in b.legal_moves, f"{u} illegal in {b.fen()}"
        b.push(mv)


def setup():
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set (root .env)")
    if not os.environ.get("INTERNAL_SECRET"):
        raise SystemExit("INTERNAL_SECRET is not set (root .env)")
    database.init_db()
    run_migrations()
    for user, email in ((USER_A, "calc-why-test@example.invalid"),
                        (USER_B, "calc-why-test-two@example.invalid")):
        _exec("DELETE FROM calc_why WHERE attempt_id IN (SELECT id FROM"
              " calc_attempts WHERE user_id = %s)", (user,))
        _exec("DELETE FROM calc_attempts WHERE user_id = %s", (user,))
        _exec("DELETE FROM calc_ratings WHERE user_id = %s", (user,))
        _exec("DELETE FROM users WHERE clerk_id = %s OR email = %s",
              (user, email))
        _exec("INSERT INTO users (clerk_id, email) VALUES (%s, %s)",
              (user, email))
    # Self-cleaning (after attempts are gone: FK): a previously aborted run
    # may have left fixture segments/cache rows (teardown never ran).
    _exec("DELETE FROM calc_why_cache WHERE w_uci IN ('c4f7', 'g2g4',"
          " 'd6b8', 'g6e8', 'g1f3', 'h2h4', 'b8a6')")
    _exec("DELETE FROM calc_segments WHERE source_puzzle_id LIKE %s",
          ("why-fixture-%",))
    seg_ids, attempt_ids = {}, {}
    for key, fen, line, submitted, ply, diverged, solved in FIXTURES:
        _check_line_legal(fen, line)
        sid = str(uuid.uuid4())
        aid = str(uuid.uuid4())
        n = (len(submitted) + 1) // 2
        _exec("INSERT INTO calc_segments (id, source_puzzle_id, segment_fen,"
              " line_uci, solver_move_count, start_solver_index, band,"
              " family, rating, trivially_forced, engine_verified_replies,"
              " verified_level, status, engine_version)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (sid, f"why-fixture-{key}", fen, " ".join(line), 2, 0,
               "test", "test", 1500, False, 1, 1, "active", "test"))
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (aid, USER_A, sid, 0, n, " ".join(submitted),
               ply if not solved else None, diverged, solved, 100,
               f"why-nonce-{key}", False))
        seg_ids[key] = sid
        attempt_ids[key] = aid
    return (TestClient(app_module.app),
            {"X-Clerk-User-Id": USER_A,
             "X-Internal-Secret": os.environ["INTERNAL_SECRET"]},
            seg_ids, attempt_ids)


def teardown():
    for user in (USER_A, USER_B):
        _exec("DELETE FROM calc_why WHERE attempt_id IN (SELECT id FROM"
              " calc_attempts WHERE user_id = %s)", (user,))
        _exec("DELETE FROM calc_attempts WHERE user_id = %s", (user,))
        _exec("DELETE FROM calc_ratings WHERE user_id = %s", (user,))
        _exec("DELETE FROM users WHERE clerk_id = %s", (user,))
    _exec("DELETE FROM calc_why_cache WHERE w_uci IN ('c4f7', 'g2g4',"
              " 'd6b8', 'g6e8', 'g1f3', 'h2h4')")
    _exec("DELETE FROM calc_segments WHERE source_puzzle_id LIKE %s",
          ("why-fixture-%",))


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + f" {name}"
          + (f" — {detail}" if detail and not cond else ""))


def main():
    client, headers, segs, attempts = setup()
    hb = {"X-Clerk-User-Id": USER_B,
          "X-Internal-Secret": os.environ["INTERNAL_SECRET"]}
    try:
        def post(attempt_id, h=headers):
            return client.post("/api/calc/why", headers=h,
                               json={"attempt_id": attempt_id})

        # --- one fixture per engine type --------------------------------
        # (want_type, want_truncated, want_line, want_reason)
        expectations = {
            "material": ("material_loss", 1, ["c4f7", "e8f7"], None),
            "mate": ("mate_against", 1, ["g2g4", "d8h4"], None),
            "stale": ("stalemate_or_draw", 1, ["d6b8", "a8b8"], None),
            "slower": ("slower_win", 4, ["g6e8", "h8h7", "e8d7", "h7g8",
                                         "d7g7"], None),
            "fine": ("also_fine", 0, ["b1c3"], None),
            "slips": ("miss", 0, ["c2c3"], "defense"),
        }
        for key, (want_type, want_trunk, want_line,
                  want_reason) in expectations.items():
            before_calls = engine.search_calls
            r = post(attempts[key])
            body = r.json()
            ok = (r.status_code == 200 and body["type"] == want_type
                  and body["truncated_at_ply"] == want_trunk
                  and body["headline"] and isinstance(body["headline"], str)
                  and engine.search_calls == before_calls + 2)
            if want_line is not None:
                ok = ok and body["line_uci"] == want_line
            if want_reason is not None:
                ok = ok and body["params"].get("reason") == want_reason
            check(f"why {key} -> {want_type}", ok,
                  (r.status_code, body, engine.search_calls - before_calls))

        # --- defender: no engine -----------------------------------------
        calls = engine.search_calls
        r = post(attempts["defender"])
        body = r.json()
        check("defender_misjudged, zero engine calls",
              r.status_code == 200 and body["type"] == "defender_misjudged"
              and body["line_uci"] == ["b8c6", "f3e5"]
              and body["eval_before"] is None
              and engine.search_calls == calls, (body, calls))

        # --- ownership / state --------------------------------------------
        r = post(attempts["material"], hb)
        check("foreign attempt -> 404", r.status_code == 404, r.status_code)
        r = post(attempts["solved"])
        check("solved attempt -> 409", r.status_code == 409, r.status_code)
        r = client.post("/api/calc/why", headers=headers,
                        json={"attempt_id": str(uuid.uuid4())})
        check("unknown attempt -> 404", r.status_code == 404, r.status_code)

        # --- idempotent repeat: stored, no new search ----------------------
        calls = engine.search_calls
        r1 = post(attempts["mate"])
        r2 = post(attempts["mate"])
        check("repeat returns stored result, no new search",
              r1.json() == r2.json() and engine.search_calls == calls,
              (r1.json(), r2.json()))

        # --- cache reuse across users ---------------------------------------
        _exec("INSERT INTO users (clerk_id, email) VALUES (%s, %s)"
              " ON CONFLICT DO NOTHING", (USER_B, "calc-why-test-two"
                                                 "@example.invalid"))
        aid_b = str(uuid.uuid4())
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (aid_b, USER_B, segs["mate"], 0, 1, "g2g4", 0, "solver",
               False, 100, "why-nonce-mate-b", False))
        calls = engine.search_calls
        rb = post(aid_b, hb)
        check("cache reuse across users, no new search",
              rb.status_code == 200 and rb.json()["type"] == "mate_against"
              and engine.search_calls == calls
              and _q("SELECT COUNT(*) FROM calc_why WHERE attempt_id = %s",
                     (aid_b,)) == [(1,)], (rb.status_code, rb.text[:200]))

        # --- determinism ------------------------------------------------------
        r1 = post(attempts["slower"])
        _exec("DELETE FROM calc_why WHERE attempt_id = %s",
              (attempts["slower"],))
        from services.calc_why_engine import position_key
        _exec("DELETE FROM calc_why_cache WHERE position_key = %s",
              (position_key(r1.json()["params"]["fen_before"]),))
        calls = engine.search_calls
        r2 = post(attempts["slower"])
        check("determinism: re-search identical",
              r2.json() == r1.json()
              and engine.search_calls == calls + 2, r2.json())

        # --- A: per-mode W mapping -------------------------------------------
        from services.calc_why import derive_inputs
        seg_fen = _seq_fen(["e2e4", "e7e5"])
        got = derive_inputs(seg_fen, "g1f3 b8c6 f3e5", 0, 2,
                            "g2g4 f3e5", 2, mode="premove")
        check("A premove maps k-th solver move",
              got["w_uci"] == "f3e5", got)
        try:
            derive_inputs(seg_fen, "g1f3 b8c6 f3e5", 0, 2, "g2g4 f3e5", 1,
                          mode="premove")
            check("A premove odd ply rejected", False)
        except ValueError:
            check("A premove odd ply rejected", True)
        try:
            derive_inputs(seg_fen, "g1f3 b8c6 f3e5", 0, 2, "g2g4 f3e5", 0,
                          mode="telepathy")
            check("A unknown mode rejected", False)
        except ValueError:
            check("A unknown mode rejected", True)
        aid_mode = str(uuid.uuid4())
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated, mode)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (aid_mode, USER_A, segs["mate"], 0, 1, "g2g4", 0, "solver",
               False, 100, "why-nonce-mode", False, "premove"))
        r = post(aid_mode)
        check("A non-lines attempt -> 409", r.status_code == 409,
              r.status_code)

        # --- C1: review attempts ----------------------------------------------
        from services.calc_why_engine import position_key as pkey
        mat_seg = segs["material"]
        src_aid = str(uuid.uuid4())
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (src_aid, USER_A, mat_seg, 0, 1, "c4f7", 0, "solver",
               False, 100, "why-nonce-src", False))
        entry_id = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_entries (id, user_id, segment_id,"
              " offset_solver_moves, n, source_attempt_id)"
              " VALUES (%s,%s,%s,%s,%s,%s)",
              (entry_id, USER_A, mat_seg, 0, 1, src_aid))
        rev_aid = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_attempts (id, entry_id, user_id,"
              " solved, first_divergence_ply, diverged_on, score,"
              " elapsed_ms, review_nonce, submitted_line, mode)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (rev_aid, entry_id, USER_A, False, 0, "solver", 0.0, 100,
               "why-rev-nonce-1", "c4f7", "lines"))
        calls = engine.search_calls
        rr = client.post("/api/calc/why", headers=headers,
                         json={"attempt_id": rev_aid, "kind": "review"})
        check("C1 review failure works via cache, no new search",
              rr.status_code == 200 and rr.json()["type"] == "material_loss"
              and engine.search_calls == calls
              and _q("SELECT COUNT(*) FROM calc_why"
                     " WHERE review_attempt_id = %s",
                     (rev_aid,)) == [(1,)], (rr.status_code,
                                             rr.text[:200]))
        rev_solved = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_attempts (id, entry_id, user_id,"
              " solved, first_divergence_ply, diverged_on, score,"
              " elapsed_ms, review_nonce)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (rev_solved, entry_id, USER_A, True, None, None, 1.0, 100,
               "why-rev-nonce-2"))
        r = client.post("/api/calc/why", headers=headers,
                        json={"attempt_id": rev_solved, "kind": "review"})
        check("C1 solved review -> 409", r.status_code == 409, r.status_code)
        r = client.post("/api/calc/why", headers=hb,
                        json={"attempt_id": rev_aid, "kind": "review"})
        check("C1 foreign review -> 404", r.status_code == 404, r.status_code)

        # --- C1b: review W comes from the review's own line ------------------
        # Original fails at ply 4; the review fails at ply 0 with a
        # different move: why must analyze the review's W at the exercise
        # start. Same segment/W as the 'mate' solve fixture, but a wider
        # exercise window (n=2): the solution continuation differs, so the
        # cache key differs and this is a fresh search.
        segx = segs["mate"]
        srcx = str(uuid.uuid4())
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (srcx, USER_A, segx, 0, 2, "g2g3 d8e7 g3g4", 4, "solver",
               False, 100, "why-nonce-srcx", False))
        entryx = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_entries (id, user_id, segment_id,"
              " offset_solver_moves, n, source_attempt_id)"
              " VALUES (%s,%s,%s,%s,%s,%s)",
              (entryx, USER_A, segx, 0, 2, srcx))
        revx = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_attempts (id, entry_id, user_id,"
              " solved, first_divergence_ply, diverged_on, score,"
              " elapsed_ms, review_nonce, submitted_line, mode)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (revx, entryx, USER_A, False, 0, "solver", 0.0, 100,
               "why-rev-nonce-x", "g2g4 f7f5 g4f5", "lines"))
        calls = engine.search_calls
        rx = client.post("/api/calc/why", headers=headers,
                         json={"attempt_id": revx, "kind": "review"})
        bx = rx.json()
        check("C1b review analyzes its own W at its own ply",
              rx.status_code == 200 and bx["type"] == "mate_against"
              and bx["line_uci"][0] == "g2g4"
              and bx["params"].get("reason") is None
              and engine.search_calls == calls + 2, (rx.status_code, bx))
        # Legacy review row with no stored line -> 409.
        rev_legacy = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_attempts (id, entry_id, user_id,"
              " solved, first_divergence_ply, diverged_on, score,"
              " elapsed_ms, review_nonce, submitted_line, mode)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (rev_legacy, entry_id, USER_A, False, 0, "solver", 0.0, 100,
               "why-rev-nonce-legacy", None, "lines"))
        r = client.post("/api/calc/why", headers=headers,
                        json={"attempt_id": rev_legacy, "kind": "review"})
        check("C1b legacy review without line -> 409",
              r.status_code == 409, r.status_code)

        # --- BE2: why-table constraints ----------------------------------------
        _exec("DELETE FROM calc_why WHERE review_attempt_id = %s", (revx,))
        r = client.post("/api/calc/why", headers=headers,
                        json={"attempt_id": revx, "kind": "review"})
        check("BE2 second review kind stored under its own key",
              r.status_code == 200 and _q(
                  "SELECT COUNT(*) FROM calc_why"
                  " WHERE review_attempt_id = %s", (revx,)) == [(1,)]
              and _q("SELECT COUNT(*) FROM calc_why"
                     " WHERE attempt_id IS NOT NULL"
                     " AND review_attempt_id IS NOT NULL") == [(0,)], r.status_code)
        try:
            _exec("INSERT INTO calc_why (attempt_id, review_attempt_id,"
                  " result, nodes) VALUES (%s,%s,%s,%s)",
                  (attempts["mate"], revx, "{}", 1))
            check("BE2 CHECK rejects both-set", False)
        except Exception:
            check("BE2 CHECK rejects both-set", True)
            con = database.connection_pool.getconn()
            try:
                con.rollback()
            finally:
                database.connection_pool.putconn(con)
        try:
            _exec("INSERT INTO calc_why (result, nodes) VALUES (%s,%s)",
                  ("{}", 1))
            check("BE2 CHECK rejects both-null", False)
        except Exception:
            check("BE2 CHECK rejects both-null", True)
            con = database.connection_pool.getconn()
            try:
                con.rollback()
            finally:
                database.connection_pool.putconn(con)
        # Cascade both kinds on user delete (USER_B owns one of each: the
        # solve attempt hits the shared cache, so no search is needed).
        aid_b = str(uuid.uuid4())
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (aid_b, USER_B, segs["mate"], 0, 1, "g2g4", 0, "solver",
               False, 100, "why-nonce-cascade", False))
        rb = client.post("/api/calc/why", headers=hb,
                         json={"attempt_id": aid_b, "kind": "solve"})
        entry_b = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_entries (id, user_id, segment_id,"
              " offset_solver_moves, n, source_attempt_id)"
              " VALUES (%s,%s,%s,%s,%s,%s)",
              (entry_b, USER_B, segs["mate"], 0, 1, aid_b))
        rev_b = str(uuid.uuid4())
        _exec("INSERT INTO calc_woodpecker_attempts (id, entry_id, user_id,"
              " solved, first_divergence_ply, diverged_on, score,"
              " elapsed_ms, review_nonce, submitted_line, mode)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (rev_b, entry_b, USER_B, False, 0, "solver", 0.0, 100,
               "why-rev-nonce-cascade", "g2g4", "lines"))
        rbb = client.post("/api/calc/why", headers=hb,
                          json={"attempt_id": rev_b, "kind": "review"})
        check("BE2 cascade fixtures ready",
              rb.status_code == 200 and rbb.status_code == 200,
              (rb.status_code, rbb.status_code))
        _exec("DELETE FROM users WHERE clerk_id = %s", (USER_B,))
        check("BE2 user delete cascades both why kinds",
              _q("SELECT COUNT(*) FROM calc_why WHERE attempt_id = %s",
                 (aid_b,)) == [(0,)]
              and _q("SELECT COUNT(*) FROM calc_why WHERE review_attempt_id"
                     " = %s", (rev_b,)) == [(0,)]
              and _q("SELECT COUNT(*) FROM calc_attempts WHERE user_id = %s",
                     (USER_B,)) == [(0,)])
        _exec("INSERT INTO users (clerk_id, email) VALUES (%s, %s)",
              (USER_B, "calc-why-test-two@example.invalid"))
        mat_fen = ("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R"
                   " w KQkq - 9 12")
        sid2 = str(uuid.uuid4())
        _exec("INSERT INTO calc_segments (id, source_puzzle_id, segment_fen,"
              " line_uci, solver_move_count, start_solver_index, band,"
              " family, rating, trivially_forced, engine_verified_replies,"
              " verified_level, status, engine_version)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (sid2, "why-fixture-transpose", mat_fen, "c4b5 c6d4 b5a4", 2,
               0, "test", "test", 1500, False, 1, 1, "active", "test"))
        aid2 = str(uuid.uuid4())
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (aid2, USER_A, sid2, 0, 1, "c4f7", 0, "solver", False, 100,
               "why-nonce-transpose", False))
        calls = engine.search_calls
        rt = post(aid2)
        check("C2 transposition hits normalized key, no new search",
              rt.status_code == 200 and rt.json()["type"] == "material_loss"
              and engine.search_calls == calls, (rt.status_code,
                                                rt.text[:200]))

        # --- C2: version bump bypass ----------------------------------------------
        _exec("UPDATE calc_why_cache SET engine_version = 'ancient'"
              " WHERE position_key = %s",
              (pkey(mat_fen),))
        calls = engine.search_calls
        aid3 = str(uuid.uuid4())
        _exec("INSERT INTO calc_attempts (id, user_id, segment_id,"
              " offset_solver_moves, n, submitted_line,"
              " first_divergence_ply, diverged_on, solved, time_taken_ms,"
              " exercise_nonce, rated)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (aid3, USER_A, sid2, 0, 1, "c4f7", 0, "solver", False, 100,
               "why-nonce-bump", False))
        rb = post(aid3)
        check("C2 version bump re-searches and refreshes cache",
              rb.status_code == 200 and rb.json()["type"] == "material_loss"
              and engine.search_calls == calls + 2
              and _q("SELECT engine_version FROM calc_why_cache"
                     " WHERE position_key = %s",
                     (pkey(mat_fen),))[0][0] != "ancient",
              (rb.status_code, engine.search_calls - calls))

        # --- D: miss classification (pure, synthetic evals) -----------------
        from services.calc_why import classify as classify_pure

        def _ev(cp=None, mate=None, wp=None):
            return {"cp": cp, "mate": mate, "win_prob": wp}

        kq = "7k/8/5KQ1/8/8/8/8/8 w - - 0 1"
        rook_fen = "r7/2N1k3/8/8/8/8/8/4KB2 w - - 0 1"

        got = classify_pure(kq, kq, "g6g5", _ev(mate=1, wp=1.0),
                            _ev(cp=-900, wp=0.0), 1, ["g6g7"])
        check("D miss mate via baseline score",
              got["type"] == "miss" and got["params"]["reason"] == "mate"
              and got["headline"] == "You missed mate in 1."
              and got["line_uci"] == ["g6g7"]
              and got["truncated_at_ply"] == 0, got)

        got = classify_pure(rook_fen, rook_fen, "f1e2", _ev(cp=600, wp=0.95),
                            _ev(cp=-80, wp=0.45), 1, ["c7a8"])
        check("D miss material names the rook",
              got["type"] == "miss"
              and got["params"]["reason"] == "material"
              and "win a rook" in got["headline"]
              and got["params"]["material_gain"] == 5
              and got["line_uci"] == ["c7a8"], got)

        got = classify_pure(rook_fen, rook_fen, "f1e2", _ev(cp=600, wp=0.95),
                            _ev(cp=80, wp=0.6), 1, ["e1e2"])
        check("D miss keep-the-win from the eval",
              got["type"] == "miss" and got["params"]["reason"] == "keep"
              and got["line_uci"] == ["e1e2"], got)

        got = classify_pure(rook_fen, rook_fen, "f1e2", _ev(cp=-100, wp=0.4),
                            _ev(cp=-500, wp=0.9), 1, ["e1e2"])
        check("D miss best-defense from the eval",
              got["type"] == "miss" and got["params"]["reason"] == "defense",
              got)

        got = classify_pure(rook_fen, rook_fen, "f1e2", _ev(cp=40, wp=0.6),
                            _ev(cp=-30, wp=0.42), 1, ["e1e2"])
        check("D also_fine still replays the solution",
              got["type"] == "also_fine" and got["line_uci"] == ["e1e2"]
              and got["truncated_at_ply"] == 0, got)

        mate_fen = _seq_fen(["f2f3", "e7e5"])
        mate_after = _seq_fen(["f2f3", "e7e5", "g2g4"])
        got = classify_pure(mate_fen, mate_after, "g2g4", _ev(cp=0, wp=0.5),
                            {"cp": None, "mate": 1, "win_prob": 1.0,
                             "pv": ["d8h4"]}, 1, ["g2g3"])
        check("D punishment beats miss",
              got["type"] == "mate_against", got)

        # --- rate limit (LAST: it consumes the per-minute budget) -----------
        # 65 rapid requests trips the 60/min budget regardless of how many
        # earlier in-window requests exist (or have aged out).
        codes = set()
        for _ in range(65):
            rr = client.post("/api/calc/why", headers=headers,
                             json={"attempt_id": str(uuid.uuid4()),
                                   "kind": "solve"})
            codes.add(rr.status_code)
        check("rate limit trips", 429 in codes, sorted(codes))
    finally:
        teardown()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
