"""
Live test harness for the Engine Sparring move endpoint
(POST /train/engine-sparring-move in src/routers/train.py).

No router tests existed in this codebase before this file (checked: zero
TestClient usages), so the pattern established here is: a MINIMAL FastAPI app
carrying only routers.train's router mounted at the same "/api" prefix main.py
uses, driven by fastapi.testclient.TestClient WITHOUT entering its lifespan
(no boot-time engine starts -- the endpoint spawns its own private Stockfish
per call and needs neither Maia nor the shared singleton nor the DB). The
Upstash-backed rate limiter is proven wired by monkeypatching
core.rate_limit.get_redis with a fake counter.

Parts:
  1. schema drift guard: the schema-layer PersonaName Literal must equal
     PersonaType's values exactly (schemas deliberately do not import from
     services/ -- this pins the redeclaration).
  2. happy path (REAL engine, real rerank): response contract, persona pick,
     best_move_* null-vs-differs invariant.
  3. invalid FEN -> 400 "Invalid FEN".
  4. wrong bot_color turn -> 409 "It is not the bot's turn".
  5. invalid persona name -> 422 (FastAPI/Pydantic's natural status for a
     Literal violation; reported from the live response).
  6. out-of-range Elo -> 400 "Invalid engine strength..." (never 500).
  7. terminal (checkmate) position -> 409 game-over.
  8. best_move-differs branch + ValueError mapping via a canned rerank_moves.
  9. rate limiter wired: over-limit -> 429 before validation; under-limit
     request reaches validation.

Run with: cd src && ../venv/bin/python routers/train_engine_sparring_test.py
"""
import os
import sys
from typing import get_args

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.rate_limit as rate_limit
import routers.train as train_router_module
from routers.train import router as train_router
from schemas.train_schemas import EngineSparringMoveRequest, PersonaName
from services.persona_fixtures import FIXTURES
from services.persona_reranker import PersonaType

FIXTURES_BY_NAME = {f["name"]: f for f in FIXTURES}
SACRIFICE_FEN = FIXTURES_BY_NAME["obvious sacrifice"]["fen"]  # white to move
SACRIFICE_FEN_BLACK_TO_MOVE = SACRIFICE_FEN.replace(" w - - 0 1", " b - - 0 1")
CHECKMATE_FEN = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"

URL = "/api/train/engine-sparring-move"
CLERK = {"X-Clerk-User-Id": "test-clerk-user"}


class FakeRedis:
    """Stand-in for the Upstash REST client the limiter talks to."""

    def __init__(self, incr_value=1):
        self.incr_value = incr_value
        self.keys = []

    def incr(self, key):
        self.keys.append(key)
        return self.incr_value

    def expire(self, *args):
        pass


def _make_client():
    app = FastAPI()
    app.include_router(train_router, prefix="/api")
    return TestClient(app)


def test_schema_persona_matches_enum():
    literal_values = get_args(PersonaName)
    enum_values = tuple(p.value for p in PersonaType)
    assert literal_values == enum_values, (literal_values, enum_values)
    fields = EngineSparringMoveRequest.model_fields
    for name in ("fen", "bot_color", "persona", "target_elo", "skill_level"):
        assert name in fields, name
    print(f"    PersonaName Literal == PersonaType values: {literal_values}")
    print("    request fields: fen/bot_color/persona/target_elo/skill_level all present")
    print("  [PASS] schema redeclaration pinned to the service enum (no drift)")


def test_happy_path_real_engine():
    client = _make_client()
    response = client.post(
        URL,
        headers=CLERK,
        json={
            "fen": SACRIFICE_FEN,
            "bot_color": "white",
            "persona": "attacker",
        },
    )
    assert response.status_code == 200, (response.status_code, response.text)
    data = response.json()
    assert set(data) == {
        "move_uci", "move_san", "persona", "engine_score_cp",
        "engine_norm_cp", "persona_final_cp", "best_move_uci", "best_move_san",
    }, set(data)
    assert data["persona"] == "attacker"
    assert isinstance(data["engine_score_cp"], int)
    assert isinstance(data["engine_norm_cp"], float)
    assert isinstance(data["persona_final_cp"], float)
    assert data["engine_norm_cp"] <= 0.0
    board = chess.Board(SACRIFICE_FEN)
    chosen = chess.Move.from_uci(data["move_uci"])
    assert chosen in board.legal_moves
    if data["best_move_uci"] is None:
        assert data["engine_norm_cp"] == 0.0, data
        print(f"    200: chose {data['move_san']} ({data['move_uci']}) = engine best"
              f" (norm {data['engine_norm_cp']:+.1f}, final {data['persona_final_cp']:+.2f})"
              " -> best_move_* null as contracted")
    else:
        assert data["best_move_uci"] != data["move_uci"]
        print(f"    200: chose {data['move_san']} (norm {data['engine_norm_cp']:+.1f});"
              f" engine best {data['best_move_san']} surfaced")
    print(f"    full response: {data}")
    print("  [PASS] happy path: real Stockfish rerank through the HTTP layer")


