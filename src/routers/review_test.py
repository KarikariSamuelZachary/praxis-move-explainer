"""
HTTP auth harness for the review route (src/routers/review.py).

The Next.js /api/analyze proxy now requires a signed-in Clerk user and
forwards X-Clerk-User-Id; this harness proves the backend boundary too:

  A. Missing X-Internal-Secret -> 401 (app middleware), no engine work.
  B. Valid secret, missing X-Clerk-User-Id -> 400, and the check runs BEFORE
     limit_by_clerk_user_id, so its IP fallback can never apply. Proved by
     firing 6 anonymous requests: a fallback limiter (limit 5/min) would turn
     the 6th into 429, but every one must be a 400.
  C. Valid secret + header reaches the handler and returns 200 with the
     engine/LLM stubbed out.

No database and no engines are touched.

Run with: cd src && ../venv/bin/python routers/review_test.py
Requires: INTERNAL_SECRET from root .env.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import main as app_module

PGN = "1. e4 e5 2. Nf3 Nc6"
TEST_CLERK_ID = "review-auth-test-user"

_STUB_ROW = {
    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "san": "Start",
    "color": "white",
    "classification": "book",
    "cp_loss": 0,
    "ep_loss": 0.0,
    "eval_cp": 0.0,
    "eval_mate": None,
    "best_move_san": None,
    "best_move_uci": None,
}


def _client() -> TestClient:
    return TestClient(app_module.app)


def _secret() -> str:
    secret = os.environ.get("INTERNAL_SECRET")
    if not secret:
        raise SystemExit("INTERNAL_SECRET is not set (root .env)")
    return secret


def test_missing_internal_secret_is_rejected():
    response = _client().post("/api/review", json={"pgn": PGN})
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Unauthorized"
    print("  [PASS] missing X-Internal-Secret -> 401")


def test_missing_clerk_user_is_rejected():
    response = _client().post(
        "/api/review",
        json={"pgn": PGN},
        headers={"X-Internal-Secret": _secret()},
    )
    assert response.status_code == 400, response.text
    assert "X-Clerk-User-Id" in response.json()["detail"], response.text
    print("  [PASS] internal secret without X-Clerk-User-Id -> 400")


def test_missing_header_rejects_before_the_user_limiter():
    client = _client()
    secret = _secret()
    # The per-user limiter is 5/min and falls back to the client IP when the
    # header is absent. If it ran before the header check, the 6th request
    # here would be a 429 instead of a 400.
    for attempt in range(6):
        response = client.post(
            "/api/review",
            json={"pgn": PGN},
            headers={"X-Internal-Secret": secret},
        )
        assert response.status_code == 400, (
            f"attempt {attempt + 1}: expected 400, got "
            f"{response.status_code} (did the IP fallback limiter run?)"
        )
    print("  [PASS] 6 anonymous requests -> 400 every time, never 429")


def test_valid_secret_and_header_pass_the_auth_gate():
    import routers.review as review_module

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            pass

        def analyze_full_game(self, pgn, target_color="both"):
            return [_STUB_ROW]

    saved = (
        review_module.GameAnalyzer,
        review_module.get_review_stockfish,
        review_module._build_explainer,
    )
    review_module.GameAnalyzer = _StubAnalyzer
    review_module.get_review_stockfish = lambda *args, **kwargs: object()
    review_module._build_explainer = lambda: None
    try:
        response = _client().post(
            "/api/review",
            json={"pgn": PGN},
            headers={
                "X-Internal-Secret": _secret(),
                "X-Clerk-User-Id": TEST_CLERK_ID,
            },
        )
    finally:
        (
            review_module.GameAnalyzer,
            review_module.get_review_stockfish,
            review_module._build_explainer,
        ) = saved

    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, list) and payload, response.text
    assert payload[0]["san"] == "Start", response.text
    print("  [PASS] valid secret + X-Clerk-User-Id -> 200 (engine mocked)")


def _long_pgn(plies: int) -> str:
    import chess
    import chess.pgn

    board = chess.Board()
    cycle = ["g1f3", "g8f6", "f3g1", "f6g8"]
    for index in range(plies):
        board.push(chess.Move.from_uci(cycle[index % len(cycle)]))
    return str(chess.pgn.Game.from_board(board))


def test_overlong_game_is_rejected_before_analysis():
    import routers.review as review_module

    called = False

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            pass

        def analyze_full_game(self, pgn, target_color="both"):
            nonlocal called
            called = True
            return [_STUB_ROW]

    saved = (
        review_module.GameAnalyzer,
        review_module.get_review_stockfish,
        review_module._build_explainer,
    )
    review_module.GameAnalyzer = _StubAnalyzer
    review_module.get_review_stockfish = lambda *args, **kwargs: object()
    review_module._build_explainer = lambda: None
    try:
        response = _client().post(
            "/api/review",
            json={"pgn": _long_pgn(158)},
            headers={
                "X-Internal-Secret": _secret(),
                "X-Clerk-User-Id": TEST_CLERK_ID,
            },
        )
    finally:
        (
            review_module.GameAnalyzer,
            review_module.get_review_stockfish,
            review_module._build_explainer,
        ) = saved

    assert response.status_code == 400, response.text
    assert "too long" in response.json()["detail"], response.text
    assert called is False, "overlong game reached the analyzer"
    print("  [PASS] 158-ply game -> 400 before any engine work (cap 157)")


def test_capabilities_reports_flag_and_mode():
    secret = _secret()
    anonymous = _client().get(
        "/api/review/capabilities", headers={"X-Internal-Secret": secret}
    )
    assert anonymous.status_code == 400, anonymous.text

    response = _client().get(
        "/api/review/capabilities",
        headers={"X-Internal-Secret": secret, "X-Clerk-User-Id": TEST_CLERK_ID},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sandbox_enabled"] is False, body
    mode = body["mode"]
    assert "multipv=2" in mode and "classifier=" in mode and "engine=" in mode, mode
    print(f"  [PASS] capabilities -> sandbox_enabled=False, mode={mode}")


def run() -> int:
    print("=== Running review route auth tests ===")
    tests = [
        test_missing_internal_secret_is_rejected,
        test_missing_clerk_user_is_rejected,
        test_missing_header_rejects_before_the_user_limiter,
        test_valid_secret_and_header_pass_the_auth_gate,
        test_overlong_game_is_rejected_before_analysis,
        test_capabilities_reports_flag_and_mode,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"  [FAIL] {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
    print(f"  {len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
