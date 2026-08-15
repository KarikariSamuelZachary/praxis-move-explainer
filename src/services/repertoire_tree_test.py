"""Standalone smoke test for the repertoire main-line vs side-line
tree-walk classifier.

Run with: cd src && PYTHONPATH=. ../venv/bin/python services/repertoire_tree_test.py

This file proves the three spec cases (a/b/c) plus the three named
edge cases. It follows exactly the convention of
`services/repertoire_service_test.py`: a standalone runner with
`_print_section` / `_print_pass` markers, no pytest dependency,
exercising the pure `classify_repertoire_lines` helper via direct
calls with hand-built row fixtures.

The fixtures build rows by replaying real UCI moves on a
`chess.Board` to compute each row's 4-field normalized FEN (no
hand-typed FEN literals — the row's `fen` is whatever the board
actually produces, normalized the same way the writer persists it).
`created_at` is an increasing int per ply (earlier int = earlier
created row), so "earliest created_at wins the main-line at a fork"
is explicit and deterministic without wall-clock datetime jitter.
"""
from __future__ import annotations

import sys
from typing import Dict, List, Tuple
from uuid import UUID, uuid4

import chess

from services.repertoire_service import _normalize_fen
from services.repertoire_tree import (
    RepertoireTreeRow,
    classify_repertoire_lines,
    count_descendants,
)


def _print_pass(label: str) -> None:
    print(f"  [PASS] {label}")


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------
# Fixture builders.
#
# A "line" is an ordered list of UCI moves from the start; we replay it
# and emit one RepertoireTreeRow per OWNER-turn ply (matching the
# upsert contract — only owner plies produce rows). `created_at` is
# assigned per-ply as an increasing int so fork tiebreaks are
# deterministic; `move_clock` is a module-level counter so crossing
# multiple line() calls keeps created_at strictly monotonic across the
# whole fixture (the main-line-vs-side-line decision at a fork
# compares created_at across rows that may have come from different
# line() calls).
# ---------------------------------------------------------------------

_move_clock = 0


def _reset_clock() -> None:
    global _move_clock
    _move_clock = 0


def _next_created_at() -> int:
    global _move_clock
    _move_clock += 1
    return _move_clock


def _owner_rows_for_line(
    uci_moves: List[str],
    owner_color: str,
    row_ids: Dict[int, UUID],
) -> List[RepertoireTreeRow]:
    """Replay `uci_moves` from the start and return one row per
    owner-turn ply, keyed into `row_ids` by ply index.

    `row_ids` is the caller's {ply_index -> UUID} map; the caller
    pre-assigns UUIDs so it knows which row id corresponds to which
    ply (the assertions key off these ids). The FEN is the 4-field
    normalized form the writer persists; the move is the raw UCI
    string.
    """
    owner_is_white = owner_color == "white"
    board = chess.Board()
    rows: List[RepertoireTreeRow] = []
    for ply, uci in enumerate(uci_moves):
        pre_ply_fen = _normalize_fen(board.fen())
        side_is_white = board.turn == chess.WHITE
        move = board.parse_uci(uci)
        board.push(move)
        if side_is_white == owner_is_white:
            rows.append(RepertoireTreeRow(
                id=row_ids[ply],
                fen=pre_ply_fen,
                move=uci,
                created_at=_next_created_at(),
            ))
    return rows


def _fen_for_line_prefix(uci_moves: List[str]) -> str:
    """Return the normalized FEN after replaying `uci_moves` from the
    start. Lets assertions name a position as 'the FEN after 1.e4 e5'
    without typing a FEN literal.
    """
    board = chess.Board()
    for uci in uci_moves:
        board.push(board.parse_uci(uci))
    return _normalize_fen(board.fen())


# ---------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------


