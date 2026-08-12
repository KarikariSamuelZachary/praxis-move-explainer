"""
Test harness for services/opponent_traps.py.

Verifies the four spec cases:
  1. A position_key in 2+ DIFFERENT games → surfaces as a trap.
  2. A position_key appearing twice in the SAME game → does NOT count
     as 2 (game_id dedupe within the group).
  3. A position_key appearing only once total → does NOT surface.
  4. Sort order: traps sorted by game_count descending.

We use a scripted fake pool/conn/cursor (same pattern as
opponent_game_analysis_test.py).  The fake cursor returns raw
``opponent_game_blunders`` rows from a shared ``state`` dict, and
``compute_opponent_traps`` does the grouping/deduping/sorting in
Python — so the test exercises the REAL clustering logic, not a mock
of it.

Run:
    cd src && ../venv/bin/python services/opponent_traps_test.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.opponent_traps as mod
from core import database


# ---------------------------------------------------------------------------
# Fake pool / conn / cursor infrastructure
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, state):
        self._state = state
        self._fetchall = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self._state["executed"].append((sql, params))
        # The traps query selects from opponent_game_blunders.
        if "FROM opponent_game_blunders" in sql:
            self._fetchall = [dict(r) for r in self._state["blunders"]]
            return
        self._fetchall = []

    def fetchall(self):
        return list(self._fetchall)

    @property
    def rowcount(self):
        return len(self._fetchall)


class _FakeConn:
    def __init__(self, state):
        self._state = state

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self._state)

    def commit(self):
        pass

    def rollback(self):
        pass


class _FakePool:
    def __init__(self, state):
        self._state = state

    def getconn(self):
        return _FakeConn(self._state)

    def putconn(self, conn):
        pass


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_compute_opponent_traps():
    state = {
        "executed": [],
        "blunders": [
            # --- posA: 2 different games → qualifies as a trap ---
            {
                "position_key": "posA",
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "move_san": "Nf3",
                "classification": "blunder",
                "game_id": "g1",
                "move_number": 10,
            },
            {
                "position_key": "posA",
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 3",
                "move_san": "Nc3",
                "classification": "mistake",
                "game_id": "g2",
                "move_number": 12,
            },
            # --- posB: twice in the SAME game → does NOT qualify ---
            {
                "position_key": "posB",
                "fen": "fenB1",
                "move_san": "e5",
                "classification": "blunder",
                "game_id": "g1",
                "move_number": 8,
            },
            {
                "position_key": "posB",
                "fen": "fenB2",
                "move_san": "d5",
                "classification": "blunder",
                "game_id": "g1",
                "move_number": 20,
            },
            # --- posC: only once → does NOT qualify ---
            {
                "position_key": "posC",
                "fen": "fenC1",
                "move_san": "Qh5",
                "classification": "blunder",
                "game_id": "g1",
                "move_number": 5,
            },
            # --- posD: 3 different games → qualifies, should sort FIRST ---
            {
                "position_key": "posD",
                "fen": "fenD1",
                "move_san": "Bb5",
                "classification": "blunder",
                "game_id": "g1",
                "move_number": 7,
            },
            {
                "position_key": "posD",
                "fen": "fenD2",
                "move_san": "Bb5",
                "classification": "mistake",
                "game_id": "g2",
                "move_number": 7,
            },
            {
                "position_key": "posD",
                "fen": "fenD3",
                "move_san": "Ba4",
                "classification": "blunder",
                "game_id": "g3",
                "move_number": 9,
            },
        ],
    }

    pool = _FakePool(state)
    database.connection_pool = pool
    conn = pool.getconn()

    traps = mod.compute_opponent_traps(
        conn,
        requested_by_user_id="user-1",
        provider="lichess",
        opponent_username="TestOpp",
    )

    # --- Assertion 1: exactly 2 traps qualify (posA, posD) ---
    assert len(traps) == 2, (
        f"Expected 2 qualifying traps (posA, posD); got {len(traps)}: "
        f"{[t['position_key'] for t in traps]}"
    )
    print("[PASS] Exactly 2 traps qualify (posA and posD)")

    # --- Assertion 2: sort order — posD (3 games) first, posA (2 games) second ---
    assert traps[0]["position_key"] == "posD", (
        f"Expected posD (3 games) to sort first; got {traps[0]['position_key']}"
    )
    assert traps[0]["game_count"] == 3, (
        f"Expected posD game_count=3; got {traps[0]['game_count']}"
    )
    assert traps[1]["position_key"] == "posA", (
        f"Expected posA (2 games) to sort second; got {traps[1]['position_key']}"
    )
    assert traps[1]["game_count"] == 2, (
        f"Expected posA game_count=2; got {traps[1]['game_count']}"
    )
    print("[PASS] Sort order: posD (3 games) first, posA (2 games) second")

    # --- Assertion 3: posB (same game, 2 rows) does NOT surface ---
    pos_keys = {t["position_key"] for t in traps}
    assert "posB" not in pos_keys, (
        "posB had 2 rows but only 1 distinct game_id — should NOT qualify"
    )
    print("[PASS] posB (same game blundering twice) correctly excluded")

    # --- Assertion 4: posC (only 1 row) does NOT surface ---
    assert "posC" not in pos_keys, (
        "posC had only 1 row — should NOT qualify"
    )
    print("[PASS] posC (single occurrence) correctly excluded")

    # --- Assertion 5: posA trap has correct fields ---
    posA = traps[1]
    assert posA["fen"] == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", (
        f"Expected representative fen from first row; got {posA['fen']}"
    )
    assert posA["moves"] == ["Nc3", "Nf3"], (
        f"Expected sorted distinct moves ['Nc3', 'Nf3']; got {posA['moves']}"
    )
    assert posA["classification"] == "blunder", (
        f"Expected worst classification 'blunder'; got {posA['classification']}"
    )
    assert posA["game_count"] == 2, (
        f"Expected game_count=2 (g1, g2); got {posA['game_count']}"
    )
    assert posA["move_number_min"] == 10, (
        f"Expected move_number_min=10; got {posA['move_number_min']}"
    )
    assert posA["move_number_max"] == 12, (
        f"Expected move_number_max=12; got {posA['move_number_max']}"
    )
    assert posA["tier"] == "position", (
        f"Expected tier='position'; got {posA['tier']}"
    )
    print("[PASS] posA trap fields: fen, moves (sorted distinct), "
          "classification (worst=blunder), game_count, move_number range, tier")

    # --- Assertion 6: posD trap has correct fields ---
    posD = traps[0]
    assert posD["moves"] == ["Ba4", "Bb5"], (
        f"Expected sorted distinct moves ['Ba4', 'Bb5']; got {posD['moves']}"
    )
    assert posD["classification"] == "blunder", (
        f"Expected worst classification 'blunder'; got {posD['classification']}"
    )
    assert posD["game_count"] == 3, (
        f"Expected game_count=3 (g1, g2, g3); got {posD['game_count']}"
    )
    assert posD["move_number_min"] == 7, (
        f"Expected move_number_min=7; got {posD['move_number_min']}"
    )
    assert posD["move_number_max"] == 9, (
        f"Expected move_number_max=9; got {posD['move_number_max']}"
    )
    print("[PASS] posD trap fields: moves (sorted distinct), "
          "classification (worst=blunder), game_count=3, move_number range")


def test_empty_blunders():
    """Zero blunder rows → empty list (not an error)."""
    state = {"executed": [], "blunders": []}
    pool = _FakePool(state)
    database.connection_pool = pool
    conn = pool.getconn()

    traps = mod.compute_opponent_traps(
        conn,
        requested_by_user_id="user-1",
        provider="lichess",
        opponent_username="NobodyHere",
    )
    assert traps == [], (
        f"Expected empty list for zero blunders; got {traps}"
    )
    print("[PASS] Zero blunder rows → empty list (not an error)")


def test_all_single_occurrence():
    """Every position appears in only 1 game → empty list."""
    state = {
        "executed": [],
        "blunders": [
            {"position_key": "posX", "fen": "fenX", "move_san": "a4",
             "classification": "blunder", "game_id": "g1", "move_number": 1},
            {"position_key": "posY", "fen": "fenY", "move_san": "h4",
             "classification": "mistake", "game_id": "g2", "move_number": 1},
            {"position_key": "posZ", "fen": "fenZ", "move_san": "e4",
             "classification": "blunder", "game_id": "g3", "move_number": 1},
        ],
    }
    pool = _FakePool(state)
    database.connection_pool = pool
    conn = pool.getconn()

    traps = mod.compute_opponent_traps(
        conn,
        requested_by_user_id="user-1",
        provider="lichess",
        opponent_username="Sparse",
    )
    assert traps == [], (
        f"Expected empty list when all positions are single-occurrence; "
        f"got {len(traps)} traps"
    )
    print("[PASS] All single-occurrence positions → empty list")


if __name__ == "__main__":
    print("=== TEST 1: FULL SCENARIO (dedupe, qualifying, sort) ===")
    test_compute_opponent_traps()
    print()
    print("=== TEST 2: ZERO BLUNDERS ===")
    test_empty_blunders()
    print()
    print("=== TEST 3: ALL SINGLE OCCURRENCE ===")
    test_all_single_occurrence()
    print()
    print("All assertions passed.")