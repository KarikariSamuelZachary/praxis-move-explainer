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
// en-passant square is reset to "-" so the returned FEN matches the
// convention used by stored repertoire_positions rows (the persistence
// path doesn't carry the EP target); without this the opponent-reply
// lookup would miss the row whenever the just-played move was a
// two-square pawn push.
export function applyUci(fen4: string, uci: string): string | null {
  try {
    const game = new Chess(`${fen4} 0 1`);
    const played = game.move({
      from: uci.slice(0, 2) as Square,
      to: uci.slice(2, 4) as Square,
      promotion: uci.length > 4 ? uci[4] : undefined,
    });
    if (!played) return null;
    const fields = normalizeFen(game.fen()).split(/\s+/);
    fields[3] = '-';
    return fields.join(' ');
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

// Quiz items: EVERY owner-side row is its own quiz item - one per saved
// move, even when several moves share the same position FEN (e.g. both
// e4 AND f4 stored against the start position). Deduping by FEN used to
// collapse diverging branches into a single item and silently dropped
// moves. We dedupe defensively by row id (the backend's unique
// (repertoire_id, fen, move) constraint already guarantees no dupes) and
// order stably by created_at so the sequence is deterministic.
export function buildQuizItems(
  rows: RepertoirePositionRow[],
  color: RepertoireColor | null
): RepertoirePositionRow[] {
  if (!color) return [];
  const ownerLetter = color === 'white' ? 'w' : 'b';
  const seen = new Set<string>();
  const items: RepertoirePositionRow[] = [];
  for (const p of rows) {
    if ((p.fen.split(/\s+/)[1] ?? '') !== ownerLetter) continue;
    if (seen.has(p.id)) continue;
    seen.add(p.id);
    items.push(p);
  }
  items.sort((a, b) =>
    a.created_at === b.created_at
      ? a.id.localeCompare(b.id)
      : a.created_at.localeCompare(b.created_at)
  );
  return items;
}