def test_invalid_fen():
    client = _make_client()
    response = client.post(
        URL,
        headers=CLERK,
        json={"fen": "not a fen", "bot_color": "white", "persona": "attacker"},
    )
    assert response.status_code == 400, (response.status_code, response.text)
    assert response.json()["detail"] == "Invalid FEN"
    print("    'not a fen' -> 400 'Invalid FEN' (message matches sparring-move)")
    print("  [PASS] invalid FEN")


def test_wrong_turn():
    client = _make_client()
    response = client.post(
        URL,
        headers=CLERK,
        json={
            "fen": SACRIFICE_FEN_BLACK_TO_MOVE,
            "bot_color": "white",
            "persona": "attacker",
        },
    )
    assert response.status_code == 409, (response.status_code, response.text)
    assert response.json()["detail"] == "It is not the bot's turn"
    print("    black-to-move FEN + bot_color=white -> 409 'It is not the bot's turn'")
    print("  [PASS] bot-turn integrity check reused verbatim")


def test_invalid_persona_status():
    client = _make_client()
    response = client.post(
        URL,
        headers=CLERK,
        json={"fen": SACRIFICE_FEN, "bot_color": "white", "persona": "wizard"},
    )
    print(f"    persona='wizard' -> HTTP {response.status_code}: {response.json()}")
    assert response.status_code == 422, (
        f"expected FastAPI/Pydantic's natural 422, got {response.status_code}"
    )
    print("  [PASS] invalid persona rejected at parse time (422), never reaching"
          " the engine")


def test_bad_elo_maps_to_400():
    client = _make_client()
    for bad in (100, 5000):
        response = client.post(
            URL,
            headers=CLERK,
            json={
                "fen": SACRIFICE_FEN,
                "bot_color": "white",
                "persona": "attacker",
                "target_elo": bad,
            },
        )
        assert response.status_code == 400, (bad, response.status_code, response.text)
        detail = response.json()["detail"]
        assert "Invalid engine strength" in detail and "elo" in detail.lower(), detail
        print(f"    target_elo={bad} -> 400: {detail}")
    print("  [PASS] out-of-range Elo: configure_strength's ValueError -> 400 (not 500)")


def test_terminal_position():
    client = _make_client()
    board = chess.Board(CHECKMATE_FEN)
    assert board.is_checkmate()
    response = client.post(
        URL,
        headers=CLERK,
        json={"fen": CHECKMATE_FEN, "bot_color": "white", "persona": "defender"},
    )
    assert response.status_code == 409, (response.status_code, response.text)
    detail = response.json()["detail"]
    assert "game is over" in detail, detail
    print(f"    checkmate FEN -> 409: {detail}")
    print("  [PASS] terminal position: deliberate 409 (divergence from"
          " sparring-move's accidental 502), as decided")


