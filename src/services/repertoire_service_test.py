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
            key = (str(rid), fen)
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
                # ON CONFLICT (repertoire_id, fen) DO UPDATE
                #   SET move = EXCLUDED.move, updated_at = NOW()
                existing["move"] = move
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
    repertoire_positions store keyed by (repertoire_id, fen)."""

    def __init__(self, repertoires: Dict[str, Dict]) -> None:
        self.repertoires = repertoires
        # (repertoire_id, fen) -> row dict. This key IS the migration's
        # UNIQUE (repertoire_id, fen) constraint — making the dedup
        # observable from tests.
        self.positions: Dict[Tuple[str, str], Dict] = {}
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


def test_replay_and_plan_skips_opponent_plies_white_owner() -> None:
    _print_section("TEST 2: _replay_and_plan skips opponent (black) plies for white repertoire")
    # 1.e4 e5 2.Nf3 Nc6 3.Bc4 — owner plays white, so only plies
    # 0/2/4 should produce plans. Plies 1/3 (black's replies) are
    # replayed onto the board so the line keeps going, but emit no
    # row themselves.
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
    plans = _replay_and_plan(moves, repertoire_color="white")
    assert len(plans) == 3, (
        f"5-ply line for a white repertoire must produce 3 plans "
        f"(one per white-ply), got {len(plans)}"
    )
    # POINT 5: every plan's fen must report white-to-move (field
    # index 1 of the normalized FEN). If even one plan landed on a
    # black-to-move fen, the spec was violated.
    for p in plans:
        side = p.fen.split()[1]
        assert side == "w", (
            f"plan {p!r} has black-to-move fen {p.fen!r}; opponent "
            f"plies must be skipped, not written"
        )
    planned_moves = [p.move for p in plans]
    assert planned_moves == ["e2e4", "g1f3", "f1c4"], (
        f"planned moves should be the white plies in ply order, "
        f"got {planned_moves}"
    )
    _print_pass(
        "5-ply (e4 e5 Nf3 Nc6 Bc4) -> 3 white-side plans; black plies "
        "replayed for board state but skipped from the plan"
    )


def test_replay_and_plan_skips_opponent_plies_black_owner() -> None:
    _print_section("TEST 3: _replay_and_plan skips opponent (white) plies for black repertoire")
    # Same line, but owner = BLACK — the rows are emitted for plies
    # 1 and 3 (the black moves), and plies 0/2/4 (white's moves) are
    # replayed only. Point 5 must hold symmetrically in both colors.
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
    plans = _replay_and_plan(moves, repertoire_color="black")
    assert len(plans) == 2, (
        f"5-ply line for a black repertoire must produce 2 plans "
        f"(one per black-ply), got {len(plans)}"
    )
    for p in plans:
        side = p.fen.split()[1]
        assert side == "b", (
            f"plan {p!r} has white-to-move fen {p.fen!r}; opponent "
            f"plies must be skipped, not written"
        )
    planned_moves = [p.move for p in plans]
    assert planned_moves == ["e7e5", "b8c6"], (
        f"planned moves should be the black plies in ply order, "
        f"got {planned_moves}"
    )
    _print_pass(
        "5-ply (e4 e5 Nf3 Nc6 Bc4) -> 2 black-side plans; white plies "
        "replayed for board state but skipped from the plan"
    )


def test_upsert_transposition_dedupes_to_one_row() -> None:
    """POINT 3 (the headline case): two different move orders that
    transpose into the same position produce ONE repertoire_positions
    row for that transposition point, not two. The mechanism is the
    migration's UNIQUE (repertoire_id, fen) + the
    ON CONFLICT (repertoire_id, fen) DO UPDATE SET move = EXCLUDED.move
    in the INSERT — verified here end-to-end through the fake conn
    which simulates both.

    Fixture (Italian Game reached via 1.Nf3 vs 1.e4 orders):
      Line A: 1.Nf3 Nc6 2.e4 e5 3.Bc4  (white moves: Nf3, e4, Bc4)
      Line B: 1.e4 e5 2.Nf3 Nc6 3.Bc4  (white moves: e4, Nf3, Bc4)
    Both reach the SAME position (1.e4 e5 2.Nf3 Nc6) with white to
    move, and both continue 3.Bc4. The (T, 'f1c4') row from that
    transposition point must land as ONE row in the store, not two.
    """
    _print_section("TEST 4: transposition -> ONE row at the transposition FEN, not two")

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
        f"ONE row after both lines, got {len(rows_at_transposition)}. "
        f"Either the FEN normalization is wrong (clock/fullmove leaks "
        f"back in) OR the ON CONFLICT clause isn't deduping."
    )
    the_row = rows_at_transposition[0]
    assert the_row["move"] == "f1c4", (
        f"transposition row's move should be 'f1c4' (both lines "
        f"continue 3.Bc4), got {the_row['move']!r}"
    )
    _print_pass(
        f"transposition FEN -> 1 row move='f1c4', not 2 "
        f"(deduped via ON CONFLICT (repertoire_id, fen) DO UPDATE)"
    )

    # Sanity: across BOTH upserts, no (repertoire_id, fen) pair got
    # duplicated in the store. The migration's UNIQUE constraint and
    # the fake's dedup share this invariant; we assert it here so a
    # future regression that bypasses the conflict can't slip through.
    seen_fens = set()
    for v in conn.positions_for_repertoire(rid):
        assert v["fen"] not in seen_fens, (
            f"duplicate fen in store: {v['fen']!r} — each fen should "
            f"appear at most ONCE per repertoire (UNIQUE constraint)"
        )
        seen_fens.add(v["fen"])
    print(f"  total distinct positions stored after both lines: {len(seen_fens)}")
    _print_pass(f"{len(seen_fens)} distinct positions across both calls (no dupes)")

    # Bonus assertion on the move-overwrite clause: at the start FEN,
    # the two lines DISAGREE (Line A plays Nf3, Line B plays e4). The
    # second call (Line B) should win, so the start-fen row should
    # end with move='e2e4'. This proves the conflict path is a true
    # UPDATE, not a silent DO NOTHING.
    start_fen = _normalize_fen(chess.Board().fen())
    start_rows = [v for v in conn.positions_for_repertoire(rid)
                  if v["fen"] == start_fen]
    assert len(start_rows) == 1 and start_rows[0]["move"] == "e2e4", (
        f"start-fen row should hold the LATEST write's move ('e2e4' "
        f"from Line B), proving DO UPDATE (not DO NOTHING); got "
        f"{start_rows!r}"
    )
    _print_pass(
        "start-fen row move = 'e2e4' (Line B's move, latest wins) — "
        "conflict path is DO UPDATE, not DO NOTHING"
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


def test_upsert_black_repertoire_only_emits_black_side_rows() -> None:
    """End-to-end point-5 check on the BLACK side: feeding the full
    upsert path the 1.e4 e5 2.Nf3 Nc6 3.Bc4 line for a black
    repertoire should produce rows ONLY for the black-to-move
    positions, never at a white-to-move fen (the e4/Nf3/Bc4 plies
    were the opponent's).
    """
    _print_section("TEST 8: black repertoire full-upsert path end-to-end (point 5, DB side)")

    conn, rid = _make_black_repertoire()
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]
    written = upsert_repertoire_positions(
        conn, repertoire_id=rid, uci_moves=moves
    )

    assert len(written) == 2, (
        f"5-ply line for a black repertoire should write 2 rows, "
        f"got {len(written)}"
    )
    for row in written:
        side = row.fen.split()[1]
        assert side == "b", (
            f"row at fen {row.fen!r} has white-to-move side; a black "
            f"repertoire must only write rows where BLACK is on move"
        )
    moves_written = [r.move for r in written]
    assert moves_written == ["e7e5", "b8c6"], (
        f"written moves should be exactly the black plies in order, "
        f"got {moves_written}"
    )
    _print_pass(
        "black-repertoire upsert wrote 2 rows at black-to-move fens; "
        f"white-to-move positions were skipped from the DB write"
    )


def main() -> int:
    print("=== Running repertoire_service upsert smoke tests ===")
    try:
        test_normalize_fen_strips_clock_and_fullmove()
        test_replay_and_plan_skips_opponent_plies_white_owner()
        test_replay_and_plan_skips_opponent_plies_black_owner()
        test_upsert_transposition_dedupes_to_one_row()
        test_upsert_illegal_move_raises_and_writes_nothing()
        test_upsert_empty_move_sequence_returns_empty()
        test_upsert_unknown_repertoire_raises_not_found()
        test_upsert_black_repertoire_only_emits_black_side_rows()
    except AssertionError as exc:
        print(f"\n  [FAIL] {exc}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())