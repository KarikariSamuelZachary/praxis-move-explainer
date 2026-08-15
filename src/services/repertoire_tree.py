"""Repertoire tree-walk: classify each stored position as main-line or
side-line.

The repertoire_positions table stores rows as (repertoire_id, fen, move)
with a UNIQUE (repertoire_id, fen) constraint but NO parent/child edges
— the tree is derived on read via a python-chess FEN-walk. This module
replays the whole repertoire from `chess.STARTING_FEN`, discovering
children by either:
  * the single stored move at an owner-turn position (deterministic —
    exactly one move per stored row by the UNIQUE constraint), or
  * every legal opponent reply at an opponent-turn position, checking
    each resulting normalized FEN against the stored-row set.

A "fork" is an opponent-turn position where MORE THAN ONE legal reply
leads to a distinct stored row. Among a fork's children, the one with
the EARLIEST created_at is the main-line continuation; every other
child at that fork is side-line. (created_at ties are broken by the
row id's string form, deterministic but stable — ties essentially
never happen under gen_random_uuid() + real wall-clock writes, and the
tiebreak exists only so a never-committed-deterministic test stays
deterministic.)

A position is main-line IFF every fork on its path from the start
chose it (or one of its ancestors) as the main-line branch — i.e.
the stored row sits on the single root-to-leaf path produced by
"follow the stored move at owner nodes; at opponent nodes take the
earliest-created child at forks, the sole child otherwise." A position
reachable only via a side-line branch is side-line even if its own
immediate neighborhood has no fork.

Transposition handling: the main-line subtree of any fork is fully
recursed BEFORE that fork's side-line children (sorted by created_at).
The walk uses first-visit-wins via a `visited_fens` set, so a stored
row reachable from BOTH a main branch and a side branch keeps the
main-line assignment it received on its first (main-line) visit —

the side-line subtree's re-entry into that FEN is a no-op. This is
what gives the spec its "every fork on its path chose it" single-path
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
  * Owner color is INFERRED from the input rows (every stored row's
    FEN has the owner on move per the upsert contract), so the function
    needs no color argument and stays pure over its row inputs.

Out of scope (separate task):
  * Wiring this into the GET queue endpoint's "train main lines only"
    filter.
  * Repertoires with a non-chess.STARTING_FEN root (those aren't a v1
    concern per the original schema decision; upsert still anchors on
    the standard start).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Sequence
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
    # The UNIQUE (repertoire_id, fen) constraint guarantees at most one
    # row per FEN, so this dict's value is a single row, not a list.
    fen_to_row: Dict[str, RepertoireTreeRow] = {}
    for r in rows:
        fen_to_row[r.fen] = r

    # Owner color: every stored row's FEN has the owner on move (the
    # upsert contract — only owner-turn plies produce rows). Infer the
    # owner's color from any row; use the start FEN's side-to-move
    # alone if no rows exist yet (the `if not rows: return` above
    # guarantees at least one row here, but the fallback keeps the
    # inference robust).
    owner_color = _infer_owner_color(fen_to_row)

    # FENs already visited by the walk. First-visit-wins: a transposed
    # position keeps the assignment its FIRST visit got. Since the
    # main-line subtree of any fork is recursed before that fork's
    # side-line children, the first visit is always the main-line one
    # when both are reachable — so a position reachable from both keeps
    # main-line. `chess.Board` cycles (threefold repetition etc.) are
    # also covered: a re-entry into a visited FEN is a no-op rather
    # than infinite recursion.
    visited_fens: set = set()

    _walk_from_start(fen_to_row, owner_color, visited_fens, result)

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


def _infer_owner_color(fen_to_row: Dict[str, RepertoireTreeRow]) -> chess.Color:
    """Return the repertoire owner's color as a python-chess Color
    (True=white, False=black), inferred from any stored row's FEN.

    Every stored row's FEN has the owner on move (the upsert contract:
    only owner-turn plies produce rows), so the side-to-move field of
    any row's normalized FEN IS the owner's color. We pick an
    arbitrary row — which one doesn't matter, the contract says they
    all agree. Falls back to white (the start-side-to-move) if the
    dict is somehow empty.
    """
    if not fen_to_row:
        return chess.WHITE
    any_fen = next(iter(fen_to_row))
    side = any_fen.split()[1]
    return side == "w"


def _walk_from_start(
    fen_to_row: Dict[str, RepertoireTreeRow],
    owner_color: chess.Color,
    visited_fens: set,
    result: Dict[UUID, bool],
) -> None:
    """Kick off the DFS from `chess.STARTING_FEN` with is_main=True.

    The start FEN itself is NOT a stored row (per the schema, the
    stored row at the start FEN — if one exists — represents the
    owner's FIRST move, which is an owner-turn node). So the root
    board is passed to `_walk_node` as-is; that helper decides owner
    vs opponent turn from `board.turn`.
    """
    board = chess.Board()  # standard start FEN
    _walk_node(board, fen_to_row, owner_color, is_main=True, visited_fens=visited_fens, result=result)


def _walk_node(
    board: chess.Board,
    fen_to_row: Dict[str, RepertoireTreeRow],
    owner_color: chess.Color,
    is_main: bool,
    visited_fens: set,
    result: Dict[UUID, bool],
) -> None:
    """Visit the current board position and recurse into children.

    Dispatch on whose turn it is:
      * owner to move -> owner-turn node: there is at most one stored
        row at this FEN (UNIQUE constraint). If present, mark the row
        with `is_main` and recurse into the single child position
        produced by pushing the row's stored UCI move (carrying the
        same `is_main`). If absent (gap) -> dead end.
      * opponent to move -> opponent-turn node: enumerate every legal
        reply; keep those whose resulting FEN matches a stored row
        (discovered children). 0 children -> dead end. 1 -> not a
        fork, recurse with the same `is_main`. >1 -> FORK: recurse
        into the earliest-created child FIRST (carrying `is_main`),
        then every other child (carrying is_main=False).

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

    is_owner_turn = board.turn == owner_color

    if is_owner_turn:
        row = fen_to_row.get(fen)
        if row is None:
            # Gap: the owner has no prepared move here. Dead end.
            return
        result[row.id] = is_main
        child = _push_stored_move(board, row)
        if child is None:
            # Stale/corrupt stored move — warn and stop this branch.
            # The row's own classification was already recorded above.
            return
        _walk_node(child, fen_to_row, owner_color, is_main, visited_fens, result)
        return

    # Opponent turn: discover children among legal replies.
    children: list = []  # list of (child_board, child_row)
    for legal in board.legal_moves:
        child_board = board.copy(stack=False)
        child_board.push(legal)
        child_fen = _normalize_fen(child_board.fen())
        child_row = fen_to_row.get(child_fen)
        if child_row is not None:
            children.append((child_board, child_row))

    if not children:
        # No prepared response to any opponent reply here: dead end.
        return

    if len(children) == 1:
        # Not a fork: single discovered child, carry the same flag.
        child_board, _child_row = children[0]
        _walk_node(child_board, fen_to_row, owner_color, is_main, visited_fens, result)
        return

    # FORK: >1 discovered children. Earliest created_at is the
    # main-line continuation; ties broken by string-form row id.
    children.sort(key=lambda pair: (pair[1].created_at, str(pair[1].id)))

    # Recurse the main-line child FIRST (carries the parent's
    # is_main), so a transposed FEN reachable from both branches
    # takes its main-line assignment on first visit. Then recurse
    # the side-line children with is_main=False.
    main_board, _main_row = children[0]
    _walk_node(main_board, fen_to_row, owner_color, is_main, visited_fens, result)
    for side_board, _side_row in children[1:]:
        _walk_node(side_board, fen_to_row, owner_color, False, visited_fens, result)


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
    that R' is reachable from R by the same walk semantics the rest of
    this module uses: push R's stored UCI move at owner-turn nodes
    (deterministic single child, since (repertoire_id, fen) is UNIQUE),
    enumerate legal opponent replies at opponent-turn nodes and follow
    every reply whose resulting FEN matches a stored row.

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
    how the start-position replay reached the target. We then mirror
    `_walk_node`'s dispatch (owner turn: single stored move -> push
    and recurse; opponent turn: enumerate legal moves, follow each
    one whose resulting FEN matches a stored row). The target row
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
    # The UNIQUE (repertoire_id, fen) constraint guarantees at most one
    # row per FEN — same assumption `_walk_node` relies on.
    fen_to_row: Dict[str, RepertoireTreeRow] = {}
    for r in rows:
        fen_to_row[r.fen] = r

    owner_color = _infer_owner_color(fen_to_row)

    # Walk descendants from the reconstructed target board. The target
    # row itself is excluded from the count because its stored FEN
    # (which is a different FEN from target_board.fen() — the latter
    # is the post-move position) sits behind the walk: the walk
    # starts at `target_board.fen()` and only moves FORWARD, so it
    # can't loop back to the target's stored FEN through legal moves
    # alone. First-visit-wins in `_walk_descendants` handles cycles
    # (transpositions) inside the descendant subtree correctly.
    #
    # We deliberately do NOT pre-seed `target_board.fen()` into
    # `visited_fens` — that would make the walk's first call bail out
    # immediately. The walk processes the start position like any
    # other position; it just doesn't happen to have a stored row
    # there (the stored row IS the parent of this position by
    # construction).
    visited_fens: set = set()
    descendants: Dict[UUID, bool] = {}
    _walk_descendants(
        target_board,
        fen_to_row,
        owner_color,
        visited_fens,
        descendants,
    )

    return len(descendants)