def test_best_move_differs_and_valueerror_mapping():
    # Canned rerank output (shape per persona_reranker's OUTPUT CONTRACT):
    # the persona picked a NON-best move; the engine-best row (norm 0) must
    # surface in best_move_uci/san. Also proves the endpoint calls
    # rerank_moves (not best_persona_move), and maps a service ValueError to
    # 400.
    canned = [
        {"uci": "e2e4", "san": "e4", "score_cp": 30, "engine_norm_cp": -7.0,
         "persona_score": 0.5, "persona_final_cp": 43.0},
        {"uci": "d2d4", "san": "d4", "score_cp": 37, "engine_norm_cp": 0.0,
         "persona_score": -0.3, "persona_final_cp": -30.0},
    ]
    original = train_router_module.rerank_moves
    calls = []

    def fake_rerank(board, persona, elo=None, skill_level=None):
        calls.append((persona, elo, skill_level))
        return [dict(row) for row in canned]

    def fake_valueerror(board, persona, elo=None, skill_level=None):
        raise ValueError("elo must be in [1320, 3190]; got 100")

    try:
        train_router_module.rerank_moves = fake_rerank
        client = _make_client()
        response = client.post(
            URL,
            headers=CLERK,
            json={
                "fen": SACRIFICE_FEN,
                "bot_color": "white",
                "persona": "sacrificer",
                "target_elo": 2000,
            },
        )
        assert response.status_code == 200, (response.status_code, response.text)
        data = response.json()
        assert data["move_uci"] == "e2e4" and data["move_san"] == "e4"
        assert data["persona"] == "sacrificer"
        assert data["engine_score_cp"] == 30 and data["engine_norm_cp"] == -7.0
        assert data["persona_final_cp"] == 43.0
        assert data["best_move_uci"] == "d2d4" and data["best_move_san"] == "d4"
        assert calls == [("sacrificer", 2000, None)], calls
        print(f"    canned rerank -> chose e4, engine best d4 surfaced: {data}")
        print("    rerank_moves called with (persona, target_elo, skill_level) verbatim")

        train_router_module.rerank_moves = fake_valueerror
        response = client.post(
            URL,
            headers=CLERK,
            json={
                "fen": SACRIFICE_FEN,
                "bot_color": "white",
                "persona": "sacrificer",
                "target_elo": 2000,
            },
        )
        assert response.status_code == 400, (response.status_code, response.text)
        assert "Invalid engine strength" in response.json()["detail"]
        print("    service ValueError -> 400 'Invalid engine strength: ...'")
    finally:
        train_router_module.rerank_moves = original
    print("  [PASS] best_move-differs branch + ValueError->400 mapping")


def test_rate_limiter_wired():
    # Over limit (incr returns 16 > limit 15) -> 429 BEFORE validation runs
    # (the 'wizard' persona would 422 if the limiter were not attached).
    fake = FakeRedis(incr_value=16)
    original_get_redis = rate_limit.get_redis
    rate_limit.get_redis = lambda: fake
    try:
        client = _make_client()
        response = client.post(
            URL,
            headers=CLERK,
            json={"fen": SACRIFICE_FEN, "bot_color": "white", "persona": "wizard"},
        )
        assert response.status_code == 429, (response.status_code, response.text)
        assert fake.keys and "test-clerk-user" in fake.keys[0], fake.keys
        assert fake.keys[0].startswith("rate_limit:import:"), fake.keys
        print(f"    16th request in window -> 429 via fake redis (key {fake.keys[0]})")

        # Under limit (incr returns 15 == limit, not over) -> passes the
        # limiter and reaches validation: 'wizard' -> 422 (NOT 429).
        fake2 = FakeRedis(incr_value=15)
        rate_limit.get_redis = lambda: fake2
        response = client.post(
            URL,
            headers=CLERK,
            json={"fen": SACRIFICE_FEN, "bot_color": "white", "persona": "wizard"},
        )
        assert response.status_code == 422, (response.status_code, response.text)
        print("    15th request (== limit) -> limiter allows; 'wizard' hits 422")
    finally:
        rate_limit.get_redis = original_get_redis
    print("  [PASS] rate limiter dependency attached and enforced (15/60)")


def main() -> int:
    print("=== engine sparring endpoint tests ===")
    # The train router's limiter talks to Upstash Redis (core/rate_limit.py,
    # built lazily from UPSTASH_REDIS_REST_URL/TOKEN). Tests run without
    # those credentials, and must never touch the real Upstash anyway, so
    # every HTTP test runs with a fake Redis installed; the dedicated limiter
    # test swaps in specific counters and restores this global itself.
    original_get_redis = rate_limit.get_redis
    rate_limit.get_redis = lambda: FakeRedis(incr_value=1)
    try:
        for test in (
            test_schema_persona_matches_enum,
            test_happy_path_real_engine,
            test_invalid_fen,
            test_wrong_turn,
            test_invalid_persona_status,
            test_bad_elo_maps_to_400,
            test_terminal_position,
            test_best_move_differs_and_valueerror_mapping,
            test_rate_limiter_wired,
        ):
            try:
                test()
            except AssertionError as exc:
                print(f"\n  [FAIL] {exc}")
                return 1
            except Exception as exc:  # noqa: BLE001
                print(f"\n  [FAIL] raised {type(exc).__name__}: {exc}")
                return 1
    finally:
        rate_limit.get_redis = original_get_redis
    print("\nAll engine-sparring endpoint tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
