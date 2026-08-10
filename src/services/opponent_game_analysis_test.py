"""
Test harness for services/opponent_game_analysis.py.

Four scenarios (per spec):

  1. PARTIAL RUN — some games already have opponent_game_analysis rows
     before the worker starts; confirm only the remainder gets processed.

  2. ZERO-BLUNDER GAME — confirm a zero-blunder game still gets an
     opponent_game_analysis row and correctly isn't reprocessed on the
     next trigger.

  3. STALE RECLAIM — a job row with status=running and heartbeat_at set
     to 10 minutes ago; confirm a new trigger reclaims and restarts it
     rather than no-op'ing forever.

  4. FRESH RUNNING JOB — heartbeat_at set to 30 seconds ago; confirm a
     new trigger correctly no-ops instead of double-running.

We use a scripted fake pool/conn/cursor that routes SQL by substring
matching to handler functions.  Each handler reads/writes a shared
``state`` dict — this gives us stateful behaviour (e.g. the unanalyzed-
games query returns fewer rows after a game's analysis row is inserted)
without a real database.

The mock analyzer returns canned classification rows so no real Stockfish
is needed.

Run:
    cd src && ../venv/bin/python services/opponent_game_analysis_test.py
"""
import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
import chess.pgn

import services.opponent_game_analysis as mod
from core import database


