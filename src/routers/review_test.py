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
from unittest.mock import patch

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

        def analyze_full_game(self, pgn, target_color="both", **kwargs):
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


def _post_with_stub(pgn: str, clerk_id: str = TEST_CLERK_ID):
    import routers.review as review_module

    state = {"called": False}

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            pass

        def analyze_full_game(self, pgn, target_color="both", **kwargs):
            state["called"] = True
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
            json={"pgn": pgn},
            headers={
                "X-Internal-Secret": _secret(),
                "X-Clerk-User-Id": clerk_id,
            },
        )
    finally:
        (
            review_module.GameAnalyzer,
            review_module.get_review_stockfish,
            review_module._build_explainer,
        ) = saved
    return response, state


def test_flag_off_has_no_ply_cap():
    response, state = _post_with_stub(_long_pgn(158))
    assert response.status_code == 200, response.text
    assert state["called"] is True
    print("  [PASS] flag off: 158-ply game accepted (old behavior, no cap)")


def test_deterministic_cap_placeholder_without_container_nps():
    with patch.dict(
        os.environ,
        {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "100000"},
        clear=False,
    ):
        os.environ.pop("REVIEW_CONTAINER_NPS", None)
        response, state = _post_with_stub(_long_pgn(158))
    assert response.status_code == 400, response.text
    assert "too long" in response.json()["detail"], response.text
    assert state["called"] is False
    print("  [PASS] flag on, no nps: 158 > gate p99 157 -> 400")


def test_deterministic_cap_comes_from_container_nps():
    with patch.dict(
        os.environ,
        {
            "REVIEW_DETERMINISTIC": "1",
            "REVIEW_NODES": "100000",
            "REVIEW_CONTAINER_NPS": "220000",
        },
        clear=False,
    ):
        response, state = _post_with_stub(_long_pgn(158))
    assert response.status_code == 200, response.text
    assert state["called"] is True

    with patch.dict(
        os.environ,
        {
            "REVIEW_DETERMINISTIC": "1",
            "REVIEW_NODES": "100000",
            "REVIEW_CONTAINER_NPS": "100000",
        },
        clear=False,
    ):
        response, state = _post_with_stub(_long_pgn(158))
    assert response.status_code == 400, response.text
    assert "max 119" in response.json()["detail"], response.text
    print("  [PASS] flag on: cap = budget * nps / N - 1 (263 -> 200, 119 -> 400)")


_EXTRAS_KEYS = (
    "fen_before",
    "player_rating",
    "raw_ep_loss",
    "second_best_cp",
    "second_best_move_uci",
    "second_best_move_san",
    "second_best_pv_uci",
)


def test_extras_are_flag_gated():
    # Calls the route function directly: the HTTP suite shares one TestClient
    # IP with a 5/min limiter, so two more POSTs would 429 for reasons
    # unrelated to this test.
    import routers.review as review_module
    from schemas.review_schemas import ReviewRequest

    row_with_extras = dict(
        _STUB_ROW,
        fen_before="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        player_rating=1500,
        raw_ep_loss=0.0123,
        second_best_cp=10.0,
        second_best_move_uci="g1f3",
        second_best_move_san="Nf3",
        second_best_pv_uci=["g1f3"],
    )
    state = {}

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            pass

        def analyze_full_game(self, pgn, target_color="both", **kwargs):
            state["include_extras"] = kwargs.get("include_extras", False)
            row = dict(row_with_extras)
            if not kwargs.get("include_extras", False):
                for key in (
                    "fen_before",
                    "player_rating",
                    "raw_ep_loss",
                    "second_best_cp",
                    "second_best_move_uci",
                    "second_best_move_san",
                    "second_best_pv_uci",
                ):
                    row.pop(key, None)
            return [row]

    saved = (
        review_module.GameAnalyzer,
        review_module.get_review_stockfish,
        review_module._build_explainer,
    )
    review_module.GameAnalyzer = _StubAnalyzer
    review_module.get_review_stockfish = lambda *args, **kwargs: object()
    review_module._build_explainer = lambda: None
    try:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REVIEW_DETERMINISTIC", None)
            rows = review_module.review_game(
                ReviewRequest(pgn=PGN),
                _clerk_id="extras-off-user",
                _ip=None,
                _user=None,
            )
        assert state["include_extras"] is False, state
        absent = [key for key in _EXTRAS_KEYS if key in rows[0]]
        assert not absent, f"flag off leaked extras: {absent} in {rows[0]}"

        with patch.dict(os.environ, {"REVIEW_DETERMINISTIC": "1"}, clear=False):
            rows = review_module.review_game(
                ReviewRequest(pgn=PGN),
                _clerk_id="extras-on-user",
                _ip=None,
                _user=None,
            )
        assert state["include_extras"] is True, state
        assert rows[0]["second_best_move_uci"] == "g1f3", rows[0]
        missing = [key for key in _EXTRAS_KEYS if key not in rows[0]]
        assert not missing, f"flag on dropped extras: {missing} in {rows[0]}"
    finally:
        (
            review_module.GameAnalyzer,
            review_module.get_review_stockfish,
            review_module._build_explainer,
        ) = saved
    print("  [PASS] extras/suggestion fields only flow when the flag is on")


