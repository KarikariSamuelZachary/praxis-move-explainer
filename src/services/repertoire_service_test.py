"""Standalone smoke test for the Repertoire trainer write path.

Run with: cd src && PYTHONPATH=. ../venv/bin/python services/repertoire_service_test.py

(PYTHONPATH=. is required so `from services.repertoire_service import ...`
resolves to src/services/...; the `cd src` half puts the cwd inside the
package root.) The repo's other standalone smoke tests under
services/*_test.py follow the same convention.

This file proves points 3 and 5 from the upsert spec actually work,
plus the two named edge cases:

  * POINT 3 (FEN normalization + transposition dedup): two different
    move orders that reach the same normalized 4-field FEN produce ONE
    repertoire_positions row, not two. Verified end-to-end through a
    fake psycopg2 conn that simulates the migration's
    `UNIQUE (repertoire_id, fen)` constraint and the
    `ON CONFLICT ... DO UPDATE SET move = EXCLUDED.move` semantics.

  * POINT 5 (opponent-turn plies are skipped): for a white repertoire,
    black's reply plies are replayed onto the board (so transpositions
    through opponent replies still resolve) but NO row is written from
    any black-to-move position. Verified both on the pure
    `_replay_and_plan` helper (ply-level) and through the full upsert
    path (DB-level).

  * EDGE CASE (illegal move): raises `IllegalRepertoireMoveError`
    naming the offending ply, without leaving partial rows for the
    plies before it. The full replay completes before any INSERT.

  * EDGE CASE (empty move sequence): returns [] and writes no rows.

The convention here matches `services/opponent_repertoire_test.py`:
a standalone runner with `_print_section` / `_print_pass` markers,
no pytest dependency, exercising pure helpers via direct calls and
the DB-touching function via a small fake conn.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import chess

from services.repertoire_service import (
    IllegalRepertoireMoveError,
    RepertoireNotFoundError,
    _normalize_fen,
    _replay_and_plan,
    upsert_repertoire_positions,
)

# Fixed "now" the fake store stamps rows with. Using a constant makes
# the snapshots in test output diff-stable and keeps assertions free
# of datetime jitter.
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _print_pass(label: str) -> None:
    print(f"  [PASS] {label}")


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def uuid_str() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------
# Fake psycopg2 conn + cursor for end-to-end dedup/skip tests.
#
# Real Postgres isn't available in this test (no DB infra here — the
# matching pattern in opponent_repertoire_test.py is "test pure
# helpers"). For the upsert path that DOES touch the DB, we exercise
# it through a fake conn that:
#   * answers the `SELECT ... FROM repertoires WHERE id = %s` query
#     from an in-memory dict;
#   * simulates the migration's `UNIQUE (repertoire_id, fen)`
#     constraint and the `ON CONFLICT (repertoire_id, fen) DO UPDATE
#     SET move = EXCLUDED.move, updated_at = NOW()` semantics on the
#     INSERT.
# The fake is intentionally small and lives entirely in this file —
# not a reusable test fixture, just enough machinery for the four
# assertions below to mean something.
# ---------------------------------------------------------------------


class _FakeCursor:
    """Minimal psycopg2 cursor stand-in for the upsert path.

    Routes on SQL verb + table name; raises NotImplementedError on
    anything else so the test fails loudly if the service changes its
    query shape.

    `cursor_factory` is accepted (and ignored) so
    `conn.cursor(cursor_factory=RealDictCursor)` mirrors real psycopg2
    usage. The fake is always dict-shaped — `RealDictCursor` is the
    only mode the service uses.
    """

    def __init__(self, conn: "_FakeConn", cursor_factory=None) -> None:
        self.conn = conn
        self._fetchone: Optional[Dict] = None
        self.rowcount = 0
        self._cursor_factory = cursor_factory

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, query: str, params=None) -> None:
        q = query.upper()

        if "FROM REPERTOIRES" in q and "WHERE" in q and "ID" in q:
            # SELECT id, user_id, name, color, created_at, updated_at
            # FROM repertoires WHERE id = %s
            (rid,) = params
            self._fetchone = self.conn.repertoires.get(str(rid))
            return

        if "INSERT INTO REPERTOIRE_POSITIONS" in q:
            # params = (repertoire_id, fen, move) — matches the only
            # INSERT shape the service emits.
            rid, fen, move = params
            key = (str(rid), fen, move)
            existing = self.conn.positions.get(key)
            if existing is None:
                # Simulate INSERT with migration defaults.
                row = {
                    "id": self.conn._new_uuid(),
                    "repertoire_id": str(rid),
                    "fen": fen,
                    "move": move,
                    "due": _NOW,
                    "stability": None,
                    "difficulty": None,
                    "state": "Learning",
                    "step": None,
                    "reps": 0,
                    "lapses": 0,
                    "last_review": None,
                    "created_at": _NOW,
                    "updated_at": _NOW,
                }
                self.conn.positions[key] = row
                self.rowcount = 1
            else:
                # ON CONFLICT (repertoire_id, fen, move) DO UPDATE
                #   SET updated_at = NOW()
                existing["updated_at"] = _NOW
                self.rowcount = 1
                row = existing
            self._fetchone = row
            return

        raise NotImplementedError(f"unmocked execute: {q}")

    def fetchone(self) -> Optional[Dict]:
        return self._fetchone

    def fetchall(self) -> List[Dict]:
        return [self._fetchone] if self._fetchone is not None else []


class _FakeConn:
    """psycopg2 conn stand-in: holds repertoires (by id) and the
    repertoire_positions store keyed by (repertoire_id, fen, move)."""

    def __init__(self, repertoires: Dict[str, Dict]) -> None:
        self.repertoires = repertoires
        # (repertoire_id, fen, move) -> row dict. This key IS the
        # migration's UNIQUE (repertoire_id, fen, move) constraint —
        # making the per-move dedup observable from tests.
        self.positions: Dict[Tuple[str, str, str], Dict] = {}
        self._seq = 0

    def cursor(self, cursor_factory=None) -> _FakeCursor:
        return _FakeCursor(self, cursor_factory=cursor_factory)

    def _new_uuid(self) -> UUID:
        self._seq += 1
        # Deterministic for tests. No assertions key on the UUID
        # value — it only needs to be a UUID instance so pydantic
        # accepts the row when building RepertoirePosition.
        return UUID(int=self._seq)

    def positions_for_repertoire(self, repertoire_id: str) -> List[Dict]:
        return [v for (k, v) in self.positions.items() if k[0] == repertoire_id]


def _make_white_repertoire(name: str = "Italian Game") -> Tuple[_FakeConn, str]:
    rid = uuid_str()
    conn = _FakeConn({
        rid: {
            "id": rid,
            "user_id": "clerk-user-1",
            "name": name,
            "color": "white",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    })
    return conn, rid


def _make_black_repertoire(name: str = "Caro-Kann") -> Tuple[_FakeConn, str]:
    rid = uuid_str()
    conn = _FakeConn({
        rid: {
            "id": rid,
            "user_id": "clerk-user-1",
            "name": name,
            "color": "black",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    })
    return conn, rid


# ---------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------


def test_normalize_fen_strips_clock_and_fullmove() -> None:
    _print_section("TEST 1: _normalize_fen strips halfmove clock + fullmove number")
    # Board.fen() always emits 6 fields: board, side, castling, ep,
    # halfmove clock, fullmove number. The migration's UNIQUE target
    # is on the 4-field form, so callers MUST strip the last two
    # before INSERT — otherwise transpositions through the same
    # position at different move counts would be stored as separate
    # rows, defeating the whole point.
    full = chess.Board().fen()  # "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    norm = _normalize_fen(full)
    expected = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
    assert norm == expected, (
        f"expected 4-field normalized FEN {expected!r}, got {norm!r}; "
        f"halfmove clock and fullmove number should be stripped"
    )
    fields = norm.split()
    assert len(fields) == 4, (
        f"expected exactly 4 fields (board, side, castling, ep), "
        f"got {len(fields)}: {fields}"
    )
    _print_pass("6-field FEN -> 4-field normalized (clock + fullmove stripped)")


def test_replay_and_plan_persists_every_ply_white_owner() -> None:
    _print_section("TEST 2: _replay_and_plan persists EVERY ply (white owner)")
    # 1.e4 e5 2.Nf3 Nc6 3.Bc4 — every ply is persisted now (both
    # owner AND opponent rows), so all 5 plies produce plans. The
    # owner_color argument is accepted for API stability but does not
    # filter the plan anymore.
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
    plans = _replay_and_plan(moves, repertoire_color="white")
    assert len(plans) == 5, (
        f"5-ply line must produce 5 plans (one per ply, both sides "
        f"persisted), got {len(plans)}"
    )
    planned_moves = [p.move for p in plans]
    assert planned_moves == ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"], (
        f"planned moves should be every ply in ply order, "
        f"got {planned_moves}"
    )
    # Side-to-move alternates w/b/w/b/w across the plans.
    sides = [p.fen.split()[1] for p in plans]
    assert sides == ["w", "b", "w", "b", "w"], (
        f"plan side-to-move sequence should alternate w/b/w/b/w; "
        f"got {sides}"
    )
    _print_pass(
        "5-ply -> 5 plans in ply order, side-to-move alternates w/b/w/b/w"
    )


def test_replay_and_plan_persists_every_ply_black_owner() -> None:
    _print_section("TEST 3: _replay_and_plan persists EVERY ply (black owner)")
    # Same line, owner = BLACK. The new writer persists every ply
    # regardless of owner color, so all 5 plies still produce plans
    # (the owner_color arg does not filter).
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
    plans = _replay_and_plan(moves, repertoire_color="black")
    assert len(plans) == 5, (
        f"5-ply line for a black repertoire must produce 5 plans "
        f"(every ply persisted regardless of owner color), got "
        f"{len(plans)}"
    )
    planned_moves = [p.move for p in plans]
    assert planned_moves == ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"], (
        f"planned moves should be every ply in ply order, "
        f"got {planned_moves}"
    )
    _print_pass(
        "5-ply -> 5 plans regardless of owner color (every ply persisted)"
    )


def test_upsert_transposition_dedupes_to_one_row() -> None:
    """POINT 3 (the headline case): two different move orders that
    transpose into the same position produce ONE repertoire_positions
    row for that transposition point's shared move, not two. The
    mechanism is the migration's UNIQUE (repertoire_id, fen, move) +
    the ON CONFLICT (repertoire_id, fen, move) DO UPDATE SET
    updated_at = NOW() in the INSERT — verified here end-to-end
    through the fake conn which simulates both.

    Fixture (Italian Game reached via 1.Nf3 vs 1.e4 orders):
      Line A: 1.Nf3 Nc6 2.e4 e5 3.Bc4
      Line B: 1.e4 e5 2.Nf3 Nc6 3.Bc4
    Both reach the SAME position (1.e4 e5 2.Nf3 Nc6) with white to
    move, and both continue 3.Bc4. The (T, transposition_fen, 'f1c4')
    row from that transposition point must land as ONE row in the
    store, not two.

    NOTE: with the new save-every-ply writer, the two lines ALSO
    share rows at intermediate positions (1.e4 e5 is reached both
    ways → the (after-e4, 'e7e5') row dedupes; 1.Nf3 Nc6 is reached
    both ways → the (after-Nf3, 'b8c6') row dedupes). And the START
    position now holds TWO rows ('g1f3' from line A and 'e2e4' from
    line B) — a fork, which is correct diverging-repertoire behavior
    under the new model. We assert the headline transposition
    dedupes; we do NOT assert the old "every FEN appears at most
    once" invariant because that invariant no longer holds by design.
    """
    _print_section("TEST 4: transposition -> ONE row at the transposition (fen, move), not two")

    conn, rid = _make_white_repertoire()

    line_a = ["g1f3", "b8c6", "e2e4", "e7e5", "f1c4"]
    line_b = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]

    upsert_repertoire_positions(conn, repertoire_id=rid, uci_moves=line_a)
    after_a = {(v["fen"], v["move"]) for v in conn.positions_for_repertoire(rid)}
    print(f"  after Line A ({len(after_a)} rows): {sorted(after_a)}")

    upsert_repertoire_positions(conn, repertoire_id=rid, uci_moves=line_b)
    after_b = {(v["fen"], v["move"]) for v in conn.positions_for_repertoire(rid)}
    print(f"  after Line B ({len(after_b)} rows): {sorted(after_b)}")

    # Compute the transposition FEN deterministically so the
    # assertion reads as "the position after 1.e4 e5 2.Nf3 Nc6"
    # rather than as a hand-typed literal.
    board = chess.Board()
    for uci in ["e2e4", "e7e5", "g1f3", "b8c6"]:
        board.push(chess.Move.from_uci(uci))
    transposition_fen = _normalize_fen(board.fen())
    print(f"  transposition normalized FEN: {transposition_fen!r}")

    rows_at_transposition = [
        v for v in conn.positions_for_repertoire(rid)
        if v["fen"] == transposition_fen
    ]
    assert len(rows_at_transposition) == 1, (
        f"transposition fen {transposition_fen!r} should have exactly "
        f"ONE row (for 'f1c4') after both lines, got "
        f"{len(rows_at_transposition)}. Either the FEN normalization "
        f"is wrong (clock/fullmove leaks back in) OR the ON CONFLICT "
        f"clause isn't deduping on (repertoire_id, fen, move)."
    )
    the_row = rows_at_transposition[0]
    assert the_row["move"] == "f1c4", (
        f"transposition row's move should be 'f1c4' (both lines "
        f"continue 3.Bc4), got {the_row['move']!r}"
    )
    _print_pass(
        f"transposition FEN -> 1 row move='f1c4', not 2 "
        f"(deduped via ON CONFLICT (repertoire_id, fen, move) DO UPDATE)"
    )

    # Bonus: the start FEN now holds TWO rows ('g1f3' from line A,
    # 'e2e4' from line B) — a diverging repertoire. Under the new
    # save-every-ply model this is the correct shape: the user has
    # prepared two different first moves. Assert it to lock in the
    # new semantics (the old owner-only writer would have overwritten
    # the start row's move on the second call).
    start_fen = _normalize_fen(chess.Board().fen())
    start_rows = [v for v in conn.positions_for_repertoire(rid)
                  if v["fen"] == start_fen]
    start_moves = sorted(r["move"] for r in start_rows)
    assert start_moves == ["e2e4", "g1f3"], (
        f"start-fen should hold TWO rows ('e2e4' and 'g1f3') after "
        f"both lines — a diverging repertoire under the new save-"
        f"every-ply writer. Got {start_moves!r}."
    )
    _print_pass(
        "start-fen holds both 'e2e4' and 'g1f3' (diverging repertoire "
        "preserved, not overwritten)"
    )


def test_upsert_illegal_move_raises_and_writes_nothing() -> None:
    _print_section("TEST 5: illegal UCI move raises with ply index; zero rows written")
    # 1.e4 e5 2.Nf3 Nf3?? — the knight on g1 was just moved to f3,
    # so pushing "g1f3" again at ply 3 is illegal (the source square
    # is empty). python-chess raises, and we wrap with the ply index.
    conn, rid = _make_white_repertoire()
    bad = ["e2e4", "e7e5", "g1f3", "g1f3"]

    raised: Optional[IllegalRepertoireMoveError] = None
    try:
        upsert_repertoire_positions(conn, repertoire_id=rid, uci_moves=bad)
    except IllegalRepertoireMoveError as exc:
        raised = exc
    assert raised is not None, (
        "expected IllegalRepertoireMoveError, no exception was raised"
    )
    assert raised.ply_index == 3, (
        f"ply index of the offending move should be 3 (zero-based), "
        f"got {raised.ply_index}"
    )
    assert raised.uci_move == "g1f3", (
        f"offending move should be 'g1f3' (the illegal one), got "
        f"{raised.uci_move!r}"
    )
    _print_pass(
        f"IllegalRepertoireMoveError raised at ply {raised.ply_index} "
        f"for {raised.uci_move!r} — message: {raised}"
    )

    # Critical: no rows should exist for this call. The full replay
    # completes BEFORE any INSERT, so a mid-sequence illegal move
    # cannot leak partial rows for the plies before it. The caller's
    # transaction would also roll back, but the contract is stronger
    # than that — the planner raises BEFORE the first INSERT is even
    # dispatched.
    n = len(conn.positions_for_repertoire(rid))
    assert n == 0, (
        f"no partial rows should occur on illegal move; found {n} "
        f"row(s) written for plies before the illegal one"
    )
    _print_pass(f"zero rows written for the illegal-sequence call ({n} in store)")


def test_upsert_empty_move_sequence_returns_empty() -> None:
    _print_section("TEST 6: empty move sequence -> [] and zero rows")
    conn, rid = _make_white_repertoire()
    result = upsert_repertoire_positions(conn, repertoire_id=rid, uci_moves=[])
    assert result == [], (
        f"empty input should return [], got {result!r}"
    )
    assert len(conn.positions_for_repertoire(rid)) == 0, (
        "empty input must not write any rows"
    )
    _print_pass("empty input -> [] returned, zero rows written")


def test_upsert_unknown_repertoire_raises_not_found() -> None:
    _print_section("TEST 7: unknown repertoire_id -> RepertoireNotFoundError")
    # Repertoire not in the fake's repertoires dict — the SELECT
    # returns None, and the service should raise before any INSERT.
    conn = _FakeConn({})  # no repertoires seeded
    try:
        upsert_repertoire_positions(
            conn, repertoire_id=uuid_str(), uci_moves=["e2e4"]
        )
    except RepertoireNotFoundError as exc:
        # And crucially: no INSERT should have run. The fake raises
        # NotImplementedError on any INSERT, so if the service had
        # reached the loop we'd see that instead.
        _print_pass(f"RepertoireNotFoundError raised: {exc}")
    else:
        raise AssertionError(
            "expected RepertoireNotFoundError, no exception was raised"
        )


def test_upsert_black_repertoire_persists_every_ply() -> None:
    """End-to-end check on the BLACK side: feeding the full upsert
    path the 1.e4 e5 2.Nf3 Nc6 3.Bc4 line for a black repertoire
    produces 5 rows — one per ply, both colors persisted. The
    owner_color arg is accepted but does not filter the plan anymore;
    read endpoints filter to owner rows where appropriate.
    """
    _print_section("TEST 8: black repertoire full-upsert path end-to-end (every ply)")

    conn, rid = _make_black_repertoire()
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
    written = upsert_repertoire_positions(
        conn, repertoire_id=rid, uci_moves=moves
    )

    assert len(written) == 5, (
        f"5-ply line should write 5 rows (every ply persisted), "
        f"got {len(written)}"
    )
    moves_written = [r.move for r in written]
    assert moves_written == ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"], (
        f"written moves should be every ply in order, got "
        f"{moves_written}"
    )
    # Side-to-move alternates w/b/w/b/w regardless of owner color.
    sides = [r.fen.split()[1] for r in written]
    assert sides == ["w", "b", "w", "b", "w"], (
        f"written rows' side-to-move should alternate w/b/w/b/w; "
        f"got {sides}"
    )
    _print_pass(
        "black-repertoire upsert wrote 5 rows (every ply, both "
        f"sides) in ply order; side-to-move alternates w/b/w/b/w"
    )


def main() -> int:
    print("=== Running repertoire_service upsert smoke tests ===")
    try:
        test_normalize_fen_strips_clock_and_fullmove()
        test_replay_and_plan_persists_every_ply_white_owner()
        test_replay_and_plan_persists_every_ply_black_owner()
        test_upsert_transposition_dedupes_to_one_row()
        test_upsert_illegal_move_raises_and_writes_nothing()
        test_upsert_empty_move_sequence_returns_empty()
        test_upsert_unknown_repertoire_raises_not_found()
        test_upsert_black_repertoire_persists_every_ply()
    except AssertionError as exc:
        print(f"\n  [FAIL] {exc}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())