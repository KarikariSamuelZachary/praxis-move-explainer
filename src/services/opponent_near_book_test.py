"""
Live test harness for feature D -- near-book repertoire similarity
(mirror-mode fallback extension).

Verifies two layers:

  A. opponent_repertoire.pick_near_repertoire_moves (the near-book data
     lookup): it must read the SAME recency-weighted repertoire data as
     pick_repertoire_move (a JOIN to opponent_games with the exponential
     decay expression, NOT a raw unweighted COUNT), filter by played_color
     and a +/-NEAR_BOOK_PLY_WINDOW ply window, exclude the live position's
     own exact position_key, and aggregate the recency weights.

  B. opponent_style_reranker.rerank_candidates(near_book_weights=...):
     the near-book multiplier must boost candidates whose move_uci is in
     the map (head-to-head), compose multiplicatively with the existing
     signal stack, degrade to no-op when no near-book data is present, and
     expose transparency fields.

  C. SEQUENCING RULE (structural). D must only run when pick_repertoire_move
     returns no move. This is enforced in the caller's control flow
     (routers/train.py): the near-book lookup + reranker live inside the
     out-of-book branch. The sequencing test below proves the two data
     lookups are structurally separate at the query level -- the exact-book
     query never issues the near-book query, and vice versa -- so reaching D
     requires a separate call the in-book branch never makes.

Run with: cd src && ../venv/bin/python services/opponent_near_book_test.py
"""
import os
import random
import sys
from collections import Counter
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess

from core import database
import services.opponent_repertoire as rep_mod
import services.opponent_style_reranker as reranker_mod
from services.opponent_style_reranker import rerank_candidates