_LIVE_FEN = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


def _stub_live(monkeypatch_calls):
    import chess

    from schemas.models import Evaluation

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            self.calls = 0

        def evaluate_position(self, board):
            self.calls += 1
            monkeypatch_calls.append(self.calls)
            return Evaluation(
                score_cp=20.0,
                best_move_uci="g1f3",
                best_move_san="Nf3",
                mate=None,
                second_best_cp=10.0,
                principal_variation_uci=["g1f3", "g8f6"],
                second_best_move_uci="d2d4",
                second_best_move_san="d4",
                second_best_pv_uci=["d2d4", "g8f6"],
            )

        def analyze_ply(self, board, move, eval_before, **kwargs):
            row = {
                "classification": "best",
                "cp_loss": 0,
                "ep_loss": 0.01,
                "eval_cp": 12.0,
                "eval_mate": None,
                "color": "white",
                "fen_before": board.fen(),
                "fen": "after",
                "san": board.san(move),
            }
            return row, eval_before, 0.01

    return _StubAnalyzer


def test_live_requires_login_and_flag():
    response = _client().post(
        "/api/review/live",
        json={"fen": _LIVE_FEN, "move": "g1f3"},
        headers={"X-Internal-Secret": _secret()},
    )
    assert response.status_code == 400, response.text

    response = _client().post(
        "/api/review/live",
        json={"fen": _LIVE_FEN, "move": "g1f3"},
        headers={
            "X-Internal-Secret": _secret(),
            "X-Clerk-User-Id": TEST_CLERK_ID,
        },
    )
    assert response.status_code == 403, response.text
    assert "disabled" in response.json()["detail"], response.text
    print("  [PASS] live: login required; 403 when the flag is off")


def test_live_returns_label_lines_and_caches():
    import routers.review as review_module

    review_module._SANDBOX_EVAL_CACHE.clear()
    review_module._SANDBOX_RESULT_CACHE.clear()
    calls: list = []
    stub = _stub_live(calls)
    saved = (
        review_module.GameAnalyzer,
        review_module.get_review_stockfish,
    )
    review_module.GameAnalyzer = stub
    review_module.get_review_stockfish = lambda *args, **kwargs: object()
    try:
        with patch.dict(
            os.environ,
            {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
            clear=False,
        ):
            headers = {
                "X-Internal-Secret": _secret(),
                "X-Clerk-User-Id": TEST_CLERK_ID,
            }
            first = _client().post(
                "/api/review/live",
                json={"fen": _LIVE_FEN, "move": "Nf3"},
                headers=headers,
            )
            second = _client().post(
                "/api/review/live",
                json={"fen": _LIVE_FEN, "move": "g1f3"},
                headers=headers,
            )
    finally:
        (
            review_module.GameAnalyzer,
            review_module.get_review_stockfish,
        ) = saved

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["classification"] == "best", body
    assert body["move_san"] == "Nf3" and body["move_uci"] == "g1f3", body
    assert body["best"]["pv_san"] == ["Nf3", "Nf6"], body["best"]
    assert body["second_best"]["move_san"] == "d4", body["second_best"]
    assert body["second_best"]["pv_san"] == ["d4", "Nf6"], body["second_best"]
    assert body["cached"] is False, body
    assert second.status_code == 200, second.text
    assert second.json()["cached"] is True, second.text
    assert len(calls) == 1, f"eval ran {len(calls)} times; cache missed"
    print("  [PASS] live: SAN/UCI accepted, label + top-2 lines, cached")


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
        test_flag_off_has_no_ply_cap,
        test_deterministic_cap_placeholder_without_container_nps,
        test_deterministic_cap_comes_from_container_nps,
        test_extras_are_flag_gated,
        test_live_requires_login_and_flag,
        test_live_returns_label_lines_and_caches,
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
