import { Chess, type Square } from 'chess.js';

// Pure, framework-free helpers for the repertoire Train session. Kept in
// their own module (no React, no Next.js) so they can be unit-tested
// against synthetic repertoires - the session walker depends on these
// invariants holding.

export type RepertoireColor = 'white' | 'black';

export type RepertoirePositionRow = {
  id: string;
  repertoire_id: string;
  fen: string;
  move: string;
  due: string;
  stability: number | null;
  difficulty: number | null;
  state: string;
  step: number | null;
  reps: number;
  lapses: number;
  last_review: string | null;
  created_at: string;
  updated_at: string;
};

export const START_FEN =
  'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -';

// 4-field FEN (matches `_normalize_fen` in services/repertoire_service.py).
// Stored FENs in repertoire_positions are normalized to 4 fields, so
// comparing positions uses these keys.
export function normalizeFen(fen: string): string {
  return fen.split(/\s+/).slice(0, 4).join(' ');
}

// Apply a UCI move to a (4-field) FEN; returns the resulting 4-field
// FEN, or null if chess.js rejects the move (illegal / corrupt). The
// en-passant field is KEPT as the board reports it (chess.js only emits
// a target when a legal en-passant capture exists), matching the FEN
// convention `_normalize_fen` persists server-side. Forcing '-' here
// used to make a reply lookup miss whenever the played move was a
// two-square pawn push that enabled an en-passant capture.
export function applyUci(fen4: string, uci: string): string | null {
  try {
    const game = new Chess(`${fen4} 0 1`);
    const played = game.move({
      from: uci.slice(0, 2) as Square,
      to: uci.slice(2, 4) as Square,
      promotion: uci.length > 4 ? uci[4] : undefined,
    });
    if (!played) return null;
    return normalizeFen(game.fen());
  } catch {
    return null;
  }
}

// Reconstruct the sequence of positions from the standard start to a
// target FEN, stepping through the session's stored (fen, move) rows
// (BFS over the stored edges - a diverging repertoire can offer several
// moves at one FEN). Returns a list of normalized FENs starting at the
// start position and ending AT the target. Empty list if the target is
// unreachable from the start via the stored rows.
export function findLinePath(
  rows: RepertoirePositionRow[],
  targetFen: string
): string[] {
  const edges = new Map<string, string[]>();
  for (const r of rows) {
    const from = normalizeFen(r.fen);
    const to = applyUci(from, r.move);
    if (!to) continue;
    const list = edges.get(from) ?? [];
    list.push(normalizeFen(to));
    edges.set(from, list);
  }
  const startKey = normalizeFen(START_FEN);
  const targetKey = normalizeFen(targetFen);
  const queue: string[][] = [[startKey]];
  const visited = new Set<string>([startKey]);
  while (queue.length > 0) {
    const path = queue.shift() as string[];
    const current = path[path.length - 1];
    if (current === targetKey) return path;
    for (const next of edges.get(current) ?? []) {
      if (visited.has(next)) continue;
      visited.add(next);
      queue.push([...path, next]);
    }
  }
  return [];
}

// Stable row order: earliest created first, id as the deterministic
// tiebreak. Matches the backend classifier's fork order
// (created_at, str(id)) and the session-order convention.
function byCreatedThenId(
  a: RepertoirePositionRow,
  b: RepertoirePositionRow
): number {
  return a.created_at === b.created_at
    ? a.id.localeCompare(b.id)
    : a.created_at.localeCompare(b.created_at);
}

// Quiz items: every owner-side row is one quiz item (one per saved
// owner move - diverging moves at the same FEN are ALL quizzed, never
// deduped away). Items are emitted in TREE order: a first-visit-wins
// DFS from the standard start that takes each position's stored rows in
// (created_at, id) order and recurses into the first child's subtree
// before its siblings. So the earliest-created branch is the "main"
// line, and every side branch follows, exactly like the backend's
// classify_repertoire_lines walk - which is what makes the training
// session start at the root and present lines in a stable order.
// Rows not reachable from the start (non-standard root, stale row) are
// appended afterwards in (created_at, id) order so they are still
// quizzed.
export function buildQuizItems(
  rows: RepertoirePositionRow[],
  color: RepertoireColor | null
): RepertoirePositionRow[] {
  if (!color) return [];
  const ownerLetter = color === 'white' ? 'w' : 'b';
  const isOwner = (p: RepertoirePositionRow) =>
    (p.fen.split(/\s+/)[1] ?? '') === ownerLetter;

  const byFen = new Map<string, RepertoirePositionRow[]>();
  for (const p of rows) {
    const key = normalizeFen(p.fen);
    const list = byFen.get(key);
    if (list) {
      list.push(p);
    } else {
      byFen.set(key, [p]);
    }
  }
  for (const list of byFen.values()) list.sort(byCreatedThenId);

  const items: RepertoirePositionRow[] = [];
  const placed = new Set<string>();
  const visited = new Set<string>();

  const walk = (fen: string) => {
    const key = normalizeFen(fen);
    if (visited.has(key)) return;
    visited.add(key);
    const list = byFen.get(key);
    if (!list) return;
    for (const row of list) {
      if (isOwner(row) && !placed.has(row.id)) {
        placed.add(row.id);
        items.push(row);
      }
      const child = applyUci(key, row.move);
      if (child) walk(child);
    }
  };

  walk(START_FEN);

  rows
    .filter((p) => isOwner(p) && !placed.has(p.id))
    .sort(byCreatedThenId)
    .forEach((p) => {
      placed.add(p.id);
      items.push(p);
    });

  return items;
}

// The next item to present: the first uncompleted item at
// `preferredFen` when one exists (branch-following), otherwise the
// first uncompleted item in tree order (line-switch to untouched
// material). null when every item is done - the session is complete.
export function nextQuizItem(
  items: RepertoirePositionRow[],
  completedIds: ReadonlySet<string>,
  preferredFen: string | null
): RepertoirePositionRow | null {
  if (preferredFen !== null) {
    const key = normalizeFen(preferredFen);
    const preferred = items.find(
      (p) => !completedIds.has(p.id) && normalizeFen(p.fen) === key
    );
    if (preferred) return preferred;
  }
  return items.find((p) => !completedIds.has(p.id)) ?? null;
}

// Pick the opponent reply to auto-play after the user's move.
// `rows` are the session rows (both colors); `afterFen` is the position
// the played move left on the board - only opponent rows are stored
// there. Prefer the first reply (created order) whose own child
// position still holds uncompleted owner work, so the session follows
// the played branch toward material the user has not answered yet.
// Falls back to the first stored reply, or null when the line ends.
export function chooseReplyFen(
  rows: RepertoirePositionRow[],
  afterFen: string,
  uncompletedFens: ReadonlySet<string>
): string | null {
  const key = normalizeFen(afterFen);
  const replies = rows
    .filter((p) => normalizeFen(p.fen) === key)
    .sort(byCreatedThenId);
  if (replies.length === 0) return null;

  for (const reply of replies) {
    const child = applyUci(key, reply.move);
    if (child && uncompletedFens.has(normalizeFen(child))) {
      return normalizeFen(child);
    }
  }

  const child = applyUci(key, replies[0].move);
  return child ? normalizeFen(child) : null;
}