def _walk_descendants(
    board: chess.Board,
    fen_to_row: Dict[str, RepertoireTreeRow],
    owner_color: chess.Color,
    visited_fens: set,
    descendants: Dict[UUID, bool],
) -> None:
    """Forward walk from `board` (which sits AT the target's board
    state, already excluded from `visited_fens`) collecting every
    stored row reachable from here. Mirrors `_walk_node`'s dispatch
    exactly so reachability here matches reachability in
    `classify_repertoire_lines` and in the gap-finder.

    A stored row encountered is added to `descendants` and the walk
    continues from it. First-visit-wins via `visited_fens` keeps the
    count well-defined under transpositions inside the descendant
    subtree.
    """
    fen = _normalize_fen(board.fen())
    if fen in visited_fens:
        return
    visited_fens.add(fen)

    is_owner_turn = board.turn == owner_color
    if is_owner_turn:
        row = fen_to_row.get(fen)
        if row is None:
            return
        descendants[row.id] = True
        child = _push_stored_move(board, row)
        if child is None:
            return
        _walk_descendants(child, fen_to_row, owner_color, visited_fens, descendants)
        return

    # Opponent turn: enumerate stored children among legal replies.
    for legal in board.legal_moves:
        child = board.copy(stack=False)
        child.push(legal)
        child_fen = _normalize_fen(child.fen())
        if child_fen in visited_fens:
            continue
        child_row = fen_to_row.get(child_fen)
        if child_row is None:
            continue
        _walk_descendants(child, fen_to_row, owner_color, visited_fens, descendants)