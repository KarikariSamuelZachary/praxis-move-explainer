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
    from core import rate_limit

    # The suite shares one in-process limiter; reset it so each stubbed POST
    # measures routing, not throttling.
    rate_limit._memory_counters.clear()

    state = {"called": False, "include_explanations": None, "include_extras": None}

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            pass

        def analyze_full_game(self, pgn, target_color="both", **kwargs):
            state["called"] = True
            state["include_explanations"] = kwargs.get("include_explanations", True)
            state["include_extras"] = kwargs.get("include_extras", False)
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


def test_review_never_generates_explanations():
    response, state = _post_with_stub(PGN)
    assert response.status_code == 200, response.text
    assert state["include_explanations"] is False, state
    print("  [PASS] review route passes include_explanations=False (AI off)")


def test_live_prewarm_warms_the_position():
    import routers.review as review_module

    review_module._SANDBOX_EVAL_CACHE.clear()
    review_module._SANDBOX_RESULT_CACHE.clear()
    calls = {"n": 0}

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            pass

        def evaluate_position(self, board):
            calls["n"] += 1
            from schemas.models import Evaluation

            return Evaluation(
                score_cp=0.0, best_move_uci="", best_move_san="(none)"
            )

    saved = (review_module.GameAnalyzer, review_module.get_review_stockfish)
    review_module.GameAnalyzer = _StubAnalyzer
    review_module.get_review_stockfish = lambda *args, **kwargs: object()
    headers = {
        "X-Internal-Secret": _secret(),
        "X-Clerk-User-Id": TEST_CLERK_ID,
    }
    try:
        with patch.dict(
            os.environ,
            {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
            clear=False,
        ):
            denied = _client().post(
                "/api/review/live/prewarm",
                json={"moves": ["e4"]},
                headers={"X-Internal-Secret": _secret()},
            )
            assert denied.status_code == 400, denied.text

            first = _client().post(
                "/api/review/live/prewarm",
                json={"moves": ["e4", "e5"]},
                headers=headers,
            )
            second = _client().post(
                "/api/review/live/prewarm",
                json={"moves": ["e4", "e5"]},
                headers=headers,
            )
    finally:
        (review_module.GameAnalyzer, review_module.get_review_stockfish) = saved

    assert first.status_code == 200, first.text
    assert first.json()["cached"] is False, first.text
    assert second.status_code == 200, second.text
    assert second.json()["cached"] is True, second.text
    assert calls["n"] == 1, f"prewarm evaluated {calls['n']} times"
    print("  [PASS] live prewarm caches the position (login required too)")


def test_prewarm_flag_off_is_403_with_login():
    response = _client().post(
        "/api/review/live/prewarm",
        json={"moves": ["e4"]},
        headers={
            "X-Internal-Secret": _secret(),
            "X-Clerk-User-Id": TEST_CLERK_ID,
        },
    )
    assert response.status_code == 403, response.text
    assert "disabled" in response.json()["detail"], response.text
    print("  [PASS] prewarm: login required; 403 when the flag is off")


def test_prewarm_bursts_cannot_429_live_labels():
    import routers.review as review_module

    from core import rate_limit

    # The shared TestClient presents one IP for every test, so start from a
    # clean budget; the finally block restores that for later tests too.
    rate_limit._memory_counters.clear()
    stub = _stub_live([])
    saved = (
        review_module.GameAnalyzer,
        review_module.get_review_stockfish,
    )
    review_module.GameAnalyzer = stub
    review_module.get_review_stockfish = lambda *args, **kwargs: object()
    user = "review-prewarm-isolation-user"
    try:
        with patch.dict(
            os.environ,
            {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
            clear=False,
        ):
            client = _client()
            headers = {
                "X-Internal-Secret": _secret(),
                "X-Clerk-User-Id": user,
            }
            statuses = [
                client.post(
                    "/api/review/live/prewarm",
                    json={"moves": ["e4", "e5"]},
                    headers=headers,
                ).status_code
                for _ in range(31)
            ]
            live = client.post(
                "/api/review/live",
                json={"moves": ["e4", "e5"], "move": "Nf3"},
                headers=headers,
            )
    finally:
        (
            review_module.GameAnalyzer,
            review_module.get_review_stockfish,
        ) = saved
        rate_limit._memory_counters.clear()

    assert statuses[:30] == [200] * 30, statuses
    assert statuses[30] == 429, statuses
    assert live.status_code == 200, live.text
    assert live.json()["classification"] == "best", live.text
    print("  [PASS] 31 prewarms: own IP budget 429s, live labels unaffected")


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

        with patch.dict(
            os.environ,
            {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
            clear=False,
        ):
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

        def expected_points_loss(self, before, after, turn_color, rating):
            return 0.03

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
        json={"moves": ["e4"], "move": "e5"},
        headers={"X-Internal-Secret": _secret()},
    )
    assert response.status_code == 400, response.text

    response = _client().post(
        "/api/review/live",
        json={"moves": ["e4"], "move": "e5"},
        headers={
            "X-Internal-Secret": _secret(),
            "X-Clerk-User-Id": TEST_CLERK_ID,
        },
    )
    assert response.status_code == 403, response.text
    assert "disabled" in response.json()["detail"], response.text
    print("  [PASS] live: login required; 403 when the flag is off")


def test_live_rejects_fen_only_requests():
    with patch.dict(
        os.environ,
        {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
        clear=False,
    ):
        response = _client().post(
            "/api/review/live",
            json={"moves": [], "fen": _LIVE_FEN, "move": "g1f3"},
            headers={
                "X-Internal-Secret": _secret(),
                "X-Clerk-User-Id": TEST_CLERK_ID,
            },
        )
    assert response.status_code == 400, response.text
    assert "moves path" in response.json()["detail"], response.text
    print("  [PASS] live: FEN-only requests rejected (no game context)")


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
                json={"moves": ["e4", "e5"], "move": "Nf3"},
                headers=headers,
            )
            second = _client().post(
                "/api/review/live",
                json={"moves": ["e4", "e5"], "move": "g1f3"},
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
    calls_after_first = len(calls)
    assert second.status_code == 200, second.text
    assert second.json()["cached"] is True, second.text
    assert len(calls) == calls_after_first, "second explore re-ran the engine"
    print("  [PASS] live: SAN/UCI accepted, label + top-2 lines, cached")


def test_live_replays_the_move_path():
    import routers.review as review_module

    review_module._SANDBOX_EVAL_CACHE.clear()
    review_module._SANDBOX_RESULT_CACHE.clear()
    seen: dict = {}

    from schemas.models import Evaluation

    class _StubAnalyzer:
        def __init__(self, **kwargs):
            pass

        def evaluate_position(self, board):
            return Evaluation(
                score_cp=15.0,
                best_move_uci="g1f3",
                best_move_san="Nf3",
                mate=None,
                second_best_cp=5.0,
                principal_variation_uci=["g1f3", "b8c6"],
            )

        def expected_points_loss(self, before, after, turn_color, rating):
            seen["prev_loss_called"] = True
            return 0.03

        def analyze_ply(self, board, move, eval_before, **kwargs):
            seen.update(kwargs)
            seen["fen"] = board.fen()
            row = {
                "classification": "best",
                "cp_loss": 0,
                "ep_loss": 0.01,
                "raw_ep_loss": 0.02,
                "eval_cp": 15.0,
                "eval_mate": None,
                "color": "white",
                "fen_before": board.fen(),
                "fen": "after",
                "san": board.san(move),
            }
            return row, eval_before, 0.02

    saved = (review_module.GameAnalyzer, review_module.get_review_stockfish)
    review_module.GameAnalyzer = _StubAnalyzer
    review_module.get_review_stockfish = lambda *args, **kwargs: object()
    try:
        with patch.dict(
            os.environ,
            {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
            clear=False,
        ):
            response = _client().post(
                "/api/review/live",
                json={
                    "moves": ["e4", "e5"],
                    "move": "Nf3",
                    "expected_mode": None,
                },
                headers={
                    "X-Internal-Secret": _secret(),
                    "X-Clerk-User-Id": TEST_CLERK_ID,
                },
            )
    finally:
        (review_module.GameAnalyzer, review_module.get_review_stockfish) = saved

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["move_san"] == "Nf3", body
    assert body["raw_ep_loss"] == 0.02, body
    assert body["is_book"] is False, body
    assert seen.get("prev_loss_called") is True, seen
    assert seen.get("opponent_prev_ep_loss") == 0.03, seen
    assert seen.get("is_book_move") is False, seen
    print("  [PASS] live: path replayed, prev EP loss + book flag carried")


def test_live_rejects_stale_mode():
    with patch.dict(
        os.environ,
        {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
        clear=False,
    ):
        response = _client().post(
            "/api/review/live",
            json={
                "moves": [],
                "fen": _LIVE_FEN,
                "move": "g1f3",
                "expected_mode": "rev-det-v1|nodes=1|stale",
            },
            headers={
                "X-Internal-Secret": _secret(),
                "X-Clerk-User-Id": TEST_CLERK_ID,
            },
        )
    assert response.status_code == 409, response.text
    assert "Stale review" in response.json()["detail"], response.text
    print("  [PASS] live: mismatched mode -> 409 stale review")


def test_live_terminal_checkmate_and_stalemate():
    import chess

    import routers.review as review_module
    from schemas.models import Evaluation

    review_module._SANDBOX_EVAL_CACHE.clear()
    review_module._SANDBOX_RESULT_CACHE.clear()
    calls = {"n": 0}

    class _FakeEngine:
        def evaluate(
            self,
            board,
            depth_limit=None,
            pov=None,
            time_limit=None,
            multipv=1,
            nodes=None,
            fresh_token=False,
        ):
            calls["n"] += 1
            legal = list(board.legal_moves)
            first = legal[0] if legal else None
            return Evaluation(
                score_cp=0.0,
                best_move_uci=first.uci() if first else "",
                best_move_san=board.san(first) if first else "(none)",
                mate=None,
                second_best_cp=None,
                principal_variation_uci=[first.uci()] if first else [],
            )

    fake_engine = _FakeEngine()
    saved = (
        review_module.GameAnalyzer,
        review_module.get_review_stockfish,
        review_module.get_review_engine_name,
    )
    # Real GameAnalyzer, fake engine: terminal synthesis must skip the
    # after-move evaluation for checkmate and stalemate.
    review_module.get_review_stockfish = lambda *args, **kwargs: fake_engine
    review_module.get_review_engine_name = lambda: "fake"
    headers = {
        "X-Internal-Secret": _secret(),
        "X-Clerk-User-Id": TEST_CLERK_ID,
    }
    try:
        with patch.dict(
            os.environ,
            {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
            clear=False,
        ):
            calls["n"] = 0
            mate = _client().post(
                "/api/review/live",
                json={"moves": ["f3", "e5", "g4"], "move": "Qh4#"},
                headers=headers,
            )
            mate_calls = calls["n"]
            calls["n"] = 0
            stalemate_path = [
                "e3", "a5", "Qh5", "Ra6", "Qxa5", "h5", "Qxc7", "Rah6",
                "h4", "f6", "Qxd7+", "Kf7", "Qxb7", "Qd3", "Qxb8", "Qh7",
                "Qxc8", "Kg6",
            ]
            stale = _client().post(
                "/api/review/live",
                json={"moves": stalemate_path, "move": "Qe6"},
                headers=headers,
            )
            stale_calls = calls["n"]
    finally:
        (
            review_module.GameAnalyzer,
            review_module.get_review_stockfish,
            review_module.get_review_engine_name,
        ) = saved

    assert mate.status_code == 200, mate.text
    mate_body = mate.json()
    assert mate_body["classification"] == "best", mate_body
    assert mate_body["eval_mate"] is not None, mate_body
    # Two calls: eval-before plus the previous-ply EP-loss context. A third
    # would mean the checkmate after-position hit the engine.
    assert mate_calls == 2, f"unexpected engine calls for checkmate ({mate_calls})"

    assert stale.status_code == 200, stale.text
    stale_body = stale.json()
    assert stale_body["eval_cp"] == 0.0, stale_body
    assert stale_body["eval_mate"] is None, stale_body
    # Two calls: eval-before plus the previous-ply EP-loss context. A third
    # would mean the stalemate after-position hit the engine.
    assert stale_calls == 2, f"stalemate after-position was engine-evaluated ({stale_calls})"
    print("  [PASS] live: checkmate + stalemate synthesized, no engine after-eval")


def test_live_nested_variation_matches_fresh_full_path():
    import routers.review as review_module
    from schemas.models import Evaluation

    review_module._SANDBOX_EVAL_CACHE.clear()
    review_module._SANDBOX_RESULT_CACHE.clear()

    class _FakeEngine:
        def evaluate(
            self,
            board,
            depth_limit=None,
            pov=None,
            time_limit=None,
            multipv=1,
            nodes=None,
            fresh_token=False,
        ):
            legal = list(board.legal_moves)
            first = legal[0] if legal else None
            rest = legal[1:]
            return Evaluation(
                score_cp=20.0,
                best_move_uci=first.uci() if first else "",
                best_move_san=board.san(first) if first else "(none)",
                mate=None,
                second_best_cp=10.0,
                principal_variation_uci=[first.uci()] if first else [],
                second_best_move_uci=rest[0].uci() if rest else None,
                second_best_move_san=board.san(rest[0]) if rest else None,
                second_best_pv_uci=[rest[0].uci()] if rest else [],
            )

    saved = (review_module.get_review_stockfish,)
    review_module.get_review_stockfish = lambda *args, **kwargs: _FakeEngine()
    headers = {
        "X-Internal-Secret": _secret(),
        "X-Clerk-User-Id": TEST_CLERK_ID,
    }
    try:
        with patch.dict(
            os.environ,
            {"REVIEW_DETERMINISTIC": "1", "REVIEW_NODES": "150000"},
            clear=False,
        ):
            first = _client().post(
                "/api/review/live",
                json={"moves": ["e4", "e5"], "move": "Bc4"},
                headers=headers,
            )
            second = _client().post(
                "/api/review/live",
                json={"moves": ["e4", "e5", "Bc4"], "move": "Bc5"},
                headers=headers,
            )
    finally:
        (review_module.get_review_stockfish,) = saved

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    from core.game_analyzer import GameAnalyzer
    from llms.mock_explainer import MockExplainer

    analyzer = GameAnalyzer(
        engine=_FakeEngine(),
        explainer=MockExplainer(),
        multipv=2,
        deterministic=True,
    )
    rows = analyzer.analyze_full_game(
        "1. e4 e5 2. Bc4 Bc5", include_explanations=False
    )
    assert rows[3]["classification"] == first.json()["classification"], (
        rows[3],
        first.json(),
    )
    assert rows[4]["classification"] == second.json()["classification"], (
        rows[4],
        second.json(),
    )
    print("  [PASS] live: variation-of-variation matches fresh full-path labels")


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
        test_live_replays_the_move_path,
        test_live_replays_the_move_path,
        test_live_rejects_stale_mode,
        test_live_rejects_fen_only_requests,
        test_live_prewarm_warms_the_position,
        test_prewarm_flag_off_is_403_with_login,
        test_prewarm_bursts_cannot_429_live_labels,
        test_live_terminal_checkmate_and_stalemate,
        test_live_nested_variation_matches_fresh_full_path,
        test_review_never_generates_explanations,
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