# ---------------------------------------------------------------------------
# Fake pool / conn / cursor infrastructure
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Cursor that routes execute() calls to scripted handlers.

    Each handler is a (matcher_fn, handler_fn) tuple.  matcher_fn(sql)
    returns True if the handler should handle this SQL.  handler_fn(sql,
    params, state) returns (fetchone, fetchall, rowcount).
    """

    def __init__(self, state, handlers):
        self._state = state
        self._handlers = handlers
        self._fetchone = None
        self._fetchall = []
        self._rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self._state["executed"].append((sql, params))
        for matcher, handler in self._handlers:
            if matcher(sql):
                result = handler(sql, params, self._state)
                if isinstance(result, tuple) and len(result) == 3:
                    fetchone, fetchall, rowcount = result
                else:
                    fetchone, fetchall, rowcount = result, [], 0
                self._fetchone = fetchone
                self._fetchall = fetchall or []
                self._rowcount = rowcount if rowcount is not None else 0
                return
        # No handler matched — default no-op
        self._fetchone = None
        self._fetchall = []
        self._rowcount = 0

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return list(self._fetchall)

    @property
    def rowcount(self):
        return self._rowcount


class _FakeConn:
    def __init__(self, state, handlers):
        self._state = state
        self._handlers = handlers

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._state, self._handlers)

    def commit(self):
        self._state["commits"] += 1

    def rollback(self):
        self._state["rollbacks"] += 1


class _FakePool:
    def __init__(self, state, handlers):
        self._state = state
        self._handlers = handlers

    def getconn(self):
        return _FakeConn(self._state, self._handlers)

    def putconn(self, conn):
        pass


def _matches(*substrings):
    """Build a matcher that checks all substrings are in the SQL."""
    def matcher(sql):
        return all(s in sql for s in substrings)
    return matcher


# ---------------------------------------------------------------------------
# Handler builders — each returns (matcher, handler) for a specific query.
# ---------------------------------------------------------------------------

def _h_select_for_update(state_key="job_row"):
    """SELECT ... FROM opponent_analysis_jobs ... FOR UPDATE"""
    def handler(sql, params, st):
        row = st.get(state_key)
        return (dict(row) if row else None, [], 1 if row else 0)
    return (_matches("opponent_analysis_jobs", "FOR UPDATE"), handler)


def _h_insert_job():
    """INSERT INTO opponent_analysis_jobs ... RETURNING"""
    def handler(sql, params, st):
        st["job_inserted"] = True
        row = st.get("new_job_row") or {
            "id": "new-job-id",
            "status": "idle",
            "heartbeat_at": None,
            "analyzed_games": 0,
            "total_games": 0,
        }
        return (dict(row), [], 1)
    return (_matches("INSERT INTO opponent_analysis_jobs"), handler)


def _h_update_job_running():
    """UPDATE opponent_analysis_jobs SET status = 'running'..."""
    def handler(sql, params, st):
        st["job_updated_to_running"] = True
        st["job_update_params"] = params
        return (None, [], 1)
    return (_matches("UPDATE opponent_analysis_jobs", "running"), handler)


def _h_update_heartbeat():
    """UPDATE opponent_analysis_jobs SET heartbeat_at = NOW()..."""
    def handler(sql, params, st):
        st["heartbeat_updates"] = st.get("heartbeat_updates", 0) + 1
        return (None, [], 1)
    return (_matches("UPDATE opponent_analysis_jobs", "heartbeat_at = NOW()"), handler)


def _h_update_status():
    """UPDATE opponent_analysis_jobs SET status = %s"""
    def handler(sql, params, st):
        st["status_update"] = params[0] if params else None
        return (None, [], 1)
    return (_matches("UPDATE opponent_analysis_jobs", "SET status = %s"), handler)


def _h_unanalyzed_stateful():
    """SELECT g.id::text FROM opponent_games g ... NOT EXISTS ...
    Stateful: returns only games NOT in state['analyzed']."""
    def handler(sql, params, st):
        all_games = st.get("all_game_ids", [])
        analyzed = st.get("analyzed", set())
        unanalyzed = [gid for gid in all_games if gid not in analyzed]
        return (None, [{"game_id": gid} for gid in unanalyzed], len(unanalyzed))
    return (_matches("NOT EXISTS", "opponent_games"), handler)


def _h_unanalyzed_from_state():
    """Same query but returns ids from state['unanalyzed_ids']."""
    def handler(sql, params, st):
        ids = st.get("unanalyzed_ids", [])
        return (None, [{"game_id": gid} for gid in ids], len(ids))
    return (_matches("NOT EXISTS", "opponent_games"), handler)


def _h_select_now_from_state():
    """SELECT NOW() — returns state['now']."""
    def handler(sql, params, st):
        return ((st.get("now"),), [], 1)
    return (_matches("SELECT NOW()"), handler)


def _h_count_from_state():
    """SELECT COUNT(*) — returns state['analyzed_count']."""
    def handler(sql, params, st):
        return ((st.get("analyzed_count", 0),), [], 1)
    return (_matches("COUNT", "opponent_game_analysis"), handler)


def _h_game_data(games_dict):
    """SELECT pgn, white_player, black_player FROM opponent_games WHERE id = ..."""
    def handler(sql, params, st):
        game_id = params[0] if params else None
        game = games_dict.get(game_id)
        return (dict(game) if game else None, [], 1 if game else 0)
    return (_matches("FROM opponent_games", "WHERE id ="), handler)


def _h_insert_analysis():
    """INSERT INTO opponent_game_analysis ... RETURNING id::text
    Tracks the game_id in state['analyzed']."""
    def handler(sql, params, st):
        # params: (requested_by_user_id, provider, opponent_username, game_id)
        game_id = params[3] if len(params) > 3 else None
        if game_id:
            st.setdefault("analyzed", set()).add(game_id)
            st.setdefault("analyzed_game_ids", []).append(game_id)
        analysis_id = f"analysis-{game_id}"
        st.setdefault("analysis_rows", []).append({
            "game_id": game_id,
            "status": "analyzed",
        })
        return ((analysis_id,), [], 1)
    return (_matches("INSERT INTO opponent_game_analysis"), handler)


def _h_delete_blunders():
    """DELETE FROM opponent_game_blunders WHERE game_id = ..."""
    def handler(sql, params, st):
        st.setdefault("blunder_deletes", []).append(params[0] if params else None)
        return (None, [], 0)
    return (_matches("DELETE FROM opponent_game_blunders"), handler)


def _h_insert_blunders():
    """INSERT INTO opponent_game_blunders ..."""
    def handler(sql, params, st):
        # params: (req_by, provider, opp, game_id, analysis_id, fen,
        #           position_key, move_number, move_san, classification, cp_loss)
        st.setdefault("blunder_inserts", []).append({
            "game_id": params[3] if len(params) > 3 else None,
            "analysis_id": params[4] if len(params) > 4 else None,
            "fen": params[5] if len(params) > 5 else None,
            "position_key": params[6] if len(params) > 6 else None,
            "move_number": params[7] if len(params) > 7 else None,
            "move_san": params[8] if len(params) > 8 else None,
            "classification": params[9] if len(params) > 9 else None,
            "centipawn_loss": params[10] if len(params) > 10 else None,
        })
        return (None, [], 1)
    return (_matches("INSERT INTO opponent_game_blunders"), handler)


def _h_select_job_status():
    """SELECT ... FROM opponent_analysis_jobs (no FOR UPDATE) — for GET endpoint."""
    def handler(sql, params, st):
        row = st.get("job_row")
        return (dict(row) if row else None, [], 1 if row else 0)
    return (_matches("FROM opponent_analysis_jobs", "provider", "opponent_username"), handler)


# ---------------------------------------------------------------------------
# PGN + mock analyzer helpers
# ---------------------------------------------------------------------------

OPP_NAME = "TestOpponent"
OTHER_NAME = "TestRival"


def _make_pgn(opp_plays_white, moves_san):
    """Build a minimal valid PGN with the opponent as White or Black."""
    game = chess.pgn.Game()
    game.headers["White"] = OPP_NAME if opp_plays_white else OTHER_NAME
    game.headers["Black"] = OTHER_NAME if opp_plays_white else OPP_NAME
    game.headers["Result"] = "*"
    board = game.board()
    node = game
    for san in moves_san:
        move = board.parse_san(san)
        node = node.add_main_variation(move)
        board.push(move)
    exporter = chess.pgn.StringExporter(
        headers=True, variations=False, comments=False
    )
    return game.accept(exporter)


_QUIET_MOVES = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O", "Nf6", "d3", "d6"]


def _make_quiet_pgn(opp_plays_white=True):
    return _make_pgn(opp_plays_white, _QUIET_MOVES)


def _make_player_dict(name):
    return {"username": name, "rating": 1500}


def _make_game_data(game_id, opp_plays_white=True):
    pgn = _make_quiet_pgn(opp_plays_white)
    return {
        "pgn": pgn,
        "white_player": _make_player_dict(
            OPP_NAME if opp_plays_white else OTHER_NAME
        ),
        "black_player": _make_player_dict(
            OTHER_NAME if opp_plays_white else OPP_NAME
        ),
    }


def _make_review_row(san, classification, cp_loss, move_number,
                     color="white", fen_before=None, fen_after=None):
    fb = fen_before or chess.STARTING_FEN
    fa = fen_after or chess.STARTING_FEN
    return {
        "fen": fa,
        "fen_before": fb,
        "san": san,
        "color": color,
        "classification": classification,
        "cp_loss": cp_loss,
        "move_number": move_number,
    }


class _MockAnalyzer:
    """Mock GameAnalyzer that returns canned classification rows.

    ``results_fn`` is called with the PGN and should return a list of
    review-row dicts (same shape as GameAnalyzer.analyze_full_game).
    """

    def __init__(self, results_fn):
        self._results_fn = results_fn
        self.calls = []

    def analyze_full_game(self, pgn, target_color="both",
                          include_explanations=True):
        self.calls.append({"pgn": pgn, "target_color": target_color})
        return self._results_fn(pgn)


# ---------------------------------------------------------------------------
# Shared setup helpers
# ---------------------------------------------------------------------------

def _install_fake_pool(state, handlers):
    """Replace database.connection_pool with a fake and return the original."""
    original = database.connection_pool
    pool = _FakePool(state, handlers)
    database.connection_pool = pool
    mod.database.connection_pool = pool
    return original


def _restore_pool(original):
    database.connection_pool = original
    mod.database.connection_pool = original


def _default_state():
    return {
        "executed": [],
        "commits": 0,
        "rollbacks": 0,
        "analyzed": set(),
    }


def _worker_handlers(games_dict):
    """Handlers for the worker function (run_opponent_game_analysis)."""
    return [
        _h_unanalyzed_stateful(),
        _h_game_data(games_dict),
        _h_insert_analysis(),
        _h_delete_blunders(),
        _h_insert_blunders(),
        _h_update_heartbeat(),
        _h_update_status(),
        _h_count_from_state(),
    ]


def _trigger_handlers():
    """Handlers for the trigger function (try_start_opponent_analysis).
    All values are read from the shared state dict (job_row, now,
    unanalyzed_ids, analyzed_count) — set by each test before calling."""
    return [
        _h_select_for_update(),
        _h_insert_job(),
        _h_unanalyzed_from_state(),
        _h_count_from_state(),
        _h_select_now_from_state(),
        _h_update_job_running(),
    ]


# ---------------------------------------------------------------------------
# Test 1: PARTIAL RUN
# ---------------------------------------------------------------------------

def test_partial_run():
    """Some games already have opponent_game_analysis rows; confirm only
    the remainder gets processed.

    Setup: 4 games imported (game-1..game-4).  game-1 and game-2 are
    already analyzed (in state['analyzed']).  The worker should process
    only game-3 and game-4.

    game-3: mock analyzer returns 1 blunder → 1 opponent_game_blunders
            row + 1 opponent_game_analysis row (status=analyzed).
    game-4: mock analyzer returns 0 blunders → 0 blunder rows + 1
            opponent_game_analysis row (status=analyzed).

    Assertions:
      - analyzed_game_ids == ['game-3', 'game-4'] (NOT game-1, game-2)
      - blunder_inserts has exactly 1 row (for game-3)
      - heartbeat_updates == 2 (once per processed game)
      - status_update == 'complete' (after all unanalyzed games done)
    """
    print("\n=== TEST 1: PARTIAL RUN (4 games, 2 already analyzed) ===")

    state = _default_state()
    # game-1 and game-2 are already analyzed
    state["analyzed"] = {"game-1", "game-2"}
    state["all_game_ids"] = ["game-1", "game-2", "game-3", "game-4"]

    games_dict = {
        "game-3": _make_game_data("game-3", opp_plays_white=True),
        "game-4": _make_game_data("game-4", opp_plays_white=True),
    }

    def results_fn(pgn):
        # mock.calls is appended BEFORE results_fn is called, so:
        #   first call: len(mock.calls) == 1
        #   second call: len(mock.calls) == 2
        if len(mock.calls) == 1:
            # First call = game-3
            return [
                _make_review_row("e4", "book", 0, 1),
                _make_review_row("Nxe5", "blunder", 500, 3),
            ]
        else:
            # Second call = game-4
            return [
                _make_review_row("e4", "book", 0, 1),
                _make_review_row("Nf3", "best", 0, 2),
            ]

    mock = _MockAnalyzer(results_fn)
    handlers = _worker_handlers(games_dict)
    original = _install_fake_pool(state, handlers)

    try:
        mod.run_opponent_game_analysis(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username=OPP_NAME,
            _analyzer=mock,
        )
    finally:
        _restore_pool(original)

    # Assertions
    analyzed_game_ids = state.get("analyzed_game_ids", [])
    assert analyzed_game_ids == ["game-3", "game-4"], (
        f"Partial run: expected only game-3 and game-4 to be analyzed, "
        f"got {analyzed_game_ids} — already-analyzed games should be skipped"
    )
    print(f"  [PASS] Only game-3 and game-4 processed (game-1, game-2 skipped)")

    blunder_inserts = state.get("blunder_inserts", [])
    assert len(blunder_inserts) == 1, (
        f"Partial run: expected 1 blunder insert (from game-3), "
        f"got {len(blunder_inserts)}"
    )
    assert blunder_inserts[0]["game_id"] == "game-3", (
        f"Partial run: blunder should be from game-3, "
        f"got {blunder_inserts[0]['game_id']}"
    )
    assert blunder_inserts[0]["classification"] == "blunder", (
        f"Partial run: blunder classification should be 'blunder', "
        f"got {blunder_inserts[0]['classification']}"
    )
    print(f"  [PASS] 1 blunder row inserted (game-3, classification=blunder)")

    heartbeat_updates = state.get("heartbeat_updates", 0)
    assert heartbeat_updates == 2, (
        f"Partial run: expected 2 heartbeat updates (1 per processed game), "
        f"got {heartbeat_updates}"
    )
    print(f"  [PASS] 2 heartbeat updates (1 per processed game)")

    status_update = state.get("status_update")
    assert status_update == "complete", (
        f"Partial run: expected status='complete' at end, "
        f"got {status_update}"
    )
    print(f"  [PASS] Job status set to 'complete' after all games processed")

    # Zero-blunder game (game-4) still got an analysis row
    assert "game-4" in state.get("analyzed", set()), (
        "Partial run: game-4 (zero blunders) should be in analyzed set"
    )
    print(f"  [PASS] Zero-blunder game-4 got an analysis row (not skipped)")


# ---------------------------------------------------------------------------
# Test 2: ZERO-BLUNDER GAME REPROCESSING
# ---------------------------------------------------------------------------

def test_zero_blunder_reprocessing():
    """Confirm a zero-blunder game gets an opponent_game_analysis row and
    correctly isn't reprocessed on the next trigger.

    First worker call: 1 unanalyzed game (game-1), mock returns 0
    blunders.  The worker should write an opponent_game_analysis row
    (status=analyzed) and set status=complete.

    Second worker call: the unanalyzed query should return [] (game-1
    is now in the analyzed set), so the worker immediately sets
    status=complete and returns without calling the analyzer.

    Assertion: the mock analyzer is called exactly once (not twice) —
    the zero-blunder game is not re-analyzed.
    """
    print("\n=== TEST 2: ZERO-BLUNDER GAME REPROCESSING ===")

    state = _default_state()
    state["analyzed"] = set()
    state["all_game_ids"] = ["game-1"]

    games_dict = {
        "game-1": _make_game_data("game-1", opp_plays_white=True),
    }

    def results_fn(pgn):
        # Return zero blunders — only "best" and "book" moves
        return [
            _make_review_row("e4", "book", 0, 1),
            _make_review_row("e5", "best", 0, 1),
            _make_review_row("Nf3", "best", 0, 2),
            _make_review_row("Nc6", "best", 0, 2),
        ]

    mock = _MockAnalyzer(results_fn)
    handlers = _worker_handlers(games_dict)
    original = _install_fake_pool(state, handlers)

    try:
        # First run: game-1 is unanalyzed
        mod.run_opponent_game_analysis(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username=OPP_NAME,
            _analyzer=mock,
        )

        assert len(mock.calls) == 1, (
            f"Zero-blunder: first run should call analyzer once, "
            f"got {len(mock.calls)} calls"
        )
        assert "game-1" in state["analyzed"], (
            "Zero-blunder: game-1 should be in analyzed set after first run"
        )
        assert state.get("status_update") == "complete", (
            f"Zero-blunder: status should be 'complete' after first run, "
            f"got {state.get('status_update')}"
        )
        print(f"  [PASS] First run: game-1 analyzed (0 blunders), status=complete")

        # Reset status_update for the second run
        state.pop("status_update", None)

        # Second run: game-1 is now analyzed → should be a no-op
        mod.run_opponent_game_analysis(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username=OPP_NAME,
            _analyzer=mock,
        )

        assert len(mock.calls) == 1, (
            f"Zero-blunder: second run should NOT call analyzer (game-1 "
            f"is already analyzed), but analyzer was called "
            f"{len(mock.calls)} times total — the zero-blunder game is "
            f"being re-analyzed"
        )
        assert state.get("status_update") == "complete", (
            f"Zero-blunder: second run should set status=complete "
            f"(no unanalyzed games), got {state.get('status_update')}"
        )
        print(f"  [PASS] Second run: analyzer NOT called (game-1 skipped), "
              f"status=complete (no reprocessing)")

        # Verify the analysis_rows list has exactly 1 entry (from the first run)
        analysis_rows = state.get("analysis_rows", [])
        assert len(analysis_rows) == 1, (
            f"Zero-blunder: expected 1 analysis row (from first run), "
            f"got {len(analysis_rows)} — the second run should not have "
            f"inserted another analysis row"
        )
        print(f"  [PASS] Only 1 analysis row in total (no duplicate from re-run)")
    finally:
        _restore_pool(original)


# ---------------------------------------------------------------------------
# Test 3: STALE RECLAIM
# ---------------------------------------------------------------------------

def test_stale_reclaim():
    """A job row with status=running and heartbeat_at set to 10 minutes
    ago; confirm a new trigger reclaims and restarts it.

    The trigger should:
      - detect the stale heartbeat (age=600s > 300s threshold)
      - flip status→running, started_at=NOW(), heartbeat_at=NOW()
      - return should_run=True
    """
    print("\n=== TEST 3: STALE RECLAIM (heartbeat 10 min ago) ===")

    now = datetime.datetime(2026, 8, 10, 12, 0, 0)
    ten_min_ago = datetime.datetime(2026, 8, 10, 11, 50, 0)

    job_row = {
        "id": "job-1",
        "status": "running",
        "heartbeat_at": ten_min_ago,
        "analyzed_games": 30,
        "total_games": 100,
    }

    state = _default_state()
    state["job_row"] = job_row
    state["now"] = now
    state["unanalyzed_ids"] = ["game-31", "game-32"]
    state["analyzed_count"] = 30
    handlers = _trigger_handlers()
    original = _install_fake_pool(state, handlers)

    try:
        result = mod.try_start_opponent_analysis(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username=OPP_NAME,
        )

        assert result["should_run"] is True, (
            f"Stale reclaim: should_run should be True (stale heartbeat "
            f"should be reclaimed), got should_run={result['should_run']} "
            f"— reason={result['reason']}"
        )
        assert result["unanalyzed_count"] == 2, (
            f"Stale reclaim: unanalyzed_count should be 2, "
            f"got {result['unanalyzed_count']}"
        )
        print(f"  [PASS] should_run=True (stale job reclaimed), "
              f"reason={result['reason']}")

        assert state.get("job_updated_to_running") is True, (
            "Stale reclaim: job row should be updated to running"
        )
        print(f"  [PASS] Job row updated to running (reclaimed)")

        # Verify the UPDATE params: total_games and analyzed_games
        update_params = state.get("job_update_params")
        assert update_params is not None, (
            "Stale reclaim: UPDATE params should be recorded"
        )
        # params: (total_games, analyzed_games, id)
        assert update_params[0] == 32, (
            f"Stale reclaim: total_games should be 32 (30 analyzed + 2 "
            f"unanalyzed), got {update_params[0]}"
        )
        assert update_params[1] == 30, (
            f"Stale reclaim: analyzed_games should be 30, "
            f"got {update_params[1]}"
        )
        print(f"  [PASS] UPDATE params correct: total_games=32, "
              f"analyzed_games=30")
    finally:
        _restore_pool(original)


# ---------------------------------------------------------------------------
# Test 4: FRESH RUNNING JOB
# ---------------------------------------------------------------------------

def test_fresh_running_job():
    """A job row with status=running and heartbeat_at set to 30 seconds
    ago; confirm a new trigger correctly no-ops instead of double-running.

    The trigger should:
      - detect the fresh heartbeat (age=30s < 300s threshold)
      - NOT update the job row
      - return should_run=False
    """
    print("\n=== TEST 4: FRESH RUNNING JOB (heartbeat 30 sec ago) ===")

    now = datetime.datetime(2026, 8, 10, 12, 0, 0)
    thirty_sec_ago = datetime.datetime(2026, 8, 10, 11, 59, 30)

    job_row = {
        "id": "job-1",
        "status": "running",
        "heartbeat_at": thirty_sec_ago,
        "analyzed_games": 30,
        "total_games": 100,
    }

    state = _default_state()
    state["job_row"] = job_row
    state["now"] = now
    state["unanalyzed_ids"] = ["game-31", "game-32"]
    state["analyzed_count"] = 30
    handlers = _trigger_handlers()
    original = _install_fake_pool(state, handlers)

    try:
        result = mod.try_start_opponent_analysis(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username=OPP_NAME,
        )

        assert result["should_run"] is False, (
            f"Fresh running job: should_run should be False (heartbeat "
            f"is fresh, another run is in progress), got "
            f"should_run={result['should_run']}"
        )
        assert result["reason"] == "fresh running job", (
            f"Fresh running job: reason should be 'fresh running job', "
            f"got {result['reason']}"
        )
        print(f"  [PASS] should_run=False (fresh heartbeat, no double-run), "
              f"reason={result['reason']}")

        assert state.get("job_updated_to_running") is not True, (
            "Fresh running job: job row should NOT be updated (no-op)"
        )
        print(f"  [PASS] Job row NOT updated (correctly no-op'd)")

        assert state.get("commits", 0) >= 1, (
            "Fresh running job: the transaction should still commit "
            "(releasing the FOR UPDATE lock) even on no-op"
        )
        print(f"  [PASS] Transaction committed (FOR UPDATE lock released)")
    finally:
        _restore_pool(original)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("opponent_game_analysis test harness")
    print(f"HEARTBEAT_STALE_SECONDS = {mod.HEARTBEAT_STALE_SECONDS}")

    test_partial_run()
    test_zero_blunder_reprocessing()
    test_stale_reclaim()
    test_fresh_running_job()

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