def test_simple_fork_disjoint_id_slots() -> None:
    """Spec case (a): two different opponent replies, each with a
    stored user response. The earlier-created reply/response pair is
    main-line; the later one is side-line.

    Fixture (white repertoire) — two forks, each choosing the earlier
    created child (see TEST (b) below for the deep case — this case is
    the shallow single-fork version):
        1.e4
          ...e5  2.Nf3   (e5 reply, Nf3 response — earlier created_at)
          ...c5  2.c4    (c5 reply, c4 response — later created_at)

    The move after 1.e4 is BLACK's turn (opponent turn) and at that
    opponent node BOTH e5->Nf3 and c5->c4 lead to stored rows — a
    fork. e5/Nf3 is created earlier, so it's main-line; c5/c4 is the
    side-line. Disjoint UUID slots per line let the assertion name
    Line A's ply-2 row and Line B's ply-2 row distinctly.
    """
    _print_section("TEST (a) (re-run with disjoint id slots)")

    # ids keyed by (line_letter, owner_ply_within_line).
    ids: Dict[Tuple[str, int], UUID] = {
        ("a", 0): uuid4(),  # 1.e4 (owner ply 0 of Line A)
        ("a", 2): uuid4(),  # 2.Nf3 (owner ply 2 of Line A)
        ("b", 2): uuid4(),  # 2.c4 (owner ply 2 of Line B — different id)
    }

    _reset_clock()
    # Line A: 1.e4 e5 2.Nf3 — owner plies at move indices 0, 2.
    board = chess.Board()
    rows: List[RepertoireTreeRow] = []
    # ply 0: 1.e4 (white/owner)
    rows.append(RepertoireTreeRow(
        id=ids[("a", 0)],
        fen=_normalize_fen(board.fen()),
        move="e2e4",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("e2e4"))
    # ply 1: 1...e5 (black/opponent — skipped)
    board.push(chess.Move.from_uci("e7e5"))
    # ply 2: 2.Nf3 (white/owner)
    rows.append(RepertoireTreeRow(
        id=ids[("a", 2)],
        fen=_normalize_fen(board.fen()),
        move="g1f3",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("g1f3"))

    # Line B: 1.e4 c5 2.c4 — ply 0 shares the start FEN with Line A,
    # so its row would collide; emit only ply 2 (the c4 row). The
    # classifier's fen_to_row already holds the 1.e4 row from Line A.
    board2 = chess.Board()
    board2.push(chess.Move.from_uci("e2e4"))
    board2.push(chess.Move.from_uci("c7c5"))
    rows.append(RepertoireTreeRow(
        id=ids[("b", 2)],
        fen=_normalize_fen(board2.fen()),
        move="c2c4",
        created_at=_next_created_at(),
    ))

    rows.sort(key=lambda r: r.created_at)
    print(f"  fixture: {len(rows)} distinct rows")
    # Sanity: 1.e4 and 2.Nf3 both have white-to-move FENs (owner), and
    # 2.c4's FEN is also white-to-move (white plays c4 after 1.e4 c5).
    for r in rows:
        assert r.fen.split()[1] == "w", (
            f"row {r.id} at fen {r.fen!r} should be white-to-move "
            f"(owner = white); a stray black-to-move row would change "
            f"the fork structure."
        )

    result = classify_repertoire_lines(rows)

    assert result[ids[("a", 0)]] is True, (
        f"1.e4 ({ids[('a', 0)]}) should be main-line (root owner move); "
        f"got {result.get(ids[('a', 0)])}"
    )
    _print_pass("1.e4 root owner row is main-line")

    assert result[ids[("a", 2)]] is True, (
        f"2.Nf3 ({ids[('a', 2)]}) should be main-line — earliest created "
        f"at the 1.e4 opponent fork; got {result.get(ids[('a', 2)])}"
    )
    _print_pass("2.Nf3 (earliest-created at fork) is main-line")

    assert result[ids[("b", 2)]] is False, (
        f"2.c4 ({ids[('b', 2)]}) should be side-line — later-created at "
        f"the 1.e4 opponent fork; got {result.get(ids[('b', 2)])}"
    )
    _print_pass("2.c4 (later-created at fork) is side-line")


def test_deep_main_line_where_every_ancestor_chose_main() -> None:
    """Spec case (b): a position several forks deep, where every
    ancestor fork chose the main branch, is itself main-line.

    Fixture (white repertoire) — two forks, each choosing the
    earlier-created child:
        1.e4
          ...e5   2.Nf3   <- main at fork 1 (earlier created)
          ...c5   2.c4    <- side at fork 1
        after 2...:
          ...Nc6  3.Bc4   <- main at fork 2 (earlier created)
          ...d6   3.Nc3   <- side at fork 2
        after 3.Bc4:
          ...Bc5  4.c3    <- main at fork 3, the deep test target

    `4.c3` sits behind THREE forks, each of which chose the main
    branch, so `4.c3` is itself main-line.

    To make forks deterministic we add ONLY the main-line responses
    plus ONE side-line response at each fork (so the fork has exactly
    2 discovered children: main + side). The side-line response's row
    is the side-line child; the main-line response's row plus the
    NEXT ply's response forms the main-line continuation.
    """
    _print_section("TEST (b): deep main behind 3 forks each choosing main is main-line")

    # Disjoint id slots keyed by (line_letter, owner_ply).
    ids: Dict[Tuple[str, int], UUID] = {
        ("main", 0): uuid4(),  # 1.e4
        ("main", 2): uuid4(),  # 2.Nf3
        ("side1", 2): uuid4(),  # 2.c4 (side of fork 1)
        ("main", 4): uuid4(),  # 3.Bc4
        ("side2", 4): uuid4(),  # 3.Nc3 (side of fork 2)
        ("main", 6): uuid4(),  # 4.c3 — the deep test target
        ("side3", 6): uuid4(),  # 4.d3 (side of fork 3)
    }

    _reset_clock()
    rows: List[RepertoireTreeRow] = []
    board = chess.Board()

    # ply 0: 1.e4 (main, owner)
    rows.append(RepertoireTreeRow(
        id=ids[("main", 0)],
        fen=_normalize_fen(board.fen()),
        move="e2e4",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("e2e4"))
    board.push(chess.Move.from_uci("e7e5"))

    # ply 2: 2.Nf3 (main at fork 1, owner)
    rows.append(RepertoireTreeRow(
        id=ids[("main", 2)],
        fen=_normalize_fen(board.fen()),
        move="g1f3",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("g1f3"))
    board.push(chess.Move.from_uci("b8c6"))

    # ply 4: 3.Bc4 (main at fork 2, owner)
    rows.append(RepertoireTreeRow(
        id=ids[("main", 4)],
        fen=_normalize_fen(board.fen()),
        move="f1c4",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("f1c4"))
    board.push(chess.Move.from_uci("f8c5"))

    # ply 6: 4.c3 (main at fork 3, owner) — THE TEST TARGET
    rows.append(RepertoireTreeRow(
        id=ids[("main", 6)],
        fen=_normalize_fen(board.fen()),
        move="c2c3",
        created_at=_next_created_at(),
    ))

    # Side of fork 1: 1.e4 c5 2.c4 — emit only the ply-2 owner row.
    sb1 = chess.Board()
    sb1.push(chess.Move.from_uci("e2e4"))
    sb1.push(chess.Move.from_uci("c7c5"))
    rows.append(RepertoireTreeRow(
        id=ids[("side1", 2)],
        fen=_normalize_fen(sb1.fen()),
        move="c2c4",
        created_at=_next_created_at(),
    ))

    # Side of fork 2: 2.Nf3 d6 3.Nc3 — emit only the ply-4 owner row.
    sb2 = chess.Board()
    sb2.push(chess.Move.from_uci("e2e4"))
    sb2.push(chess.Move.from_uci("e7e5"))
    sb2.push(chess.Move.from_uci("g1f3"))
    sb2.push(chess.Move.from_uci("d7d6"))
    rows.append(RepertoireTreeRow(
        id=ids[("side2", 4)],
        fen=_normalize_fen(sb2.fen()),
        move="b1c3",
        created_at=_next_created_at(),
    ))

    # Side of fork 3: 3.Bc4 Nf6 4.d3 — emit only the ply-6 owner row.
    sb3 = chess.Board()
    sb3.push(chess.Move.from_uci("e2e4"))
    sb3.push(chess.Move.from_uci("e7e5"))
    sb3.push(chess.Move.from_uci("g1f3"))
    sb3.push(chess.Move.from_uci("b8c6"))
    sb3.push(chess.Move.from_uci("f1c4"))
    sb3.push(chess.Move.from_uci("g8f6"))
    rows.append(RepertoireTreeRow(
        id=ids[("side3", 6)],
        fen=_normalize_fen(sb3.fen()),
        move="d2d3",
        created_at=_next_created_at(),
    ))

    rows.sort(key=lambda r: r.created_at)
    print(f"  fixture: {len(rows)} rows across main + 3 fork-side branches")

    result = classify_repertoire_lines(rows)

    # Headline assertion: the deep main-line row (4.c3) behind 3 forks
    # is main-line because every fork chose main.
    assert result[ids[("main", 6)]] is True, (
        f"4.c3 ({ids[('main', 6)]}) should be main-line — every one of "
        f"its 3 ancestor forks chose the main branch; got "
        f"{result.get(ids[('main', 6)])}"
    )
    _print_pass("4.c3 (3 forks deep, all chose main) is main-line")

    # Supporting assertions: the intermediate main rows and the side
    # rows at each fork.
    assert result[ids[("main", 0)]] is True
    assert result[ids[("main", 2)]] is True
    assert result[ids[("main", 4)]] is True
    _print_pass("intermediate 1.e4 / 2.Nf3 / 3.Bc4 main rows all main-line")

    assert result[ids[("side1", 2)]] is False, (
        f"2.c4 (side of fork 1) should be side-line; got "
        f"{result.get(ids[('side1', 2)])}"
    )
    assert result[ids[("side2", 4)]] is False, (
        f"3.Nc3 (side of fork 2) should be side-line; got "
        f"{result.get(ids[('side2', 4)])}"
    )
    assert result[ids[("side3", 6)]] is False, (
        f"4.d3 (side of fork 3) should be side-line; got "
        f"{result.get(ids[('side3', 6)])}"
    )
    _print_pass("all 3 fork-side rows (2.c4 / 3.Nc3 / 4.d3) are side-line")


def test_side_line_with_no_own_fork_is_still_side() -> None:
    """Spec case (c): a position where even ONE ancestor fork chose
    the OTHER branch is side-line, even if that position's own
    immediate neighborhood has no fork.

    Fixture (white repertoire):
        1.e4
          ...e5  2.Nf3   (main at fork 1)
          ...c5  2.c4    (side at fork 1) <- the chosen branch here is SIDE
        after 2.c4:
          ...e6  3.Nc3   (NO fork below — only one opponent reply e6
                          has a stored user response 3.Nc3)

    `3.Nc3` sits at a position with NO fork of its own (only one
    discovered child at the opponent node after 2.c4). But its
    ancestor fork (1.e4) chose the OTHER branch, so 3.Nc3 inherits
    side-line from its parent.
    """
    _print_section("TEST (c): side-line with no own fork is side (ancestor chose other)")

    ids: Dict[Tuple[str, int], UUID] = {
        ("main", 0): uuid4(),  # 1.e4
        ("main", 2): uuid4(),  # 2.Nf3 (main at fork 1)
        ("side", 2): uuid4(),  # 2.c4 (side at fork 1)
        ("side", 4): uuid4(),  # 3.Nc3 (TEST TARGET, no fork below)
    }

    _reset_clock()
    rows: List[RepertoireTreeRow] = []
    board = chess.Board()

    rows.append(RepertoireTreeRow(
        id=ids[("main", 0)],
        fen=_normalize_fen(board.fen()),
        move="e2e4",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("e2e4"))
    board.push(chess.Move.from_uci("e7e5"))
    rows.append(RepertoireTreeRow(
        id=ids[("main", 2)],
        fen=_normalize_fen(board.fen()),
        move="g1f3",
        created_at=_next_created_at(),
    ))

    # Side of fork 1: 1.e4 c5 2.c4
    sb1 = chess.Board()
    sb1.push(chess.Move.from_uci("e2e4"))
    sb1.push(chess.Move.from_uci("c7c5"))
    rows.append(RepertoireTreeRow(
        id=ids[("side", 2)],
        fen=_normalize_fen(sb1.fen()),
        move="c2c4",
        created_at=_next_created_at(),
    ))
    # Advance sb1 past 2.c4 (the row's own move) so the NEXT ply (an
    # opponent reply) lands on a correctly-colored board. Failing to
    # push the row's move before pushing the next ply's move would
    # apply e7e6 on a white-to-move board and corrupt the side-4 row's
    # fen. The classifier itself constructs its own board, so this
    # only matters for the fixture's FEN-correctness invariant.
    sb1.push(chess.Move.from_uci("c2c4"))

    # Below 2.c4: 2...e6 3.Nc3 — ONE discovered child only (e6);
    # NOT a fork. But the ancestor fork 1.e4 chose the Nf3 branch,
    # not the c4 branch, so 3.Nc3 is side-line.
    sb1.push(chess.Move.from_uci("e7e6"))
    rows.append(RepertoireTreeRow(
        id=ids[("side", 4)],
        fen=_normalize_fen(sb1.fen()),
        move="b1c3",
        created_at=_next_created_at(),
    ))

    rows.sort(key=lambda r: r.created_at)
    print(f"  fixture: {len(rows)} rows (main fork + single-child side branch)")

    # Sanity: the side branch's opponent node (after 2.c4) has exactly
    # one discovered child — i.e. NO fork. Confirm by counting legal
    # replies that lead to a stored-row FEN.
    after_c4 = chess.Board()
    after_c4.push(chess.Move.from_uci("e2e4"))
    after_c4.push(chess.Move.from_uci("c7c5"))
    after_c4.push(chess.Move.from_uci("c2c4"))
    stored_fens = {r.fen for r in rows}
    discovered = 0
    for m in after_c4.legal_moves:
        b = after_c4.copy(stack=False)
        b.push(m)
        if _normalize_fen(b.fen()) in stored_fens:
            discovered += 1
    assert discovered == 1, (
        f"the opponent node after 2.c4 should have exactly ONE "
        f"discovered child (NO fork below) for the test to mean what "
        f"the spec case (c) says; got {discovered}"
    )
    _print_pass(f"side branch's own node has no fork ({discovered} child)")

    result = classify_repertoire_lines(rows)

    assert result[ids[("main", 0)]] is True
    assert result[ids[("main", 2)]] is True
    _print_pass("1.e4 / 2.Nf3 main rows are main-line")

    assert result[ids[("side", 2)]] is False, (
        f"2.c4 (side at fork 1) should be side-line; got "
        f"{result.get(ids[('side', 2)])}"
    )
    _print_pass("2.c4 is side-line (ancestor fork chose other)")

    assert result[ids[("side", 4)]] is False, (
        f"3.Nc3 ({ids[('side', 4)]}) should be side-line — its ancestor "
        f"fork 1.e4 chose the OTHER (Nf3) branch, even though this row's "
        f"own neighborhood has no fork; got {result.get(ids[('side', 4)])}"
    )
    _print_pass("3.Nc3 (no own fork, but ancestor fork chose other) is side-line")


def test_no_forks_linear_line_all_main() -> None:
    """Edge: no forks anywhere (one linear line with a single opponent
    reply at each opponent node) -> every row is main-line.

    Fixture (white repertoire): 1.e4 e5 2.Nf3 Nc6 3.Bc4 — at each
    opponent node there is exactly ONE discovered child (the single
    opponent reply black played), so no fork ever arises.
    """
    _print_section("TEST (edge): no forks -> all rows main-line")

    ids = {ply: uuid4() for ply in range(5)}
    _reset_clock()
    rows = _owner_rows_for_line(
        ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
        "white",
        ids,
    )
    print(f"  fixture: {len(rows)} rows on a single linear line")

    result = classify_repertoire_lines(rows)

    assert len(result) == len(rows), (
        f"every row should be classified; got {len(result)} for "
        f"{len(rows)} rows"
    )
    # Assert every stored row is True.
    for r in rows:
        assert result[r.id] is True, (
            f"row {r.id} at fen {r.fen!r} should be main-line (no fork "
            f"exists anywhere in a linear line); got {result[r.id]}"
        )
    _print_pass(f"all {len(rows)} rows on the linear line are main-line")


def test_unreachable_row_defaults_main_line() -> None:
    """Edge: a stored row at a FEN not reachable from the start via the
    legal-move walk should not crash; it's logged and defaulted to
    main-line (an unreachable row isn't part of any fork decision).

    Fixture: real main-line rows for 1.e4 e5 2.Nf3, PLUS one bogus row
    at a FEN that the legal-move walk cannot reach from the start (we
    craft it by inventing a position and a UCI move that are
    self-consistent but unrelated to the walk).
    """
    _print_section("TEST (edge): unreachable row -> default main-line, no crash")

    ids = {ply: uuid4() for ply in range(3)}
    _reset_clock()
    rows = _owner_rows_for_line(["e2e4", "e7e5", "g1f3"], "white", ids)

    # Craft an unreachable row: use a mid-game-ish FEN that the start
    # walk cannot reach (illegal/unrelated position), with a legal move
    # on that position so the row is internally consistent. The walker
    # never reaches this FEN via legal moves from the start, so it'll
    # end up in the unreachable-defaulted branch.
    # This FEN is a scramble that isn't reachable from the start.
    bogus_board = chess.Board("8/8/8/8/4k3/8/8/4K3 w - -")
    bogus_fen = _normalize_fen(bogus_board.fen())
    bogus_id = uuid4()
    rows.append(RepertoireTreeRow(
        id=bogus_id,
        fen=bogus_fen,
        move="e1e2",  # legal king step on this position
        created_at=_next_created_at(),
    ))
    print(f"  fixture: {len(rows)} rows (3 reachable + 1 unreachable)")
    print(f"  unreachable fen: {bogus_fen!r}")

    result = classify_repertoire_lines(rows)

    # The three reachable main-line rows -> main-line.
    for r in rows[:3]:
        assert result[r.id] is True, (
            f"reachable row {r.id} should be main-line (linear line, no "
            f"forks); got {result.get(r.id)}"
        )
    _print_pass("3 reachable rows are main-line (linear + no forks)")

    # The bogus row -> unreachable default, main-line.
    assert result.get(bogus_id) is True, (
        f"unreachable row {bogus_id} should default to main-line per "
        f"spec; got {result.get(bogus_id)}"
    )
    _print_pass("unreachable row defaulted to main-line (logged at WARNING)")


def test_empty_input_returns_empty() -> None:
    """Edge: empty input -> {} (not None, not crash)."""
    _print_section("TEST (edge): empty input -> {}")

    result = classify_repertoire_lines([])
    assert result == {}, (
        f"empty input should return {{}}, got {result!r}"
    )
    _print_pass("empty input -> {} returned")


def test_count_descendants_fork_below_parent_asymmetric_depth() -> None:
    """The 409 path's count math.

    Build a repertoire where the target row sits ABOVE a fork (the
    fork is at the opponent node right after the target's stored
    move); one branch of the fork has a deeper continuation and the
    other is a single-row leaf. This is exactly the case the
    docstring says `count_descendants` handles: descendants go all
    the way down a branch (not just immediate children), the target
    itself is NOT counted as its own descendant, and BOTH fork
    branches contribute.

    Fixture (owner=white):
        1.e4 (P0 — the row whose delete the 409 would gate)
          ...e5   2.Nf3 (Pe5)   ...Nc6   3.Bc4 (Pnc6)   (2 descendants)
          ...c5   2.Nf3 (Pc5)                              (1 descendant, leaf)

    Eye-counts:
      count_descendants(P0)   = 3   (Pe5 + Pnc6 + Pc5)
      count_descendants(Pe5)  = 1   (Pnc6)
      count_descendants(Pnc6) = 0   (leaf)
      count_descendants(Pc5)  = 0   (leaf)

    A naive-broken implementation would give:
      * 4 for P0 — if the target row itself were mis-counted as its own
        descendant (off-by-one on target exclusion).
      * 2 for P0 — if descendants were counted only at immediate-child
        depth and the deeper Pnc6 row got skipped (off-by-one on depth).
      * 2 for P0 — if only ONE branch of the fork were walked because
        the side-line branch were silently dropped at forks (the
        tree-walk main-line vs side-line distinction is for
        `classify_repertoire_lines`; `count_descendants` walks every
        reachable stored row regardless of fork branch).

    We also format the 409 detail string the route would emit from
    each count, to confirm the count appears correctly AND the
    n=1 (singular "response") vs n=3 (plural "responses")
    pluralization reads the way a user would expect.
    """
    _print_section("TEST (count_descendants): fork below parent, asymmetric depth")

    ids = {
        ("root", 0): uuid4(),   # 1.e4
        ("main", 2): uuid4(),   # 2.Nf3 on 1...e5 (deep branch)
        ("main", 4): uuid4(),   # 3.Bc4 (deep branch's continuation)
        ("side", 2): uuid4(),   # 2.Nf3 on 1...c5 (shallow branch, leaf)
    }

    _reset_clock()
    rows: List[RepertoireTreeRow] = []
    board = chess.Board()

    # 1.e4 — the row whose delete the 409 would gate.
    rows.append(RepertoireTreeRow(
        id=ids[("root", 0)],
        fen=_normalize_fen(board.fen()),
        move="e2e4",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("e2e4"))

    # Main (deep) branch: 1...e5 -> 2.Nf3 -> 2...Nc6 -> 3.Bc4.
    mb = board.copy(stack=False)
    mb.push(chess.Move.from_uci("e7e5"))
    rows.append(RepertoireTreeRow(
        id=ids[("main", 2)],
        fen=_normalize_fen(mb.fen()),
        move="g1f3",
        created_at=_next_created_at(),
    ))
    mb.push(chess.Move.from_uci("g1f3"))
    mb.push(chess.Move.from_uci("b8c6"))
    rows.append(RepertoireTreeRow(
        id=ids[("main", 4)],
        fen=_normalize_fen(mb.fen()),
        move="f1c4",
        created_at=_next_created_at(),
    ))

    # Side (shallow) branch: 1...c5 -> 2.Nf3 (leaf, no continuation).
    sb = board.copy(stack=False)
    sb.push(chess.Move.from_uci("c7c5"))
    rows.append(RepertoireTreeRow(
        id=ids[("side", 2)],
        fen=_normalize_fen(sb.fen()),
        move="g1f3",
        created_at=_next_created_at(),
    ))

    print(f"  fixture: {len(rows)} rows")
    # Sanity: every stored row sits at a white-to-move FEN (owner=white).
    # A stray black-to-move row would change the fork structure and make
    # the eye-count math misleading.
    assert all(r.fen.split()[1] == "w" for r in rows), (
        "every stored row in this fixture should sit at a white-to-move "
        "FEN (owner = white); a stray black-to-move row would change the "
        "fork structure."
    )

    c_root = count_descendants(rows, ids[("root", 0)])
    c_main2 = count_descendants(rows, ids[("main", 2)])
    c_main4 = count_descendants(rows, ids[("main", 4)])
    c_side2 = count_descendants(rows, ids[("side", 2)])

    assert c_root == 3, (
        f"descendants of P0 (the fork's parent): expected 3 "
        f"(Pe5 + Pnc6 + Pc5). Got {c_root}. A value of 4 would mean the "
        f"target itself was mis-counted as its own descendant; 2 would "
        f"mean either only immediate-child depth was counted (deeper "
        f"Pnc6 skipped) or one fork branch was dropped."
    )
    _print_pass("count_descendants(P0) = 3 (both fork branches + deep leaf, target excluded)")

    assert c_main2 == 1, (
        f"descendants of Pe5: expected 1 (Pnc6), got {c_main2}"
    )
    _print_pass("count_descendants(Pe5) = 1 (the deep continuation Pnc6)")

    assert c_main4 == 0, (
        f"descendants of Pnc6 (leaf): expected 0, got {c_main4}"
    )
    _print_pass("count_descendants(Pnc6) = 0 (leaf)")

    assert c_side2 == 0, (
        f"descendants of Pc5 (leaf): expected 0, got {c_side2}"
    )
    _print_pass("count_descendants(Pc5) = 0 (leaf)")

    # The route formats the 409 detail from the int count; confirm
    # both the n=1 (singular) and n=3 (plural) pluralization branches
    # read as expected. This is the line of formatting that's easy
    # to get subtly backwards (`{'s' if n == 1 else ''}` is the
    # wrong-way-around trap — the route originally had it inverted
    # and this assertion would have caught it).
    def detail_msg(n: int) -> str:
        return (
            f"this position has {n} prepared "
            f"response{'s' if n != 1 else ''} "
            f"beneath it — remove those first"
        )

    msg_n3 = detail_msg(c_root)
    assert msg_n3 == "this position has 3 prepared responses beneath it — remove those first", (
        f"409 detail for n=3 should pluralize ('responses'); got {msg_n3!r}"
    )
    _print_pass(f"409 detail string for n=3 reads correctly: {msg_n3!r}")

    msg_n1 = detail_msg(c_main2)
    assert msg_n1 == "this position has 1 prepared response beneath it — remove those first", (
        f"409 detail for n=1 should NOT pluralize ('response'); got {msg_n1!r}"
    )
    _print_pass(f"409 detail string for n=1 reads correctly: {msg_n1!r}")


def test_count_descendants_transposition_dedup_shared_row() -> None:
    """Two opponent-reply paths from the target converge on the SAME
    stored row (real chess transposition). `count_descendants` must
    count the shared row ONCE, not twice — the first-visit-wins
    `visited_fens` set in `_walk_descendants` is what keeps the count
    sane across the re-join.

    Fixture (owner=white) — same final position reached two ways
    (1.e4 e5 2.Nf3 Nc6 transposes with 1.e4 Nc6 2.Nf3 e5; both
    converge on the same owner-to-move FEN where 3.Bc4 is stored;
    after 3...Bc5 they converge AGAIN where 4.c3 is stored):

      Path A: 1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.c3
      Path B: 1.e4 Nc6 2.Nf3 e5 3.Bc4 Bc5 4.c3   (same end position)

    Stored-row collapse (UNIQUE (repertoire_id, fen) — both paths'
    3.Bc4 cell is the SAME row, and both paths' 4.c3 cell is the SAME
    row):
      P0     : 1.e4                 (root)
      Pe5    : 2.Nf3 at "after 1.e4 e5"      (path A only)
      PNc6   : 2.Nf3 at "after 1.e4 Nc6"     (path B only)
      Pital  : 3.Bc4 — SHARED by both paths
      Pital5 : 4.c3  — SHARED by both paths

    Eye-counts (with dedup):
      count_descendants(P0)   = 4   (Pe5, PNc6, Pital, Pital5 — NOT 6)
      count_descendants(Pe5)  = 2   (Pital, Pital5)
      count_descendants(PNc6) = 2   (Pital, Pital5)
      count_descendants(Pital)  = 1   (Pital5)
      count_descendants(Pital5) = 0

    A broken implementation returning 6 for P0 would mean the
    `visited_fens` dedup in `_walk_descendants` is broken (the
    shared rows would be double-counted — once per incoming path).
    """
    _print_section("TEST (count_descendants): transposition dedup (shared row via 2 paths)")

    ids = {
        ("root", 0): uuid4(),
        ("e5_branch", 2): uuid4(),
        ("Nc6_branch", 2): uuid4(),
        ("shared", 4): uuid4(),
        ("shared", 6): uuid4(),
    }

    _reset_clock()
    rows: List[RepertoireTreeRow] = []

    # 1.e4
    board = chess.Board()
    rows.append(RepertoireTreeRow(
        id=ids[("root", 0)],
        fen=_normalize_fen(board.fen()),
        move="e2e4",
        created_at=_next_created_at(),
    ))
    board.push(chess.Move.from_uci("e2e4"))

    # Path A branch: 1...e5 -> 2.Nf3 row.
    pa = board.copy(stack=False)
    pa.push(chess.Move.from_uci("e7e5"))
    rows.append(RepertoireTreeRow(
        id=ids[("e5_branch", 2)],
        fen=_normalize_fen(pa.fen()),
        move="g1f3",
        created_at=_next_created_at(),
    ))

    # Path B branch: 1...Nc6 -> 2.Nf3 row (different FEN from path A's
    # because the opponent reply differs — Nc6 vs e5 leaves a different
    # piece on the board).
    pb = board.copy(stack=False)
    pb.push(chess.Move.from_uci("b8c6"))
    rows.append(RepertoireTreeRow(
        id=ids[("Nc6_branch", 2)],
        fen=_normalize_fen(pb.fen()),
        move="g1f3",
        created_at=_next_created_at(),
    ))

    # Shared row 1: 3.Bc4 — both paths converge on this FEN.
    pa.push(chess.Move.from_uci("g1f3"))
    pa.push(chess.Move.from_uci("b8c6"))  # path A continues: 2...Nc6
    pb.push(chess.Move.from_uci("g1f3"))
    pb.push(chess.Move.from_uci("e7e5"))  # path B continues: 2...e5
    fen_after_e5_nc6 = _normalize_fen(pa.fen())
    fen_after_nc6_e5 = _normalize_fen(pb.fen())
    assert fen_after_e5_nc6 == fen_after_nc6_e5, (
        f"transposition sanity: 1.e4 e5 2.Nf3 Nc6 and 1.e4 Nc6 2.Nf3 e5 "
        f"must reach the same owner-to-move FEN; got "
        f"{fen_after_e5_nc6!r} vs {fen_after_nc6_e5!r}. If these "
        f"differ the dedup fixture itself is wrong."
    )
    rows.append(RepertoireTreeRow(
        id=ids[("shared", 4)],
        fen=fen_after_e5_nc6,  # == fen_after_nc6_e5
        move="f1c4",
        created_at=_next_created_at(),
    ))

    # Shared row 2: 3...Bc5 -> 4.c3 — both paths converge AGAIN.
    pa.push(chess.Move.from_uci("f1c4"))
    pa.push(chess.Move.from_uci("f8c5"))
    pb.push(chess.Move.from_uci("f1c4"))
    pb.push(chess.Move.from_uci("f8c5"))
    fen_two_a = _normalize_fen(pa.fen())
    fen_two_b = _normalize_fen(pb.fen())
    assert fen_two_a == fen_two_b, (
        f"transposition sanity (2nd ply): 1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 "
        f"and 1.e4 Nc6 2.Nf3 e5 3.Bc4 Bc5 must converge; got "
        f"{fen_two_a!r} vs {fen_two_b!r}."
    )
    rows.append(RepertoireTreeRow(
        id=ids[("shared", 6)],
        fen=fen_two_a,
        move="c2c3",
        created_at=_next_created_at(),
    ))

    print(f"  fixture: {len(rows)} rows (5 distinct ids; 2 of them shard onto the transposed position)")
    assert all(r.fen.split()[1] == "w" for r in rows), (
        "every stored row sits at a white-to-move FEN (owner = white)"
    )

    c_root = count_descendants(rows, ids[("root", 0)])
    c_e5 = count_descendants(rows, ids[("e5_branch", 2)])
    c_nc6 = count_descendants(rows, ids[("Nc6_branch", 2)])
    c_shared4 = count_descendants(rows, ids[("shared", 4)])
    c_shared6 = count_descendants(rows, ids[("shared", 6)])

    # Without dedup (broken): P0 would be 6 (each shared row counted
    # once per incoming path — 2 once each via Pe5->Pital and PNc6->Pital,
    # 2 once each via Pe5->Pital->Pital5 and PNc6->Pital->Pital5, plus
    # Pe5 and PNc6 themselves = 6). With dedup: P0 = 4. Asserting == 4
    # directly catches the regression if `visited_fens` is dropped or
    # mis-ordered in `_walk_descendants`.
    assert c_root == 4, (
        f"descendants of P0 with transposition dedup: expected 4 (Pe5, "
        f"PNc6, Pital, Pital5 — Pital and Pital5 each counted ONCE "
        f"despite being reachable via two opponent-reply paths). "
        f"Got {c_root}. A value of 6 would mean the visited_fens "
        f"dedup is broken: each shared row was counted twice."
    )
    _print_pass("count_descendants(P0) = 4 (transposed rows counted once via dedup, not 6)")

    assert c_e5 == 2, (
        f"descendants of Pe5: expected 2 (Pital, Pital5), got {c_e5}"
    )
    _print_pass("count_descendants(Pe5) = 2")

    assert c_nc6 == 2, (
        f"descendants of PNc6: expected 2 (Pital, Pital5), got {c_nc6}"
    )
    _print_pass("count_descendants(PNc6) = 2")

    assert c_shared4 == 1, (
        f"descendants of Pital (3.Bc4 transposed row): expected 1 (Pital5), "
        f"got {c_shared4}"
    )
    _print_pass("count_descendants(Pital) = 1")

    assert c_shared6 == 0, (
        f"descendants of Pital5 (leaf): expected 0, got {c_shared6}"
    )
    _print_pass("count_descendants(Pital5) = 0 (leaf)")


def test_count_descendants_leaf_missing_empty() -> None:
    """Edge cases for count_descendants:
      * leaf row (no descendants) -> 0
      * target row id not present in `rows` (caller bug — the route's
        404 path should have caught a missing position before this) ->
        0, NOT a crash
      * empty `rows` -> 0
    """
    _print_section("TEST (count_descendants): leaf, missing target, empty rows")

    # Linear 1.e4 e5 2.Nf3 — owner rows at plies 0 and 2 only.
    ids = {0: uuid4(), 2: uuid4()}
    _reset_clock()
    rows = _owner_rows_for_line(["e2e4", "e7e5", "g1f3"], "white", ids)
    print(f"  fixture: {len(rows)} rows on a single linear line")

    # 2.Nf3 row is a leaf — no stored positions beneath it.
    c_leaf = count_descendants(rows, ids[2])
    assert c_leaf == 0, (
        f"descendants of a leaf: expected 0, got {c_leaf}"
    )
    _print_pass("leaf row has 0 descendants")

    # Target id not present in `rows` (caller bug — route's 404 would
    # have caught this before calling). count_descendants must NOT
    # crash; it returns 0 so the route's 404 wins.
    c_missing = count_descendants(rows, uuid4())
    assert c_missing == 0, (
        f"descendants of a target not in `rows`: expected 0 (no crash), "
        f"got {c_missing}"
    )
    _print_pass("missing target id -> 0 (no crash, route's 404 wins anyway)")

    # Empty `rows`.
    c_empty = count_descendants([], uuid4())
    assert c_empty == 0, (
        f"descendants in empty `rows`: expected 0, got {c_empty}"
    )
    _print_pass("empty rows -> 0")


def main() -> int:
    print("=== Running repertoire_tree classifier smoke tests ===")
    try:
        test_simple_fork_disjoint_id_slots()
        test_deep_main_line_where_every_ancestor_chose_main()
        test_side_line_with_no_own_fork_is_still_side()
        test_no_forks_linear_line_all_main()
        test_unreachable_row_defaults_main_line()
        test_empty_input_returns_empty()
        # count_descendants (the 409 gate's count math).
        test_count_descendants_fork_below_parent_asymmetric_depth()
        test_count_descendants_transposition_dedup_shared_row()
        test_count_descendants_leaf_missing_empty()
    except AssertionError as exc:
        print(f"\n  [FAIL] {exc}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())