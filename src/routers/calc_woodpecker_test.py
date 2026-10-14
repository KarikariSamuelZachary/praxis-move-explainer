"""HTTP tests for the calc Woodpecker queue (routers/calc_woodpecker.py).

Verified:
  W1. A failed /submit creates exactly one entry (server-side, with FSRS
      defaults); a repeat failure on the same exercise does not duplicate
      it; a solved submit creates none.
  W2. Queue and count agree; neither leaks any line move.
  W3. Token kinds are rejected on the wrong endpoint both ways.
  W4. Review solve advances FSRS state (reps, stability, future due);
      review fail records Again without graduating; replay -> 409 with
      no reveal.
  W5. Reviews never touch calc_ratings or calc_attempts.
  W6. Deleting the user cascades entries and review attempts.

Run with: cd src && ../venv/bin/python routers/calc_woodpecker_test.py
Requires: DATABASE_URL / INTERNAL_SECRET from root .env, calc tables
migrated + loaded.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi.testclient import TestClient

import main as app_module
from core import database
from core.migrations import run_migrations
from routers.calc_test import _mint, _other_uci

TEST_CLERK_ID = "calc-wp-http-test"
TEST_EMAIL = "calc-wp-http-test@example.invalid"


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


def setup():
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is not set (root .env)")
    if not os.environ.get("INTERNAL_SECRET"):
        raise SystemExit("INTERNAL_SECRET is not set (root .env)")
    database.init_db()
    run_migrations()
    _exec("DELETE FROM calc_woodpecker_attempts WHERE user_id = %s",
          (TEST_CLERK_ID,))
    _exec("DELETE FROM calc_woodpecker_entries WHERE user_id = %s",
          (TEST_CLERK_ID,))
    _exec("DELETE FROM calc_attempts WHERE user_id = %s", (TEST_CLERK_ID,))
    _exec("DELETE FROM calc_ratings WHERE user_id = %s", (TEST_CLERK_ID,))
    _exec("DELETE FROM users WHERE clerk_id = %s OR email = %s",
          (TEST_CLERK_ID, TEST_EMAIL))
    _exec("INSERT INTO users (clerk_id, email) VALUES (%s, %s)",
          (TEST_CLERK_ID, TEST_EMAIL))
    return (TestClient(app_module.app),
            {"X-Clerk-User-Id": TEST_CLERK_ID,
             "X-Internal-Secret": os.environ["INTERNAL_SECRET"]})


def teardown():
    _exec("DELETE FROM calc_woodpecker_attempts WHERE user_id = %s",
          (TEST_CLERK_ID,))
    _exec("DELETE FROM calc_woodpecker_entries WHERE user_id = %s",
          (TEST_CLERK_ID,))
    _exec("DELETE FROM calc_attempts WHERE user_id = %s", (TEST_CLERK_ID,))
    _exec("DELETE FROM calc_ratings WHERE user_id = %s", (TEST_CLERK_ID,))
    _exec("DELETE FROM users WHERE clerk_id = %s", (TEST_CLERK_ID,))


def _segment(seg_id):
    rows = _q("SELECT id, segment_fen, line_uci, solver_move_count, rating"
              " FROM calc_segments WHERE id = %s", (seg_id,))
    assert rows
    sid, fen, line, n, rating = rows[0]
    return {"id": str(sid), "fen": fen, "toks": line.split(), "n": n,
            "rating": rating}


def _fresh_segment():
    rows = _q("SELECT id FROM calc_segments WHERE status = 'active'"
              " AND trivially_forced = false AND solver_move_count >= 3"
              " ORDER BY rating DESC LIMIT 200")
    attempted = {r[0] for r in _q(
        "SELECT DISTINCT segment_id FROM calc_attempts WHERE user_id = %s",
        (TEST_CLERK_ID,))}
    for (sid,) in rows:
        if str(sid) not in {str(a) for a in attempted}:
            return _segment(str(sid))
    raise AssertionError("no fresh segment")


def _expected(seg, offset, n):
    return seg["toks"][2 * offset:2 * offset + 2 * n - 1]


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + f" {name}"
          + (f" — {detail}" if detail and not cond else ""))


def main():
    client, headers = setup()
    try:
        seg = _fresh_segment()
        exp = _expected(seg, 0, 3)

        # --- W1: capture ------------------------------------------------------
        tok, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tok,
            "moves": [_other_uci(exp[0])] + exp[1:]})
        check("W1 fail recorded", r.status_code == 200
              and r.json()["solved"] is False, r.text[:200])
        entries = _q("SELECT segment_id, offset_solver_moves, n,"
                     " source_attempt_id, state, reps, lapses, is_mastered,"
                     " stability, difficulty, due, last_review"
                     " FROM calc_woodpecker_entries WHERE user_id = %s",
                     (TEST_CLERK_ID,))
        check("W1 exactly one entry", len(entries) == 1, entries)
        e = entries[0]
        check("W1 entry identity + FSRS defaults",
              str(e[0]) == seg["id"] and e[1] == 0 and e[2] == 3
              and e[3] is not None and e[4] == 1 and e[5] == 0 and e[6] == 0
              and e[7] is False and e[8] is None and e[9] is None
              and e[10] is not None and e[11] is None, e)
        tok2, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tok2,
            "moves": [_other_uci(exp[0])] + exp[1:]})
        check("W1 repeat failure no duplicate",
              r.status_code == 200 and _q(
                  "SELECT COUNT(*) FROM calc_woodpecker_entries"
                  " WHERE user_id = %s", (TEST_CLERK_ID,)) == [(1,)])
        seg2 = _fresh_segment()
        exp2 = _expected(seg2, 0, 3)
        tok3, _ = _mint(TEST_CLERK_ID, seg2["id"], 0, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tok3, "moves": exp2})
        check("W1 solved submit creates none",
              r.json()["solved"] is True and _q(
                  "SELECT COUNT(*) FROM calc_woodpecker_entries"
                  " WHERE user_id = %s", (TEST_CLERK_ID,)) == [(1,)])

        # --- W2: queue + count ---------------------------------------------------
        r = client.get("/api/calc/woodpecker/queue?limit=20", headers=headers)
        check("W2 queue serves", r.status_code == 200, r.text[:200])
        items = r.json()
        rc = client.get("/api/calc/woodpecker/count", headers=headers)
        check("W2 queue and count agree",
              rc.json()["due_count"] == len(items) == 1, (rc.text, items))
        it = items[0]
        check("W2 queue keys exact",
              set(it.keys()) == {"entry_id", "fen", "side_to_move", "n",
                                 "review_token"}, set(it.keys()))
        blob = json.dumps(items) + rc.text
        check("W2 no line leak",
              not any(t in blob for t in seg["toks"]), blob[:200])
        b = chess.Board(seg["fen"])
        check("W2 queue FEN replays from segment start",
              it["fen"] == b.fen() and it["n"] == 3, it["fen"])

        # --- W3: wrong-endpoint tokens ----------------------------------------------
        r = client.post("/api/calc/woodpecker/attempt", headers=headers,
                        json={"review_token": tok3, "moves": exp2})
        check("W3 solve token at review endpoint -> 400",
              r.status_code == 400, r.status_code)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": it["review_token"], "moves": exp})
        check("W3 review token at submit endpoint -> 400",
              r.status_code == 400, (r.status_code, r.text[:200]))

        # --- W4: review solve advances FSRS -------------------------------------------
        snap_ratings = _q("SELECT n, rating, attempts FROM calc_ratings"
                          " WHERE user_id = %s", (TEST_CLERK_ID,))
        snap_attempts = _q("SELECT COUNT(*) FROM calc_attempts"
                           " WHERE user_id = %s", (TEST_CLERK_ID,))
        r = client.post("/api/calc/woodpecker/attempt", headers=headers,
                        json={"review_token": it["review_token"],
                              "moves": exp})
        body = r.json()
        check("W4 review solve",
              r.status_code == 200 and body["solved"] is True
              and body["continuation"] == [] and body["rated"] is False,
              r.text[:300])
        st = _q("SELECT state, reps, lapses, stability, difficulty, due,"
                " last_review, is_mastered FROM calc_woodpecker_entries"
                " WHERE user_id = %s", (TEST_CLERK_ID,))[0]
        check("W4 FSRS advanced on Good",
              st[1] == 1 and st[3] is not None and st[4] is not None
              and st[5] is not None and st[6] is not None and st[5] > st[6]
              and st[7] is False, st)
        att = _q("SELECT solved, score, elapsed_ms, review_nonce"
                 " FROM calc_woodpecker_attempts WHERE user_id = %s",
                 (TEST_CLERK_ID,))
        check("W4 one review attempt row",
              len(att) == 1 and att[0][0] is True and att[0][1] == 1.0
              and att[0][3], att)
        # replay -> 409, no reveal
        r2 = client.post("/api/calc/woodpecker/attempt", headers=headers,
                         json={"review_token": it["review_token"],
                               "moves": exp})
        check("W4 replay -> 409, no reveal",
              r2.status_code == 409 and "solved" not in r2.json()
              and "continuation" not in r2.json(), (r2.status_code,
                                                   r2.text[:200]))
        check("W5 solve-review touches neither ratings nor attempts",
              _q("SELECT n, rating, attempts FROM calc_ratings"
                 " WHERE user_id = %s", (TEST_CLERK_ID,)) == snap_ratings
              and _q("SELECT COUNT(*) FROM calc_attempts WHERE user_id = %s",
                     (TEST_CLERK_ID,)) == snap_attempts)

        # --- W4b: review fail schedules Again --------------------------------------------
        seg3 = _fresh_segment()
        exp3 = _expected(seg3, 0, 3)
        tokf, _ = _mint(TEST_CLERK_ID, seg3["id"], 0, 3)
        client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokf,
            "moves": [_other_uci(exp3[0])] + exp3[1:]})
        items = client.get("/api/calc/woodpecker/queue?limit=20",
                           headers=headers).json()
        mine = [i for i in items
                if i["entry_id"] == _q(
                    "SELECT id FROM calc_woodpecker_entries WHERE user_id = %s"
                    " AND segment_id = %s", (TEST_CLERK_ID, seg3["id"]))[0][0]]
        check("W4b new card queued", len(mine) == 1, len(items))
        bad = [_other_uci(exp3[0])] + exp3[1:]
        snap_ratings_b = _q("SELECT n, rating, attempts FROM calc_ratings"
                            " WHERE user_id = %s", (TEST_CLERK_ID,))
        snap_attempts_b = _q("SELECT COUNT(*) FROM calc_attempts"
                             " WHERE user_id = %s", (TEST_CLERK_ID,))
        r = client.post("/api/calc/woodpecker/attempt", headers=headers,
                        json={"review_token": mine[0]["review_token"],
                              "moves": bad})
        b = r.json()
        check("W4b review fail reveals continuation only",
              r.status_code == 200 and b["solved"] is False
              and b["first_divergence_ply"] == 0
              and b["continuation"] == exp3, r.text[:300])
        st3 = _q("SELECT reps, lapses, last_review FROM"
                 " calc_woodpecker_entries WHERE user_id = %s AND segment_id"
                 " = %s", (TEST_CLERK_ID, seg3["id"]))[0]
        check("W4b Again recorded, no lapse from Learning",
              st3[0] == 1 and st3[1] == 0 and st3[2] is not None, st3)

        # --- W5: ratings/attempts untouched (fail-review half) ----------------
        # (The solve-review half was asserted right after W4's replay check,
        # before W4b's submit legitimately rated and moved the row.)
        check("W5 fail-review touches neither ratings nor attempts",
              _q("SELECT n, rating, attempts FROM calc_ratings"
                 " WHERE user_id = %s", (TEST_CLERK_ID,)) == snap_ratings_b
              and _q("SELECT COUNT(*) FROM calc_attempts WHERE user_id = %s",
                     (TEST_CLERK_ID,)) == snap_attempts_b)

        # --- W6: user delete cascades -------------------------------------------------------
        _exec("DELETE FROM users WHERE clerk_id = %s", (TEST_CLERK_ID,))
        check("W6 cascade on user delete",
              _q("SELECT COUNT(*) FROM calc_woodpecker_entries"
                 " WHERE user_id = %s", (TEST_CLERK_ID,)) == [(0,)]
              and _q("SELECT COUNT(*) FROM calc_woodpecker_attempts"
                     " WHERE user_id = %s", (TEST_CLERK_ID,)) == [(0,)])
    finally:
        teardown()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