# ---------------------------------------------------------------------------
# Fake DB infrastructure (modeled on opponent_style_reranker_test.py's
# _FakeTrapCursor / _FakeTrapConn pattern, but for the repertoire pickers).
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, state):
        self._state = state
        self._rows: List[Dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self._state["executed"].append((sql, params))
        self._rows = [dict(r) for r in self._state["rows"]]

    def fetchall(self):
        return list(self._rows)


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
    def __init__(self, conn):
        self._conn = conn

    def getconn(self):
        return self._conn

    def putconn(self, conn):
        pass


def _install_fake_pool(state):
    database.connection_pool = _FakePool(_FakeConn(state))


def _uninstall_fake_pool():
    database.connection_pool = None


def _run(style, candidates, board, near_book_weights, n_trials, seed):
    rng = random.Random(seed)
    picks = Counter()
    for _ in range(n_trials):
        result = rerank_candidates(
            candidates=candidates,
            style=style,
            board=board,
            rng=rng,
            near_book_weights=near_book_weights,
        )
        picks[result["chosen_index"]] += 1
    return picks


def _make_style(sufficient=True, sacrifice_frequency=0.0):
    return {
        "sufficient": sufficient,
        "game_count": 12,
        "sacrifice_frequency": sacrifice_frequency,
        "opening_family_lean": {"Sicilian Defense": 1.0} if sufficient else None,
        "queens_stay_on_rate": 0.5,
        "queen_trade_move_number": None,
        "castling_side_distribution": None,
        "average_game_length": None,
    }


# ---------------------------------------------------------------------------
# Test D1: pick_near_repertoire_moves reads RECENCY-WEIGHTED data, not raw
# counts.
#
# The fake rows give every move a raw frequency of 10 but a weighted_frequency
# of 2.5 (a decayed value). The returned map must use 2.5, proving the
# function aggregates the JOIN-with-decay weighted_frequency column rather
# than the raw COUNT. Also verifies the query issues the JOIN + exp() decay
# expression (so the weighting is done in SQL over opponent_games.end_time).
# ---------------------------------------------------------------------------
def test_near_book_reads_weighted_data():
    print("\n=== Test D1: near-book reads recency-weighted data, not raw counts ===")
    board = chess.Board()  # start position, white to move
    state = {
        "executed": [],
        "rows": [
            {"move_uci": "e2e4", "frequency": 10, "weighted_frequency": 2.5},
            {"move_uci": "d2d4", "frequency": 10, "weighted_frequency": 0.0},
        ],
    }
    _install_fake_pool(state)
    try:
        result = rep_mod.pick_near_repertoire_moves(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username="TestOpp",
            board=board,
        )
    finally:
        _uninstall_fake_pool()

    print(f"  returned weights: {result}")
    # e2e4's weight must be the decayed 2.5, not the raw count 10.
    assert result is not None, "near-book map should not be None with rows present"
    assert result["e2e4"] == 2.5, (
        f"e2e4 weight must be weighted_frequency (2.5), not raw frequency "
        f"(10); got {result}"
    )
    # d2d4 had weighted_frequency=0.0 -> dropped from the map (underflow guard).
    assert "d2d4" not in result, (
        f"d2d4 (weighted_frequency=0.0) should be excluded from the map; "
        f"got {result}"
    )

    # The query must carry the JOIN + exponential decay expression, proving
    # the weighting happens in SQL over opponent_games.end_time.
    assert state["executed"], "pick_near_repertoire_moves should issue one query"
    sql = state["executed"][0][0]
    assert "FROM opponent_repertoire_moves" in sql
    assert "JOIN opponent_games" in sql, (
        f"near-book query must JOIN opponent_games to read end_time; got {sql!r}"
    )
    assert "exp(" in sql and "g.end_time" in sql, (
        f"near-book query must apply the recency decay expression; got {sql!r}"
    )
    print(f"  [PASS] weighted_frequency used (2.5), zero-weight move dropped, "
          f"SQL JOIN+decay confirmed")


# ---------------------------------------------------------------------------
# Test D2: ply window + color filter + exact-position exclusion in the query
# params.
# ---------------------------------------------------------------------------
def test_near_book_query_params():
    print("\n=== Test D2: ply window, color filter, exact-key exclusion ===")
    # Black to move after 1.e4: FEN has black to move -> played_color="black",
    # _candidate_ply_index = 1 (0-indexed), window [1-2, 1+2] = [-1, 3].
    board = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
    expected_played_color = "black"
    expected_ply_index = 1
    expected_key = rep_mod._position_key(board)

    state = {"executed": [], "rows": []}
    _install_fake_pool(state)
    try:
        result = rep_mod.pick_near_repertoire_moves(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username="TestOpp",
            board=board,
        )
    finally:
        _uninstall_fake_pool()

    assert result is None, "empty rows -> None (no near-book signal)"
    assert state["executed"], "should issue one query"
    sql, params = state["executed"][0]

    assert "r.played_color = %s" in sql, f"query must filter played_color; got {sql!r}"
    assert "r.ply_index BETWEEN %s AND %s" in sql, (
        f"query must use a ply-index window; got {sql!r}"
    )
    assert "r.position_key <> %s" in sql, (
        f"query must exclude the live position_key; got {sql!r}"
    )

    # params order: (lambda, seconds_per_year, user, provider, username,
    #                 played_color, lo, hi, position_key)
    assert params[-4] == expected_played_color, (
        f"played_color param should be {expected_played_color!r}; got {params}"
    )
    assert params[-3] == expected_ply_index - rep_mod.NEAR_BOOK_PLY_WINDOW, (
        f"ply window lo should be {expected_ply_index - rep_mod.NEAR_BOOK_PLY_WINDOW}; "
        f"got {params}"
    )
    assert params[-2] == expected_ply_index + rep_mod.NEAR_BOOK_PLY_WINDOW, (
        f"ply window hi should be {expected_ply_index + rep_mod.NEAR_BOOK_PLY_WINDOW}; "
        f"got {params}"
    )
    assert params[-1] == expected_key, (
        f"excluded position_key should be {expected_key!r}; got {params[-1]!r}"
    )
    # Decay constants are threaded through as the first two params, matching
    # pick_repertoire_move.
    assert params[0] == rep_mod.RECENCY_DECAY_LAMBDA_PER_YEAR
    assert params[1] == rep_mod._SECONDS_PER_YEAR
    print(f"  [PASS] played_color={expected_played_color}, "
          f"ply window=[{expected_ply_index - rep_mod.NEAR_BOOK_PLY_WINDOW}, "
          f"{expected_ply_index + rep_mod.NEAR_BOOK_PLY_WINDOW}], "
          f"excluded key={expected_key!r}, decay constants threaded")


# ---------------------------------------------------------------------------
# Test D3: SEQUENCING RULE -- structural separation of exact-book and
# near-book lookups.
#
# pick_repertoire_move (exact) must issue ONLY the exact-key query (no ply
# window, no played_color filter, no `<>` exclusion), while
# pick_near_repertoire_moves issues the near-book query. D is a SEPARATE code
# path: the in-book branch returns the book move without ever reaching the
# near-book lookup, so near-book can neither double-influence nor override a
# true book hit.
# ---------------------------------------------------------------------------
def test_sequencing_exact_book_skips_near_book():
    print("\n=== Test D3: SEQUENCING (exact book hit skips near-book) ===")
    board = chess.Board()  # start position, white to move

    # --- exact-book path ---
    exact_state = {
        "executed": [],
        "rows": [
            {
                "move_uci": "e2e4",
                "move_san": "e4",
                "frequency": 1,
                "weighted_frequency": 1.0,
            },
        ],
    }
    _install_fake_pool(exact_state)
    try:
        exact_choice = rep_mod.pick_repertoire_move(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username="TestOpp",
            board=board,
        )
    finally:
        _uninstall_fake_pool()

    assert exact_choice is not None and exact_choice["move_uci"] == "e2e4"
    assert len(exact_state["executed"]) == 1, (
        f"exact-book path should issue exactly one query; got "
        f"{len(exact_state['executed'])}"
    )
    exact_sql = exact_state["executed"][0][0]
    assert "r.position_key = %s" in exact_sql
    assert "ply_index BETWEEN" not in exact_sql, (
        f"exact-book query must NOT use a ply window; got {exact_sql!r}"
    )
    assert "r.played_color = %s" not in exact_sql, (
        f"exact-book query must NOT filter played_color; got {exact_sql!r}"
    )
    assert "r.position_key <> %s" not in exact_sql, (
        f"exact-book query must NOT use `<>` exclusion; got {exact_sql!r}"
    )
    print(f"  [PASS] exact-book query is ply-window-free / color-free; "
          f"returns the book move directly")

    # --- near-book path (separate) ---
    near_state = {
        "executed": [],
        "rows": [{"move_uci": "e2e4", "frequency": 2, "weighted_frequency": 2.0}],
    }
    _install_fake_pool(near_state)
    try:
        near_choice = rep_mod.pick_near_repertoire_moves(
            requested_by_user_id="user-1",
            provider="lichess",
            opponent_username="TestOpp",
            board=board,
        )
    finally:
        _uninstall_fake_pool()

    assert near_choice is not None
    assert len(near_state["executed"]) == 1
    near_sql = near_state["executed"][0][0]
    assert "ply_index BETWEEN" in near_sql
    assert "r.played_color = %s" in near_sql
    assert "r.position_key <> %s" in near_sql
    print(f"  [PASS] near-book query is a separate lookup (ply window + color "
          f"+ exclusion); reached only via an explicit separate call")


# ---------------------------------------------------------------------------
# Test D4: NEAR-BOOK HEAD-TO-HEAD (WITH vs WITHOUT near_book_weights).
#
# All other signals dormant (sac_freq=0, qt neutral, no setup/castle/trap/
# length). near_book_weights maps only the mid-rank candidate c3b5 -> 1.0.
# WITH: c3b5 share=1.0 -> near_book_mult = 1 + 2.0 = 3.0.
# WITHOUT: all signals dormant -> deterministic top pick (c3a4), c3b5 = 0%.
# ---------------------------------------------------------------------------
NEAR_BOARD_FEN = "7k/8/8/3p4/8/2N5/8/4K3 w - - 0 1"
NEAR_CANDIDATES = [
    {"move": "c3a4", "score": 12, "wdl": {"win": 0.50, "draw": 0.04, "loss": 0.46}, "policy": 0.35},
    {"move": "c3b5", "score": 8,  "wdl": {"win": 0.49, "draw": 0.045, "loss": 0.465}, "policy": 0.30},
    {"move": "c3e4", "score": -8, "wdl": {"win": 0.43, "draw": 0.04, "loss": 0.53}, "policy": 0.20},
    {"move": "c3d5", "score": 30, "wdl": {"win": 0.52, "draw": 0.04, "loss": 0.44}, "policy": 0.15},
]


def test_near_book_head_to_head():
    print("\n=== Test D4: NEAR-BOOK HEAD-TO-HEAD (WITH vs WITHOUT) ===")
    board = chess.Board(NEAR_BOARD_FEN)
    style = _make_style(sufficient=True, sacrifice_frequency=0.0)
    n_trials = 4000

    near_book_weights = {"c3b5": 1.0}

    with_picks = _run(style, NEAR_CANDIDATES, board, near_book_weights,
                      n_trials, seed=9001)
    without_picks = _run(style, NEAR_CANDIDATES, board, None,
                         n_trials, seed=9001)

    print(f"  empirical distribution over {n_trials} trials each:")
    print(f"    {'idx':>3}  {'move':>6}  {'with_pct':>10}  {'without_pct':>12}  {'delta_pp':>10}")
    for idx in range(len(NEAR_CANDIDATES)):
        c = NEAR_CANDIDATES[idx]
        w_pct = with_picks.get(idx, 0) / n_trials * 100
        wo_pct = without_picks.get(idx, 0) / n_trials * 100
        marker = " <-- near-book" if c["move"] == "c3b5" else ""
        print(f"    {idx:>3}  {c['move']:>6}  {w_pct:>9.2f}%  {wo_pct:>11.2f}%  "
              f"{w_pct - wo_pct:>+9.2f}pp{marker}")

    b5_with_pct = with_picks.get(1, 0) / n_trials * 100
    b5_without_pct = without_picks.get(1, 0) / n_trials * 100

    # WITHOUT must be deterministic top pick (all signals dormant) -> c3b5 0%.
    assert b5_without_pct == 0.0, (
        f"WITHOUT near-book data, all signals are dormant -> deterministic "
        f"top pick (c3a4), c3b5 should be 0%%; got {b5_without_pct:.2f}%%"
    )
    # WITH must boost c3b5 substantially above its policy base rate (30%).
    base_rate_b5 = 0.30 / (0.35 + 0.30 + 0.20 + 0.15) * 100
    assert b5_with_pct > base_rate_b5 + 10.0, (
        f"near-book should push c3b5 above its base rate "
        f"({base_rate_b5:.2f}%%); got {b5_with_pct:.2f}%%"
    )
    assert b5_with_pct - b5_without_pct >= 25.0, (
        f"near-book head-to-head: c3b5 should be >= 25pp more WITH than "
        f"WITHOUT; got with={b5_with_pct:.2f}%% without={b5_without_pct:.2f}%%"
    )
    print(f"\n  [PASS] with c3b5_pct ({b5_with_pct:.2f}%) - without "
          f"({b5_without_pct:.2f}%) = {b5_with_pct - b5_without_pct:.2f}pp; "
          f"base rate {base_rate_b5:.2f}%")


# ---------------------------------------------------------------------------
# Test D5: near-book composes multiplicatively with sac.
# ---------------------------------------------------------------------------
def test_near_book_composition_with_sac():
    print("\n=== Test D5: near-book COMPOSES with sac (multiplicative) ===")
    board = chess.Board(NEAR_BOARD_FEN)
    style = _make_style(sufficient=True, sacrifice_frequency=0.15)

    # c3e4 is sac-looking (knight lands on d5-attacked e4) AND near-book.
    result = rerank_candidates(
        candidates=NEAR_CANDIDATES, style=style, board=board,
        rng=random.Random(0), near_book_weights={"c3e4": 1.0},
    )
    print(f"  chosen: {result['chosen_move_uci']}; "
          f"signals_applied: {result['bias_breakdown']['signals_applied']}")
    assert "sacrifice" in result["bias_breakdown"]["signals_applied"]
    assert "near_book" in result["bias_breakdown"]["signals_applied"]

    for row in result["bias_breakdown"]["weights"]:
        if row["move"] == "c3e4":
            sac_m = row["sac_multiplier"]
            nb_m = row["near_book_multiplier"]
            combo = row["bias_multiplier"]
            print(f"    c3e4: sac_mult={sac_m} near_book_mult={nb_m} "
                  f"combo={combo}")
            assert abs(sac_m - 1.6) < 1e-3, (
                f"c3e4 sac_mult should be 1.6 (sac_freq=0.15); got {sac_m}"
            )
            assert abs(nb_m - 3.0) < 1e-6, (
                f"c3e4 near_book_mult should be 1 + 2.0*1.0 = 3.0; got {nb_m}"
            )
            expected_combo = sac_m * nb_m
            assert abs(combo - expected_combo) < 1e-3, (
                f"c3e4 combo should equal sac*near_book = {expected_combo}; "
                f"got {combo}"
            )
            assert combo > sac_m and combo > nb_m
            print(f"  [PASS] c3e4: sac*near_book = {sac_m}*{nb_m} = {combo}")
            break
    else:
        raise AssertionError("c3e4 not found in breakdown weights")


# ---------------------------------------------------------------------------
# Test D6: no-data / insufficient-data regression (near-book dormant).
# ---------------------------------------------------------------------------
def test_near_book_no_data_regression():
    print("\n=== Test D6: no near-book data -> no-op ===")
    board = chess.Board(NEAR_BOARD_FEN)
    style = _make_style(sufficient=True, sacrifice_frequency=0.15)

    for label, nbw in [("None", None), ("{}", {})]:
        result = rerank_candidates(
            candidates=NEAR_CANDIDATES, style=style, board=board,
            rng=random.Random(0), near_book_weights=nbw,
        )
        assert result["applied_bias"] is True, (
            f"sac_freq=0.15 + c3e4 sac-looking -> applied_bias True via sac "
            f"alone; got {result}"
        )
        assert result["near_book_active"] is False, (
            f"near_book_weights={label}: near_book_active should be False"
        )
        assert result["near_book_candidate_count"] == 0
        assert "near_book" not in result["bias_breakdown"]["signals_applied"], (
            f"near_book_weights={label}: signals_applied should not include "
            f"'near_book'"
        )
        for row in result["bias_breakdown"]["weights"]:
            assert row["near_book_multiplier"] == 1.0, (
                f"near_book_weights={label}: near_book_multiplier should be "
                f"1.0; got {row['near_book_multiplier']}"
            )
            assert row["near_book_weight"] == 0.0
        print(f"    [PASS] near_book_weights={label}: near_book_active=False, "
              f"all near_book_multiplier=1.0, signals_applied="
              f"{result['bias_breakdown']['signals_applied']}")


# ---------------------------------------------------------------------------
# Test D7: insufficient-data regression with near-book data present
# (mirrors trap test 26): the outer sufficient gate short-circuits before
# near-book is ever evaluated.
# ---------------------------------------------------------------------------
def test_near_book_insufficient_data():
    print("\n=== Test D7: INSUFFICIENT-DATA (near-book data present) ===")
    board = chess.Board(NEAR_BOARD_FEN)
    style = _make_style(sufficient=False, sacrifice_frequency=0.20)

    for trial in range(5):
        result = rerank_candidates(
            candidates=NEAR_CANDIDATES, style=style, board=board,
            rng=random.Random(trial), near_book_weights={"c3b5": 1.0},
        )
        assert result["chosen_index"] == 0
        assert result["source"] == "insufficient_data"
        assert result["applied_bias"] is False
        assert result["bias_breakdown"] is None
        assert result["near_book_active"] is False, (
            f"trial {trial}: near_book_active should be False (sufficient "
            f"gate short-circuits before near-book evaluation); got "
            f"{result['near_book_active']}"
        )
        assert result["near_book_candidate_count"] == 0
    print(f"  [PASS] 5 trials all returned candidates[0] deterministically; "
          f"near_book_active=False (never evaluated)")


# ---------------------------------------------------------------------------
# Test D8: transparency fields.
# ---------------------------------------------------------------------------
def test_near_book_transparency():
    print("\n=== Test D8: transparency fields ===")
    board = chess.Board(NEAR_BOARD_FEN)
    style = _make_style(sufficient=True, sacrifice_frequency=0.0)

    # Two near-book moves: c3b5 (share 0.75) and c3d5 (share 0.25).
    result = rerank_candidates(
        candidates=NEAR_CANDIDATES, style=style, board=board,
        rng=random.Random(0),
        near_book_weights={"c3b5": 3.0, "c3d5": 1.0},
    )
    assert result["applied_bias"] is True
    assert result["near_book_active"] is True
    assert result["near_book_candidate_count"] == 2
    assert "near_book" in result["bias_breakdown"]["signals_applied"]

    for row in result["bias_breakdown"]["weights"]:
        assert "near_book_weight" in row
        assert "near_book_multiplier" in row
        if row["move"] == "c3b5":
            assert abs(row["near_book_weight"] - 0.75) < 1e-3, (
                f"c3b5 near_book_weight should be 0.75; got "
                f"{row['near_book_weight']}"
            )
            assert abs(row["near_book_multiplier"] - 2.5) < 1e-3, (
                f"c3b5 near_book_multiplier should be 1+2.0*0.75=2.5; got "
                f"{row['near_book_multiplier']}"
            )
        elif row["move"] == "c3d5":
            assert abs(row["near_book_weight"] - 0.25) < 1e-3
            assert abs(row["near_book_multiplier"] - 1.5) < 1e-3
        else:
            assert row["near_book_weight"] == 0.0
            assert row["near_book_multiplier"] == 1.0
    print(f"  [PASS] near_book_active=True, near_book_candidate_count=2, "
          f"per-row near_book_weight/multiplier correct, signals_applied "
          f"includes 'near_book'")


def main():
    print("opponent_near_book live test harness")
    test_near_book_reads_weighted_data()
    test_near_book_query_params()
    test_sequencing_exact_book_skips_near_book()
    test_near_book_head_to_head()
    test_near_book_composition_with_sac()
    test_near_book_no_data_regression()
    test_near_book_insufficient_data()
    test_near_book_transparency()
    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
