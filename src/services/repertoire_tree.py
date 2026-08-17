"""Repertoire tree-walk: classify each stored position as main-line or
side-line.

The repertoire_positions table stores rows as (repertoire_id, fen, move)
with a UNIQUE (repertoire_id, fen, move) constraint but NO parent/child
edges — the tree is derived on read via a python-chess FEN-walk. This
module replays the whole repertoire from `chess.STARTING_FEN`,
discovering children by reading the stored rows AT the current FEN —
one outgoing edge per row. Owner and opponent rows are treated
symmetrically: the writer stores a row for EVERY ply (both colors), so
the tree at any FEN is simply ALL rows stored there, regardless of
which side is on move.

A "fork" is ANY position where MORE THAN ONE outgoing edge exists —
several prepared opponent replies, several saved owner moves from the
same position, or any mix. Among a fork's children, the one with the
EARLIEST created_at is the main-line continuation; every other child
at that fork is side-line. (created_at ties are broken by the row id's
string form, deterministic but stable — ties essentially never happen
under gen_random_uuid() + real wall-clock writes, and the tiebreak
exists only so a never-committed-deterministic test stays deterministic.)

A position is main-line IFF every fork on its path from the start chose
it (or one of its ancestors) as the main-line branch — i.e. the stored
row sits on the single root-to-leaf path produced by "follow the
earliest-created saved move at every node." A position reachable only
via a side-line branch is side-line even if its own immediate
neighborhood has no fork.

Transposition handling: the main-line subtree of any fork is fully
recursed BEFORE that fork's side-line children (sorted by created_at).
The walk uses first-visit-wins via a `visited_fens` set, so a stored
row reachable from BOTH a main branch and a side branch keeps the
main-line assignment it received on its first (main-line) visit — the
side-line subtree's re-entry into that FEN is a no-op. This is what
gives the spec its "every fork on its path chose it" single-path
semantics across transpositions.

Pattern parity with the rest of this feature:
  * Pure function: caller SELECTs (id, fen, move, created_at) for
    every row in one repertoire and passes them in — this module takes
    NO `conn` and touches NO database, so it's independently testable
    the same way `_replay_and_plan` is (see repertoire_service_test.py).
  * FEN normalization reuses `_normalize_fen` from
    `services/repertoire_service` — the same helper `repertoire_gaps`
    already imports (precedent in this exact codebase now, two files
    deep), so the 4-field canonicalization stays a single source of
    truth shared by the write path, the gap-finder, and this walker.

Out of scope (separate task):
  * Wiring this into the GET queue endpoint's "train main lines only"
    filter (the router applies the classifier's row-id classification,
    then filters by side-to-move for owner-only training rows).
  * Repertoires with a non-chess.STARTING_FEN root (those aren't a v1
    concern per the original schema decision; upsert still anchors on
    the standard start).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Sequence
from uuid import UUID

import chess

from services.repertoire_service import _normalize_fen

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepertoireTreeRow:
    """The four columns the tree-walk needs from a repertoire_positions
    row. The caller SELECTs exactly these and passes them in (rather
    than this module taking a conn) so the walker stays pure and is
    independently testable without a fake DB.

    `fen` MUST be the 4-field normalized form the writer persists
    (board, side-to-move, castling rights, en passant square — see
    `_normalize_fen`). The caller gets this for free from a plain
    `SELECT id, fen, move, created_at FROM repertoire_positions`; no
    re-normalization is needed.
    """
    id: UUID
    fen: str
    move: str  # UCI string ("e2e4", "e7e8q"); NOT SAN.
    created_at: object  # datetime; typed loosely so tests can use
                        # any orderable stand-in (e.g. ints) without
                        # importing datetime just for the dataclass.


# Rows indexed by their stored (already-4-field-normalized) FEN. The
# UNIQUE (repertoire_id, fen, move) constraint allows SEVERAL rows per
# FEN — one per saved move — so every lookup yields a LIST (possibly
# empty). Sort order for "earliest row at a FEN" is
# (created_at, str(id)), the module-wide fork tiebreak.
FenRowMap = Dict[str, List[RepertoireTreeRow]]


def _rows_sorted(rows: List[RepertoireTreeRow]) -> List[RepertoireTreeRow]:
    """Rows ordered by the module-wide (created_at, str(id)) tiebreak."""
    return sorted(rows, key=lambda r: (r.created_at, str(r.id)))


def classify_repertoire_lines(
    rows: Sequence[RepertoireTreeRow],
) -> Dict[UUID, bool]:
    """Classify every stored row in one repertoire as main-line or
    side-line.

    Returns a `{row_id -> is_main_line: bool}` mapping for every row
    in `rows`. A row is main-line (True) iff every fork on its path
    from `chess.STARTING_FEN` chose it (or one of its ancestors) as
    the main-line branch. The main-line branch at a fork is the child
    with the EARLIEST created_at; ties break by string-form row id.

    Edge cases (per spec):
      * No forks anywhere (one linear line) -> every row main-line.
      * Empty input -> `{}`.
      * Unreachable row (its FEN isn't reachable from the start via
        the legal-move walk — shouldn't happen if
        upsert_repertoire_positions is the only writer, but doesn't
        crash) -> logged at WARNING and defaulted to main-line (an
        unreachable row isn't part of any fork decision).

    The walk is a DFS that carries an `is_main` flag down the tree.
    It starts True; the flag flips to False the moment the walk
    recurses into any fork's non-earliest (side-line) child and stays
    False for that whole subtree. At a fork, the earliest-created
    child's subtree is recursed FIRST (with is_main=True), then the
    side-line children — so a transposed FEN reachable from both sees
    its main-line assignment first and keeps it (the `visited_fens`
    set makes later side-line re-entry a no-op).
    """
    result: Dict[UUID, bool] = {}

    if not rows:
        return result

    # Index rows by their stored (already-4-field-normalized) FEN.
    # Several rows may share a FEN (one per saved move — a diverging
    # repertoire), so the value is a LIST.
    fen_to_rows: FenRowMap = {}
    for r in rows:
        fen_to_rows.setdefault(r.fen, []).append(r)

    # FENs already visited by the walk. First-visit-wins: a transposed
    # position keeps the assignment its FIRST visit got. Since the
    # main-line subtree of any fork is recursed before that fork's
    # side-line children, the first visit is always the main-line one
    # when both are reachable — so a position reachable from both keeps
    # main-line. `chess.Board` cycles (threefold repetition etc.) are
    # also covered: a re-entry into a visited FEN is a no-op rather
    # than infinite recursion. Visiting a FEN classifies ALL rows at
    # it in one shot, so a later re-entry can never miss a row.
    visited_fens: set = set()

    _walk_from_start(fen_to_rows, visited_fens, result)

    # Any row not in `result` is unreachable from the start via the
    # legal-move walk. Spec: log a warning and default to main-line.
    for r in rows:
        if r.id not in result:
            log.warning(
                "repertoire position %s at fen %r is unreachable from "
                "the start position via the legal-move walk; defaulting "
                "to main-line. This shouldn't happen if "
                "upsert_repertoire_positions is the only writer.",
                r.id,
                r.fen,
            )
            result[r.id] = True

    return result


def _walk_from_start(
    fen_to_rows: FenRowMap,
    visited_fens: set,
    result: Dict[UUID, bool],
) -> None:
    """Kick off the DFS from `chess.STARTING_FEN` with is_main=True.

    The start FEN itself can be a stored row (the row represents the
    owner's FIRST move, which is the first ply to push from the start
    board). The root board is passed to `_walk_node` as-is; that
    helper reads stored rows at the current FEN and dispatches forks
    vs single-edge nodes.
    """
    board = chess.Board()  # standard start FEN
    _walk_node(board, fen_to_rows, is_main=True, visited_fens=visited_fens, result=result)


def _walk_node(
    board: chess.Board,
    fen_to_rows: FenRowMap,
    is_main: bool,
    visited_fens: set,
    result: Dict[UUID, bool],
) -> None:
    """Visit the current board position and recurse into children.

    The outgoing edges from this position are simply the stored rows
    AT this FEN — owner rows + opponent rows alike (the writer stores
    a row for EVERY ply). 0 rows (gap) -> dead end. 1 row -> not a
    fork, carry the same `is_main`. >1 rows -> FORK (a diverging
    position — multiple prepared moves from here, whether by the
    owner OR by the opponent as prepared replies): the earliest-
    created row's subtree carries `is_main`, every other row is a
    side-line branch (classified False and recursed with is_main=False).

    A stored row's UCI move push is guarded by try/except: if a row
    is somehow stale/corrupt (illegal move on its FEN — shouldn't
    happen since the writer validates via board.parse_uci, but a row
    could in principle be moved-by-constraint-changed out from under
    us), we log a warning and stop that branch rather than crashing
    the whole walk. The row still gets marked with the current
    `is_main` (it was reachable as a FEN) before the push is
    attempted, so a corrupt row's own classification is stable.
    """
    fen = _normalize_fen(board.fen())

    if fen in visited_fens:
        # First-visit-wins. This FEN was already classified via an
        # earlier (main-line) path; re-entering it changes nothing.
        return
    visited_fens.add(fen)

    rows_here = _rows_sorted(fen_to_rows.get(fen, []))
    if not rows_here:
        # Gap: no prepared moves here. Dead end.
        return

    if len(rows_here) == 1:
        # Not a fork: single saved move, carry the same flag.
        row = rows_here[0]
        result[row.id] = is_main
        child = _push_stored_move(board, row)
        if child is None:
            return
        _walk_node(child, fen_to_rows, is_main, visited_fens, result)
        return

    # FORK: >1 saved moves from this position. The earliest-created
    # row continues the line the user built first; the rest are
    # side-line branches.
    main_row = rows_here[0]
    result[main_row.id] = is_main
    main_child = _push_stored_move(board, main_row)
    if main_child is not None:
        _walk_node(main_child, fen_to_rows, is_main, visited_fens, result)
    for side_row in rows_here[1:]:
        result[side_row.id] = False
        side_child = _push_stored_move(board, side_row)
        if side_child is None:
            continue
        _walk_node(side_child, fen_to_rows, False, visited_fens, result)


def _push_stored_move(board: chess.Board, row: RepertoireTreeRow):
    """Return a fresh board with the row's UCI move pushed, or None if
    the move is illegal on the board (stale/corrupt row).

    Uses `board.parse_uci` (validate-then-push) rather than
    `Move.from_uci` + `board.push` — the latter raises a bare
    AssertionError for not-pseudo-legal moves, which would bypass the
    ValueError handler. This mirrors the convention in
    `repertoire_service._replay_and_plan`.
    """
    child = board.copy(stack=False)
    try:
        move = child.parse_uci(row.move)
    except ValueError as exc:
        log.warning(
            "repertoire position %s has stale/corrupt stored move %r "
            "illegal on its fen %r: %s — stopping that branch.",
            row.id,
            row.move,
            row.fen,
            exc,
        )
        return None
    child.push(move)
    return child


def count_descendants(
    rows: Sequence[RepertoireTreeRow],
    target_row_id: UUID,
) -> int:
    """Count the stored rows that descend from `target_row_id`.

    A "descendant" of a stored row R is any OTHER stored row R' such
    that R' is reachable from R by walking the tree forward: push
    each saved UCI move at every node (one edge per stored row — a
    diverging repertoire contributes every branch). The walk treats
    owner and opponent rows symmetrically (the writer stores both).
    The target row itself is NOT counted as its own descendant.

    Purpose: the per-position DELETE endpoint in `routers/repertoire.py`
    needs to refuse the delete (with 409 Conflict) when the target
    position has prepared responses further down the line — silently
    deleting it would orphan those descendants: their stored rows
    would still exist in the DB but become unreachable from the
    tree-walk (no parent row points to them anymore — FENs alone
    aren't edges in this schema). A silent cascade-delete of
    descendants would be far riskier than the user asked for; a
    silent orphan-leave would surface as a confusing "this position
    exists but the gap-finder can't reach it" downstream. Detecting
    the case up front and refusing with a count in the 409 detail
    lets the UI prompt "remove those first."

    Implementation: a focused forward walk starting from the target
    row's board state. The target's board state is reconstructed
    directly from the target's own data — `chess.Board(target.fen)`
    with `target.move` pushed — so the walk doesn't depend on knowing
    how the start-position replay reached the target. The target row
    itself is NOT counted as its own descendant. First-visit-wins
    via a visited set (matches `_walk_node`) — a row reachable from
    the target via multiple paths (transpositions inside the
    descendant subtree) is counted once.

    Returns 0 if the target row has no descendants, if the target
    row is not present in `rows` (caller bug — the route's 404 path
    should have caught that before this is called), or if the
    target's stored FEN doesn't parse as a chess position (an
    unreachable row: the same case `classify_repertoire_lines`
    warns about). Returns 0 for an empty `rows`.

    Pure: takes no `conn`, no DB access, deterministic. Mirrors
    `classify_repertoire_lines`'s testing surface — the test file
    `repertoire_tree_test.py` exercises both via the same
    `_owner_rows_for_line` fixture builder.

    Edge cases (matching `_walk_node`'s behavior):
      * Stale/corrupt stored move at a descendant (illegal on its
        FEN) -> that branch stops at the corruption; siblings still
        recurse. We do NOT silently drop the stale row from the
        count — it was still reached, the user's stored FEN exists,
        and it represents something the writer put there. We just
        stop following THAT branch.
      * The target row's own stale move -> the walk never starts;
        no descendants are reported (0). This is consistent with
        `_walk_node` logging the corruption and treating the node as
        a dead end.
    """
    if not rows:
        return 0

    # Find the target row.
    target: RepertoireTreeRow | None = None
    for r in rows:
        if r.id == target_row_id:
            target = r
            break
    if target is None:
        # Caller bug — the target row should already be confirmed in
        # repertoire_positions by the caller. Don't crash; return 0 so
        # the route's 404 (which surfaces via the ownership check on a
        # separate SELECT) still wins.
        return 0

    # Reconstruct the board state AT the target's stored FEN. We can
    # do this directly from the target row's own data because the
    # descendants are, by definition, FORWARD of the target — the walk
    # only needs to know what board state the target IS in, not how
    # the start-position replay got there. The target's board is
    # `chess.Board(target.fen)` with `target.move` pushed; we don't
    # need to reconstruct the path from start.
    try:
        target_board = chess.Board(target.fen)
    except ValueError:
        # Stored FEN doesn't parse as a chess position — this is the
        # "unreachable from start" case `classify_repertoire_lines`
        # warns about. No reachable descendants either; report 0 so
        # the caller can still delete the (unreachable) row.
        log.warning(
            "count_descendants: target row %s has unparseable stored "
            "fen %r; reporting 0 descendants.",
            target.id,
            target.fen,
        )
        return 0
    try:
        move = target_board.parse_uci(target.move)
    except ValueError as exc:
        # The target's own stored move is illegal on its stored FEN
        # (stale/corrupt row). Consistent with `_walk_node`: log and
        # treat the node as a dead end. No descendants.
        log.warning(
            "count_descendants: target row %s has stale/corrupt "
            "stored move %r illegal on its fen %r: %s — reporting 0 "
            "descendants.",
            target.id,
            target.move,
            target.fen,
            exc,
        )
        return 0
    target_board.push(move)

    # Index rows by their stored (already-4-field-normalized) FEN.
    # Several rows may share a FEN (one per saved move) — same
    # assumption `_walk_node` relies on.
    fen_to_rows: FenRowMap = {}
    for r in rows:
        fen_to_rows.setdefault(r.fen, []).append(r)

    # Walk descendants from the reconstructed target board. The target
    # row itself is excluded from the count because its stored FEN
    # (which is a different FEN from target_board.fen() — the latter
    # is the post-move position) sits behind the walk: the walk starts
    # at `target_board.fen()` and only moves FORWARD, so it can't loop
    # back to the target's stored FEN through legal moves alone.
    # First-visit-wins in `_walk_descendants` handles cycles
    # (transpositions) inside the descendant subtree correctly.
    visited_fens: set = set()
    descendants: Dict[UUID, bool] = {}
    _walk_descendants(target_board, fen_to_rows, visited_fens, descendants)

    return len(descendants)


def _walk_descendants(
    board: chess.Board,
    fen_to_rows: FenRowMap,
    visited_fens: set,
    descendants: Dict[UUID, bool],
) -> None:
    """Forward walk from `board` (which sits AT the target's board
    state, already excluded from `visited_fens`) collecting every
    stored row reachable from here. Mirrors `_walk_node`'s simplified
    dispatch: the outgoing edges from any position are simply the
    stored rows at that FEN (owner and opponent alike).

    Every stored row ENCOUNTERED at a visited FEN is added to
    `descendants` (all of them — a FEN can hold several saved moves)
    and the walk continues down each row's child position.
    First-visit-wins via `visited_fens` keeps the count well-defined
    under transpositions inside the descendant subtree.
    """
    fen = _normalize_fen(board.fen())
    if fen in visited_fens:
        return
    visited_fens.add(fen)

    rows_here = fen_to_rows.get(fen, [])
    if not rows_here:
        return

    for row in rows_here:
        descendants[row.id] = True
        child = _push_stored_move(board, row)
        if child is None:
            continue
        _walk_descendants(child, fen_to_rows, visited_fens, descendants)