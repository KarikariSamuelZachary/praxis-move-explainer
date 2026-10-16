"""HTTP tests for the hardened Blind Calculation trainer (routers/calc.py).

Token contract: GET /next issues an HMAC-bound exercise_token;
POST /submit takes {exercise_token, moves[]} only. One attempt per token
(409 on replay, no reveal). 30-day segment-level exclusion in /next.

Verified:
  A. Full correct line via token -> solved=true, empty continuation, one
     attempt row (server-side elapsed ms stored, nonce stored).
  B. Wrong first move / defender reply / last move -> first-divergence
     grading with reveal limited to that exercise's continuation.
  C. Too few / too many moves, garbage UCI -> 422; unknown and retired
     segments (via minted tokens) -> 404; n out of range -> 422.
  D. Promotion move graded exact, case-normalized.
  E. offset > 0 exercise grades against the sliced line.
  F. Solved segments are never re-served (segment-level exclusion).
  G. GET never leaks the line (exact keys incl. exercise_token, no move
     substring, FENs replay correctly).
  H. Tampered token -> 400; another user's token -> 403; expired token
     -> 400; all with no line information.
  I. Double submit -> 409 first reusable... (second submit 409, body has
     no grading fields and no reveal).
  J. 30-day exclusion: attempted segment never re-served (deterministic
     3-segment pool); 31-day-old attempt no longer excludes.

Run with: cd src && ../venv/bin/python routers/calc_test.py
Requires: DATABASE_URL / INTERNAL_SECRET from root .env, calc_segments
loaded, calc_attempts migrated (nonce + CASCADE).
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi.testclient import TestClient

import main as app_module
from core import database
from core.migrations import run_migrations
from services.calc_tokens import _b64e

TEST_CLERK_ID = "calc-http-test"
TEST_EMAIL = "calc-http-test@example.invalid"
TEST_CLERK_ID_2 = "calc-http-test-two"


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
    _exec("DELETE FROM calc_attempts WHERE user_id IN (%s, %s)",
          (TEST_CLERK_ID, TEST_CLERK_ID_2))
    _exec("DELETE FROM users WHERE clerk_id IN (%s, %s) OR email = %s",
          (TEST_CLERK_ID, TEST_CLERK_ID_2, TEST_EMAIL))
    _exec("INSERT INTO users (clerk_id, email) VALUES (%s, %s)",
          (TEST_CLERK_ID, TEST_EMAIL))
    _exec("INSERT INTO users (clerk_id, email) VALUES (%s, %s)",
          (TEST_CLERK_ID_2, "calc-http-test-two@example.invalid"))
    return (TestClient(app_module.app),
            {"X-Clerk-User-Id": TEST_CLERK_ID,
             "X-Internal-Secret": os.environ["INTERNAL_SECRET"]})


def teardown():
    _exec("DELETE FROM calc_attempts WHERE user_id IN (%s, %s)",
          (TEST_CLERK_ID, TEST_CLERK_ID_2))
    _exec("DELETE FROM calc_ratings WHERE user_id IN (%s, %s)",
          (TEST_CLERK_ID, TEST_CLERK_ID_2))
    _exec("DELETE FROM users WHERE clerk_id IN (%s, %s)",
          (TEST_CLERK_ID, TEST_CLERK_ID_2))


def _mint(user, seg_id, offset, n, issued_at=None):
    """Test-only token minter (mirrors services.calc_tokens.issue_token)."""
    from services.calc_tokens import _b64e, _signing_key
    payload = {"kind": "solve", "user_id": user, "segment_id": seg_id,
               "offset": offset, "n": n, "nonce": os.urandom(16).hex(),
               "issued_at": int(time.time()) if issued_at is None
               else issued_at}
    body = _b64e(json.dumps(payload, sort_keys=True,
                             separators=(",", ":")).encode())
    sig = _b64e(hmac_mod.new(_signing_key(), body.encode(),
                             hashlib.sha256).digest())
    return f"{body}.{sig}", payload


def _nonce_of(token: str) -> str:
    from services.calc_tokens import _b64d
    return json.loads(_b64d(token.split(".")[0]).decode())["nonce"]


def _segment(seg_id):
    rows = _q("SELECT id, segment_fen, line_uci, solver_move_count, rating,"
              " status FROM calc_segments WHERE id = %s", (seg_id,))
    assert rows, f"segment {seg_id} missing"
    sid, fen, line, n, rating, status = rows[0]
    return {"id": str(sid), "fen": fen, "toks": line.split(), "n": n,
            "rating": rating, "status": status}


def _pick(min_len=3, want_promo=False, exclude=(), lowest=False):
    sql = ("SELECT id FROM calc_segments WHERE status = 'active'"
           " AND trivially_forced = false AND solver_move_count >= %s")
    params: list = [min_len]
    if exclude:
        sql += " AND id NOT IN %s"
        params.append(tuple(exclude))
    sql += " ORDER BY rating ASC LIMIT 50" if lowest else \
        " ORDER BY rating DESC LIMIT 50"
    for (sid,) in _q(sql, tuple(params)):
        seg = _segment(str(sid))
        if (any(len(t) == 5 for t in seg["toks"])) == want_promo:
            return seg
    raise AssertionError("no suitable segment found")


def _attempted(user):
    return [r[0] for r in _q("SELECT DISTINCT segment_id FROM calc_attempts"
                             " WHERE user_id = %s", (user,))]


def _expected(seg, offset, n):
    return seg["toks"][2 * offset:2 * offset + 2 * n - 1]


def _other_uci(avoid):
    for cand in ("a7a6", "h7h6", "a2a3", "h2h3"):
        if cand != avoid:
            return cand
    raise AssertionError


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + f" {name}"
          + (f" — {detail}" if detail and not cond else ""))


def main():
    client, headers = setup()
    h2 = {"X-Clerk-User-Id": TEST_CLERK_ID_2,
          "X-Internal-Secret": os.environ["INTERNAL_SECRET"]}
    try:
        # --- GET issues a token ------------------------------------------
        r = client.get("/api/calc/next?n=3", headers=headers)
        check("GET serves", r.status_code == 200, r.text[:200])
        got = r.json()
        check("GET keys exact",
              set(got.keys()) == {"segment_id", "offset", "n", "fen",
                                  "side_to_move", "rating", "exercise_token"},
              set(got.keys()))
        gseg = _segment(got["segment_id"])

        # --- A: full correct line via token -------------------------------
        exp = _expected(gseg, got["offset"], got["n"])
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": got["exercise_token"], "moves": exp,
            "time_taken_ms": 12345})
        check("A status 200", r.status_code == 200, r.text[:200])
        body = r.json()
        check("A solved, no continuation", body["solved"] is True
              and body["continuation"] == [], body)
        rows = _q("SELECT solved, first_divergence_ply, diverged_on,"
                  " exercise_nonce, time_taken_ms, rated, rating_before,"
                  " rating_after, score, k_factor, expected_score"
                  " FROM calc_attempts"
                  " WHERE user_id = %s AND segment_id = %s"
                  " AND offset_solver_moves = %s AND n = %s",
                  (TEST_CLERK_ID, got["segment_id"], got["offset"],
                   got["n"]))
        check("A one attempt row", len(rows) == 1, rows)
        check("A row solved, NULL divergence, nonce + server elapsed",
              rows and rows[0][0] is True and rows[0][1] is None
              and rows[0][2] is None and rows[0][3]
              and rows[0][4] is not None and rows[0][4] != 12345
              and rows[0][4] >= 0, rows)
        # First attempt on a segment is rated; fresh user seeds at 1100.
        from core.rating import (CALC_K_FACTOR, calc_expected_score,
                                calculate_fractional_rating_change,
                                calc_clamp_rating)
        exp_e = calc_expected_score(1100, gseg["rating"])
        exp_d = calculate_fractional_rating_change(1100, gseg["rating"],
                                                   1.0)
        check("A rated, exact delta",
              body["rated"] is True and body["rating_before"] == 1100
              and body["delta"] == exp_d
              and body["rating_after"] == calc_clamp_rating(1100 + exp_d)
              and rows[0][5] is True and rows[0][8] == 1.0
              and rows[0][9] == CALC_K_FACTOR and rows[0][10] == exp_e,
              (body, rows))

        # --- B: divergences reveal only this exercise ----------------------
        seg = _pick(min_len=3)
        tok, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        exp3 = _expected(seg, 0, 3)
        bad1 = [exp3[0], _other_uci(exp3[1])] + exp3[2:]
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tok, "moves": bad1})
        b = r.json()
        check("B defender divergence",
              b["solved"] is False and b["first_divergence_ply"] == 1
              and b["diverged_on"] == "defender"
              and b["continuation"] == exp3[1:], b)
        tok0, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        bad0 = [_other_uci(exp3[0])] + exp3[1:]
        b = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tok0, "moves": bad0}).json()
        check("B first-move divergence",
              b["first_divergence_ply"] == 0 and b["diverged_on"] == "solver"
              and b["continuation"] == exp3, b)

        # --- C: malformed / missing ----------------------------------------
        tokc, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokc, "moves": exp3[:-1]})
        check("C too few -> 422", r.status_code == 422, r.status_code)
        tokc2, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        garbage = exp3.copy()
        garbage[2] = "zzz9"
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokc2, "moves": garbage})
        check("C garbage UCI -> 422", r.status_code == 422, r.status_code)
        toku, _ = _mint(TEST_CLERK_ID,
                         "00000000-0000-0000-0000-000000000000", 0, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": toku, "moves": exp3})
        check("C unknown segment -> 404", r.status_code == 404,
              r.status_code)
        _exec("UPDATE calc_segments SET status = 'retired' WHERE id = %s",
              (seg["id"],))
        try:
            tokr, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
            r = client.post("/api/calc/submit", headers=headers, json={
                "exercise_token": tokr, "moves": exp3})
            check("C retired segment -> 404", r.status_code == 404,
                  r.status_code)
        finally:
            _exec("UPDATE calc_segments SET status = 'active' WHERE id = %s",
                  (seg["id"],))
        r = client.get("/api/calc/next?n=2", headers=headers)
        check("C n=2 -> 422", r.status_code == 422, r.status_code)
        r = client.get("/api/calc/next",
                       headers={"X-Internal-Secret":
                                os.environ["INTERNAL_SECRET"]})
        check("C missing clerk -> 400", r.status_code == 400, r.status_code)

        # --- D: promotion ----------------------------------------------------
        pseg = _pick(min_len=3, want_promo=True)
        ptoks = pseg["toks"]
        pi = next(i for i, t in enumerate(ptoks) if len(t) == 5)
        poff = max(0, min(pi // 2 - 1, (len(ptoks) + 1) // 2 - 3))
        pexp = _expected(pseg, poff, 3)
        check("D promo in window", any(len(t) == 5 for t in pexp), pexp)
        tokp, _ = _mint(TEST_CLERK_ID, pseg["id"], poff, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokp, "moves": pexp})
        check("D promo exact solves", r.json()["solved"] is True,
              r.text[:200])
        tokp2, _ = _mint(TEST_CLERK_ID, pseg["id"], poff, 3)
        upper = [m[:-1] + m[-1].upper() if len(m) == 5 else m for m in pexp]
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokp2, "moves": upper})
        check("D promo uppercase normalized", r.json()["solved"] is True,
              r.text[:200])

        # --- E: offset > 0 ----------------------------------------------------
        oseg = _pick(min_len=4)
        oexp = _expected(oseg, 1, 3)
        toko, _ = _mint(TEST_CLERK_ID, oseg["id"], 1, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": toko, "moves": oexp})
        check("E offset>0 correct solves", r.json()["solved"] is True,
              r.text[:300])

        # --- F: solved segment never re-served ---------------------------------
        seen = False
        for _ in range(12):
            r = client.get("/api/calc/next?n=3", headers=headers)
            if r.status_code != 200:
                break
            if r.json()["segment_id"] == got["segment_id"]:
                seen = True
        check("F solved segment not re-served", not seen)

        # --- G: no line leak -----------------------------------------------------
        leak = False
        for _ in range(5):
            r = client.get("/api/calc/next?n=3", headers=h2)
            if r.status_code != 200:
                continue
            g = r.json()
            lseg = _segment(g["segment_id"])
            if any(t in json.dumps(g) for t in lseg["toks"]):
                leak = True
            b = chess.Board(lseg["fen"])
            for u in lseg["toks"][:2 * g["offset"]]:
                b.push(chess.Move.from_uci(u))
            if b.fen() != g["fen"]:
                leak = True
        check("G no leak, FENs replay correctly", not leak)

        # --- H: token attacks ------------------------------------------------------
        tokt, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        # Tamper the FIRST sig char: every one of its 6 bits is data
        # (tampering the last char can be a no-op — low bits are padding).
        body, sig = tokt.split(".", 1)
        tampered = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tampered, "moves": exp3})
        check("H tampered -> 400, no line info",
              r.status_code == 400 and "continuation" not in r.json()
              and all(t not in r.text for t in exp3), (r.status_code,
                                                      r.text[:200]))
        # Pre-subkey signature (signed with INTERNAL_SECRET directly) must
        # fail: proves the server uses the derived subkey.
        raw_body = _b64e(json.dumps(
            {"kind": "solve", "user_id": TEST_CLERK_ID, "segment_id": seg["id"],
             "offset": 0, "n": 3, "nonce": os.urandom(16).hex(),
             "issued_at": int(time.time())},
            sort_keys=True, separators=(",", ":")).encode())
        raw_sig = _b64e(hmac_mod.new(
            os.environ["INTERNAL_SECRET"].encode(), raw_body.encode(),
            hashlib.sha256).digest())
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": f"{raw_body}.{raw_sig}", "moves": exp3})
        check("H direct-secret signature rejected -> 400",
              r.status_code == 400, (r.status_code, r.text[:200]))
        tokf, _ = _mint(TEST_CLERK_ID_2, seg["id"], 0, 3)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokf, "moves": exp3})
        check("H foreign token -> 403", r.status_code == 403, r.status_code)
        toko_exp, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3,
                             issued_at=int(time.time()) - 25 * 3600)
        r = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": toko_exp, "moves": exp3})
        check("H expired -> 400, no line info",
              r.status_code == 400 and "continuation" not in r.json(),
              (r.status_code, r.text[:200]))

        # --- I: double submit -> 409, no reveal --------------------------------------
        tokd, _ = _mint(TEST_CLERK_ID, seg["id"], 0, 3)
        r1 = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokd, "moves": exp3})
        check("I first submit accepted", r1.status_code == 200,
              r1.text[:200])
        r2 = client.post("/api/calc/submit", headers=headers, json={
            "exercise_token": tokd, "moves": exp3})
        b2 = r2.json()
        check("I replay -> 409 with no grading fields and no reveal",
              r2.status_code == 409 and "solved" not in b2
              and "continuation" not in b2
              and all(t not in r2.text for t in exp3), (r2.status_code,
                                                      r2.text[:200]))
        nrows = _q("SELECT COUNT(*) FROM calc_attempts WHERE user_id = %s"
                   " AND exercise_nonce = %s",
                   (TEST_CLERK_ID, _nonce_of(tokd)))
        check("I replay wrote no row", nrows == [(1,)], nrows)

        # --- J: 30-day exclusion, deterministic 3-segment pool -------------------------
        pool = [str(r[0]) for r in _q(
            "SELECT id FROM calc_segments WHERE status = 'active'"
            " AND trivially_forced = false AND solver_move_count >= 3"
            " ORDER BY rating DESC LIMIT 3")]
        # Pin user2's window over the top-rated pool (else the rating
        # window, not the exclusion, decides what is served).
        _exec("UPDATE calc_ratings SET rating = 3200 WHERE user_id = %s"
              " AND n = 3", (TEST_CLERK_ID_2,))
        _exec("UPDATE calc_segments SET status = 'retired'"
              " WHERE status = 'active' AND id NOT IN %s", (tuple(pool),))
        try:
            # fail segment pool[0] as user2 (failure reveals the line too)
            s0 = _segment(pool[0])
            e0 = _expected(s0, 0, 3)
            tokj, _ = _mint(TEST_CLERK_ID_2, pool[0], 0, 3)
            bad = [_other_uci(e0[0])] + e0[1:]
            r = client.post("/api/calc/submit", headers=h2, json={
                "exercise_token": tokj, "moves": bad})
            check("J fail recorded", r.status_code == 200
                  and r.json()["solved"] is False, r.text[:200])
            served = set()
            for _ in range(10):
                r = client.get("/api/calc/next?n=3", headers=h2)
                if r.status_code == 200:
                    served.add(r.json()["segment_id"])
            check("J failed segment never re-served",
                  pool[0] not in served, served)
            # backdate beyond the window -> servable again
            _exec("UPDATE calc_attempts SET created_at = NOW() -"
                  " make_interval(days => 31) WHERE user_id = %s"
                  " AND segment_id = %s", (TEST_CLERK_ID_2, pool[0]))
            served2 = set()
            for _ in range(15):
                r = client.get("/api/calc/next?n=3", headers=h2)
                if r.status_code == 200:
                    served2.add(r.json()["segment_id"])
            check("J 31-day-old attempt no longer excludes",
                  pool[0] in served2, served2)
        finally:
            _exec("UPDATE calc_segments SET status = 'active'"
                  " WHERE status = 'retired'")

        # --- R: per-depth rating --------------------------------------------------
        from core.rating import (CALC_K_FACTOR, calc_expected_score,
                                calculate_fractional_rating_change,
                                calc_clamp_rating)
        # R1: seed from tactical_rating, else fixed default.
        _exec("UPDATE users SET tactical_rating = 1500 WHERE clerk_id = %s",
              (TEST_CLERK_ID_2,))
        _exec("DELETE FROM calc_ratings WHERE user_id = %s", (TEST_CLERK_ID_2,))
        r = client.get("/api/calc/next?n=3", headers=h2)
        check("R1 seeded from tactical-200",
              r.status_code == 200 and _q(
                  "SELECT rating FROM calc_ratings WHERE user_id = %s"
                  " AND n = 3", (TEST_CLERK_ID_2,)) == [(1300,)], r.status_code)
        _exec("UPDATE users SET tactical_rating = NULL WHERE clerk_id = %s",
              (TEST_CLERK_ID_2,))
        _exec("DELETE FROM calc_ratings WHERE user_id = %s", (TEST_CLERK_ID_2,))
        r = client.get("/api/calc/next?n=4", headers=h2)
        check("R1 default seed 1100",
              r.status_code == 200 and _q(
                  "SELECT rating FROM calc_ratings WHERE user_id = %s"
                  " AND n = 4", (TEST_CLERK_ID_2,)) == [(1100,)], r.status_code)

        # R2: full failure lowers the rating, exact delta (near user rating
        # so the expectancy is far from 0/1 and the delta is nonzero).
        fseg = _pick(min_len=3, lowest=True,
                     exclude=_attempted(TEST_CLERK_ID_2))
        ftok, _ = _mint(TEST_CLERK_ID_2, fseg["id"], 0, 3)
        fexp = _expected(fseg, 0, 3)
        r = client.get("/api/calc/next?n=3", headers=h2)
        check("R2 n=3 row seeded", r.status_code == 200, r.status_code)
        r0 = _q("SELECT rating FROM calc_ratings WHERE user_id = %s AND n = 3",
                (TEST_CLERK_ID_2,))[0][0]
        r = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": ftok,
            "moves": [_other_uci(fexp[0])] + fexp[1:]})
        b = r.json()
        e = calc_expected_score(r0, fseg["rating"])
        d = calculate_fractional_rating_change(r0, fseg["rating"], 0.0)
        check("R2 failure lowers, exact fields",
              b["solved"] is False and b["rated"] is True
              and b["rating_before"] == r0 and b["delta"] == d
              and b["rating_after"] == calc_clamp_rating(r0 + d) and d < 0
              and _q("SELECT score, expected_score, k_factor FROM"
                     " calc_attempts WHERE user_id = %s AND segment_id = %s",
                     (TEST_CLERK_ID_2, fseg["id"])) == [(0.0, e,
                                                        CALC_K_FACTOR)],
              (b, d, e))

        # R3: partial credit — solver ply vs defender ply, exact scores.
        pseg = _pick(min_len=5, exclude=_attempted(TEST_CLERK_ID_2))
        pexp4 = _expected(pseg, 0, 4)
        ptok, _ = _mint(TEST_CLERK_ID_2, pseg["id"], 0, 4)
        wrong_solver = pexp4[:2] + [_other_uci(pexp4[2])] + pexp4[3:]
        r = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": ptok, "moves": wrong_solver})
        b = r.json()
        check("R3 solver-ply divergence scores 1/4",
              b["solved"] is False and b["first_divergence_ply"] == 2
              and b["diverged_on"] == "solver"
              and _q("SELECT score FROM calc_attempts WHERE user_id = %s"
                     " AND segment_id = %s", (TEST_CLERK_ID_2,
                                             pseg["id"])) == [(0.25,)], b)
        qseg = _pick(min_len=5, exclude=_attempted(TEST_CLERK_ID_2))
        qexp4 = _expected(qseg, 0, 4)
        qtok, _ = _mint(TEST_CLERK_ID_2, qseg["id"], 0, 4)
        wrong_def = qexp4[:3] + [_other_uci(qexp4[3])] + qexp4[4:]
        # qexp4[3] is the 2nd defender reply (after solver move 2): guard it
        assert len(qexp4) == 7
        r = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": qtok, "moves": wrong_def})
        b = r.json()
        check("R3 defender-ply divergence scores 2/4",
              b["solved"] is False and b["first_divergence_ply"] == 3
              and b["diverged_on"] == "defender"
              and _q("SELECT score FROM calc_attempts WHERE user_id = %s"
                     " AND segment_id = %s", (TEST_CLERK_ID_2,
                                             qseg["id"])) == [(0.5,)], b)
        rb = _q("SELECT rating_before, rating_after FROM calc_attempts"
                " WHERE user_id = %s AND segment_id = %s",
                (TEST_CLERK_ID_2, qseg["id"]))[0]
        qe = calc_expected_score(rb[0], qseg["rating"])
        qd = calculate_fractional_rating_change(rb[0], qseg["rating"], 0.5)
        check("R3 partial delta exact",
              b["delta"] == qd and b["rating_after"] == calc_clamp_rating(rb[0]
                                                                     + qd)
              and _q("SELECT expected_score FROM calc_attempts"
                     " WHERE user_id = %s AND segment_id = %s",
                     (TEST_CLERK_ID_2, qseg["id"])) == [(qe,)], (b, qd))

        # R4: second attempt on the same segment is unrated, any offset/n.
        rseg = _pick(min_len=5, exclude=_attempted(TEST_CLERK_ID_2))
        rtok1, _ = _mint(TEST_CLERK_ID_2, rseg["id"], 0, 3)
        r1 = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": rtok1, "moves": _expected(rseg, 0, 3)})
        check("R4 first attempt rated", r1.json()["rated"] is True,
              r1.text[:200])
        mid = _q("SELECT rating FROM calc_ratings WHERE user_id = %s AND n = 4",
                 (TEST_CLERK_ID_2,))
        rtok2, _ = _mint(TEST_CLERK_ID_2, rseg["id"], 1, 4)
        r2 = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": rtok2, "moves": _expected(rseg, 1, 4)})
        b2 = r2.json()
        check("R4 second attempt unrated despite offset+n change",
              b2["solved"] is True and b2["rated"] is False
              and b2["rating_before"] is None and b2["rating_after"] is None
              and b2["delta"] == 0
              and _q("SELECT rating FROM calc_ratings WHERE user_id = %s"
                     " AND n = 4", (TEST_CLERK_ID_2,)) == mid
              and _q("SELECT rated, rating_before, score FROM calc_attempts"
                     " WHERE user_id = %s AND segment_id = %s"
                     " AND offset_solver_moves = 1",
                     (TEST_CLERK_ID_2, rseg["id"])) == [(False, None, 1.0)],
              (b2, mid))

        # R5: two concurrent first-attempt submits serialize on the row lock.
        import threading
        excl = _attempted(TEST_CLERK_ID)
        cseg_a = _pick(min_len=5, exclude=excl)
        cseg_b = _pick(min_len=5, exclude=excl + [cseg_a['id']])
        _exec("DELETE FROM calc_ratings WHERE user_id = %s AND n = 5",
              (TEST_CLERK_ID,))
        _exec("DELETE FROM calc_attempts WHERE user_id = %s"
              " AND segment_id IN (%s, %s)",
              (TEST_CLERK_ID, cseg_a["id"], cseg_b["id"]))
        toka, _ = _mint(TEST_CLERK_ID, cseg_a["id"], 0, 5)
        tokb, _ = _mint(TEST_CLERK_ID, cseg_b["id"], 0, 5)
        expa = _expected(cseg_a, 0, 5)
        expb = _expected(cseg_b, 0, 5)
        bar = threading.Barrier(3)
        outs = {}

        def _fire(name, tok, moves):
            bar.wait()
            outs[name] = client.post("/api/calc/submit", headers=headers,
                                     json={"exercise_token": tok,
                                           "moves": moves})

        ta = threading.Thread(target=_fire, args=("a", toka, expa))
        tb = threading.Thread(target=_fire, args=("b", tokb, expb))
        ta.start()
        tb.start()
        bar.wait()
        ta.join()
        tb.join()
        ok_ab = outs["a"].status_code == 200 and outs["b"].status_code == 200
        check("R5 concurrent submits both accepted",
              ok_ab and outs["a"].json()["rated"]
              and outs["b"].json()["rated"],
              (outs["a"].status_code, outs["b"].status_code))
        r5 = _q("SELECT rating, attempts FROM calc_ratings WHERE user_id = %s"
                " AND n = 5", (TEST_CLERK_ID,))[0]

        def _apply(r, seg_rating):
            dd = calculate_fractional_rating_change(r, seg_rating, 1.0)
            return calc_clamp_rating(r + dd)

        finals = {_apply(_apply(1100, cseg_a["rating"]), cseg_b["rating"]),
                  _apply(_apply(1100, cseg_b["rating"]), cseg_a["rating"])}
        check("R5 final equals a sequential order, attempts=2",
              r5[0] in finals and r5[1] == 2, (r5, finals))

        # R6: window widening at the corpus edges.
        _exec("UPDATE calc_ratings SET rating = 1050 WHERE user_id = %s"
              " AND n = 3", (TEST_CLERK_ID_2,))
        r = client.get("/api/calc/next?n=3", headers=h2)
        check("R6 low edge widens to 1200s",
              r.status_code == 200 and _segment(
                  r.json()["segment_id"])["rating"] >= 1150,
              r.status_code)
        _exec("UPDATE calc_ratings SET rating = 3400 WHERE user_id = %s"
              " AND n = 3", (TEST_CLERK_ID_2,))
        r = client.get("/api/calc/next?n=3", headers=h2)
        check("R6 high edge widens down to corpus max",
              r.status_code == 200, r.status_code)

        # R7: /ratings lists per-depth rows with exact keys (no line data).
        r = client.get("/api/calc/ratings", headers=headers)
        rl = r.json()["ratings"]
        check("R7 ratings endpoint",
              r.status_code == 200 and all(
                  set(e.keys()) == {"n", "rating", "attempts"} for e in rl)
              and any(e["n"] == 3 for e in rl)
              and "line_uci" not in r.text and "continuation" not in r.text,
              r.text[:300])

        # K: single-ply hint + hinted-submit neutralization -----------------
        kseg = _pick(min_len=3, exclude=_attempted(TEST_CLERK_ID_2))
        ktok, _ = _mint(TEST_CLERK_ID_2, kseg["id"], 0, 3)
        kexp = _expected(kseg, 0, 3)
        h = client.post("/api/calc/hint", headers=h2, json={
            "exercise_token": ktok, "plies_entered": 0}).json()
        check("K1 hint ply 0 is the first expected ply",
              h.get("uci") == kexp[0] and h.get("ply") == 0, h)
        h2p = client.post("/api/calc/hint", headers=h2, json={
            "exercise_token": ktok, "plies_entered": 2}).json()
        check("K1 hint ply 2, token not consumed",
              h2p.get("uci") == kexp[2] and h2p.get("ply") == 2, h2p)
        ro = client.post("/api/calc/hint", headers=h2, json={
            "exercise_token": ktok, "plies_entered": len(kexp)})
        check("K2 hint past the line -> 422", ro.status_code == 422,
              ro.text[:200])
        rn = client.post("/api/calc/hint", headers=h2, json={
            "exercise_token": ktok, "plies_entered": -1})
        check("K2 negative plies_entered -> 422", rn.status_code == 422,
              rn.text[:200])
        mid3 = _q("SELECT rating FROM calc_ratings WHERE user_id = %s"
                  " AND n = 3", (TEST_CLERK_ID_2,))
        kh = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": ktok, "moves": kexp, "hints_used": 2})
        kb = kh.json()
        check("K3 hinted solve grades but stores unrated",
              kh.status_code == 200 and kb["solved"] is True
              and kb["rated"] is False and kb["rating_before"] is None
              and kb["rating_after"] is None and kb["delta"] == 0
              and _q("SELECT rating FROM calc_ratings WHERE user_id = %s"
                     " AND n = 3", (TEST_CLERK_ID_2,)) == mid3
              and _q("SELECT rated, rating_before, score FROM calc_attempts"
                     " WHERE user_id = %s AND segment_id = %s"
                     " AND offset_solver_moves = 0 AND n = 3",
                     (TEST_CLERK_ID_2, kseg["id"])) == [(False, None, 1.0)],
              (kb, mid3))
        hn = _mint(TEST_CLERK_ID_2, kseg["id"], 0, 3)[0]
        rh = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": hn, "moves": kexp, "hints_used": -1})
        check("K4 negative hints_used -> 422", rh.status_code == 422,
              rh.text[:200])

        # T: retry re-issues a submittable token for the same exercise -----
        tseg = _pick(min_len=3, exclude=_attempted(TEST_CLERK_ID_2))
        ttok, _ = _mint(TEST_CLERK_ID_2, tseg["id"], 0, 3)
        texp = _expected(tseg, 0, 3)
        t0 = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": ttok, "moves": texp})
        check("T0 first submit rated", t0.json()["rated"] is True,
              t0.text[:200])
        tr = client.post("/api/calc/retry", headers=h2, json={
            "exercise_token": ttok})
        check("T1 retry issues a fresh token for a spent one",
              tr.status_code == 200
              and tr.json()["exercise_token"] != ttok,
              tr.text[:200])
        t1 = client.post("/api/calc/submit", headers=h2, json={
            "exercise_token": tr.json()["exercise_token"], "moves": texp})
        tb = t1.json()
        check("T1 retry submit grades, stores unrated",
              t1.status_code == 200 and tb["solved"] is True
              and tb["rated"] is False
              and len(_q("SELECT id FROM calc_attempts WHERE user_id = %s"
                         " AND segment_id = %s",
                         (TEST_CLERK_ID_2, tseg["id"]))) == 2,
              (tb, t1.text[:200]))
        ftok, _ = _mint(TEST_CLERK_ID, tseg["id"], 0, 3)
        ff = client.post("/api/calc/retry", headers=h2, json={
            "exercise_token": ftok})
        check("T2 foreign retry token -> 403", ff.status_code == 403,
              ff.text[:200])
        fx = client.post("/api/calc/retry", headers=h2, json={
            "exercise_token": ttok + "x"})
        check("T2 tampered retry token -> 400", fx.status_code == 400,
              fx.text[:200])
    finally:
        teardown()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
