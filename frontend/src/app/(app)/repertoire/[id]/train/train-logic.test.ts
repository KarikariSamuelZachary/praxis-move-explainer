/**
 * Standalone tests for the repertoire training session's pure logic
 * (`train-logic.ts`): tree-ordered quiz items, branch following, and
 * the "no saved move is skipped / any saved line can come first"
 * session invariants.
 *
 * Run with:
 *   cd frontend
 *   npx tsc "src/app/(app)/repertoire/[id]/train/train-logic.ts" \
 *     "src/app/(app)/repertoire/[id]/train/train-logic.test.ts" \
 *     --outDir .test-build --module commonjs --moduleResolution node \
 *     --target es2020 --esModuleInterop --skipLibCheck
 *   node .test-build/train-logic.test.js
 */
import { strict as assert } from 'node:assert';
import { Chess } from 'chess.js';

import {
  applyUci,
  buildQuizItems,
  chooseReplyFen,
  nextQuizItem,
  normalizeFen,
  START_FEN,
  type RepertoirePositionRow,
} from './train-logic';

// ---------------------------------------------------------------------
// Fixtures. Mirror the writer: a "line" is POSTed as one transaction, so
// every row in it shares one created_at (ties broken by id). Different
// lines get increasing created_at. A line with L plys stores one row per
// ply - both colors (the writer persists every ply).
// ---------------------------------------------------------------------

function buildRows(lines: string[][]): RepertoirePositionRow[] {
  const rows: RepertoirePositionRow[] = [];
  // The real writer upserts on UNIQUE (repertoire_id, fen, move): a
  // shared prefix is stored ONCE, with the FIRST save's id/created_at.
  const seen = new Set<string>();
  lines.forEach((moves, lineIdx) => {
    const created = `2026-01-01T00:00:${String(10 + lineIdx).padStart(2, '0')}.000Z`;
    const game = new Chess();
    moves.forEach((move, ply) => {
      const fen = normalizeFen(game.fen());
      game.move({
        from: move.slice(0, 2),
        to: move.slice(2, 4),
        promotion: move.length > 4 ? move[4] : undefined,
      });
      const rowKey = `${fen}|${move}`;
      if (seen.has(rowKey)) return;
      seen.add(rowKey);
      rows.push({
        id: `L${lineIdx}-P${String(ply).padStart(2, '0')}`,
        repertoire_id: 'rep',
        fen,
        move,
        due: created,
        stability: null,
        difficulty: null,
        state: 'Learning',
        step: null,
        reps: 0,
        lapses: 0,
        last_review: null,
        created_at: created,
        updated_at: created,
      });
    });
  });
  return rows;
}

function rowsAt(
  rows: RepertoirePositionRow[],
  fen: string
): RepertoirePositionRow[] {
  const key = normalizeFen(fen);
  return rows.filter((r) => normalizeFen(r.fen) === key);
}

// ---------------------------------------------------------------------
// Session simulator: a direct port of finishResolvedPosition's advance
// logic (completed row set -> reply choice -> next item). `choose`
// receives the presented item and the saved rows at its FEN and returns
// the UCI the "user" plays.
// ---------------------------------------------------------------------

type Choice = (
  presented: RepertoirePositionRow,
  savedHere: RepertoirePositionRow[]
) => string;

function simulate(
  rows: RepertoirePositionRow[],
  color: 'white' | 'black',
  choose: Choice
) {
  const items = buildQuizItems(rows, color);
  const completed = new Set<string>();
  const presented: string[] = [];
  const answered: string[] = [];
  const played: string[] = [];
  let current: RepertoirePositionRow | null = items[0] ?? null;

  let guard = 0;
  while (current !== null) {
    assert.ok(guard++ < 100, 'session did not terminate');
    presented.push(current.id);
    const savedHere = rowsAt(rows, current.fen);
    const move = choose(current, savedHere);
    const match = savedHere.find((r) => r.move === move);
    assert.ok(
      match,
      `played ${move} must be saved at ${normalizeFen(current.fen)}`
    );
    answered.push(match.id);
    played.push(move);
    if (!completed.has(match.id)) completed.add(match.id);

    const after = applyUci(current.fen, move);
    const uncompletedFens = new Set(
      items
        .filter((p) => !completed.has(p.id))
        .map((p) => normalizeFen(p.fen))
    );
    const replyFen = after
      ? chooseReplyFen(rows, after, uncompletedFens)
      : null;
    current = nextQuizItem(items, completed, replyFen);
  }

  return { items, completed, presented, answered, played };
}

function playPresented(
  presented: RepertoirePositionRow,
  savedHere: RepertoirePositionRow[]
): string {
  const match = savedHere.find((r) => r.id === presented.id);
  assert.ok(match, 'presented move must be saved at its FEN');
  return match.move;
}

// ---------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------

function testTreeOrderedQuizItems() {
  // Mainline saved FIRST, but its rows are given ids that sort in the
  // "wrong" (non-tree) order, so a flat (created_at, id) sort would
  // present Bb5 before e4. Tree order must still start at the root.
  const rows = buildRows([
    ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5', 'a7a6'], // main Ruy
    ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'f8c5'], // Italian
    ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'd2d4', 'e5d4'], // center fork
    ['f2f4', 'f7f5'], // Bird (root fork)
  ]);
  // Force the mainline ids to sort after its own later plys.
  const main = rows.filter((r) => r.id.startsWith('L0-'));
  [main[0].id, main[2].id] = [main[2].id, main[0].id]; // e4 <-> Nf3

  const items = buildQuizItems(rows, 'white');
  assert.deepEqual(
    items.map((p) => p.move),
    ['e2e4', 'g1f3', 'f1b5', 'f1c4', 'd2d4', 'f2f4'],
    'quiz items must follow tree order (root, mainline, then siblings)'
  );
  console.log('  [PASS] buildQuizItems emits tree order, not flat created order');
}

function testNextItemSelection() {
  const rows = buildRows([
    ['e2e4', 'e7e5', 'g1f3'],
    ['d2d4', 'd7d5', 'c2c4'],
  ]);
  const items = buildQuizItems(rows, 'white');
  const afterE4 = applyUci(START_FEN, 'e2e4');
  assert.ok(afterE4);
  const afterE4E5 = applyUci(afterE4, 'e7e5');
  assert.ok(afterE4E5);
  const e4Child = items.find(
    (p) => normalizeFen(p.fen) === normalizeFen(afterE4E5)
  );
  assert.ok(e4Child, 'e4 continuation item should exist');

  const completed = new Set<string>();
  assert.equal(
    nextQuizItem(items, completed, afterE4E5)?.id,
    e4Child.id,
    'preferred FEN wins when it holds uncompleted work'
  );
  assert.equal(
    nextQuizItem(items, completed, null)?.id,
    items[0].id,
    'fallback is the first uncompleted item in tree order'
  );
  completed.add(items[0].id);
  assert.equal(
    nextQuizItem(items, completed, afterE4E5)?.id,
    e4Child.id,
    'completed items are skipped'
  );
  completed.add(e4Child.id);
  assert.equal(
    nextQuizItem(items, completed, afterE4E5)?.id,
    items[2].id,
    'falls back to the next tree-order item when preferred FEN is done'
  );
  console.log('  [PASS] nextQuizItem prefers the branch, skips completed rows');
}

function testReplyPrefersUncompletedBranch() {
  // Owner plays 1.e4; opponent has two prepared replies (e5 main, c5
  // side). The e5 branch's owner continuation is already completed, so
  // the reply choice must prefer c5 (which still has work below).
  const rows = buildRows([
    ['e2e4', 'e7e5', 'g1f3'], // after 1.e4: e5 -> Nf3
    ['e2e4', 'c7c5', 'g1f3'], // after 1.e4: c5 -> Nf3
  ]);
  const items = buildQuizItems(rows, 'white');
  const afterE4 = applyUci(START_FEN, 'e2e4');
  assert.ok(afterE4);

  const e5Child = applyUci(afterE4, 'e7e5');
  const c5Child = applyUci(afterE4, 'c7c5');
  assert.ok(e5Child && c5Child);

  const completed = new Set<string>();
  // Mark the e5-branch owner item done, leaving only the c5 branch.
  const e5Owner = items.find((p) => p.move === 'g1f3' && p.fen === e5Child);
  assert.ok(e5Owner);
  completed.add(e5Owner.id);

  const uncompletedFens = new Set(
    items
      .filter((p) => !completed.has(p.id))
      .map((p) => normalizeFen(p.fen))
  );
  assert.equal(
    chooseReplyFen(rows, afterE4, uncompletedFens),
    normalizeFen(c5Child),
    'reply must follow the branch that still has uncompleted work'
  );

  const allFens = new Set(items.map((p) => normalizeFen(p.fen)));
  assert.equal(
    chooseReplyFen(rows, afterE4, allFens),
    normalizeFen(e5Child),
    'with both branches open the earliest-created reply wins (main line)'
  );
  console.log('  [PASS] chooseReplyFen follows the branch with pending work');
}

function testSidelineCanComeFirst() {
  // The reported bug: at the root the presented item is the mainline
  // e4, but the user plays the saved Grob sideline 1.h4. The session
  // must FOLLOW 1...h5 into the Grob continuation (g4 item) instead of
  // jumping to the Italian mainline, and the mainline items must still
  // be quizzed afterwards.
  const rows = buildRows([
    ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'f8c5', 'c2c3', 'd7d5'],
    ['h2h4', 'h7h5', 'g2g4', 'g7g5'],
    ['h2h4', 'h7h5', 'g2g3', 'g7g6'],
  ]);
  const items = buildQuizItems(rows, 'white');

  let deviated = false;
  const result = simulate(rows, 'white', (presented, savedHere) => {
    // Deviation ONCE: at the root's first presentation, play 1.h4
    // instead of the presented mainline move.
    if (!deviated && presented.move === 'e2e4') {
      deviated = true;
      return 'h2h4';
    }
    return playPresented(presented, savedHere);
  });

  const order = result.presented.map(
    (id) => items.find((p) => p.id === id)?.move
  );
  assert.deepEqual(
    result.played.slice(0, 2),
    ['h2h4', 'g2g4'],
    'playing 1.h4 must immediately continue the Grob (g4), not the mainline'
  );
  assert.deepEqual(
    order.slice(0, 2),
    ['e2e4', 'g2g4'],
    'the item presented right after the deviation is the Grob continuation'
  );
  assert.equal(
    result.completed.size,
    items.length,
    'every quiz item must be completed'
  );
  assert.equal(
    new Set(result.answered).size,
    result.answered.length,
    'no saved move may be answered twice in this run'
  );
  console.log(
    '  [PASS] playing a sideline first follows that sideline (no wrong-mark/derail)'
  );
}

function testSiblingNotSkippedByPlayingTheOther() {
  // After the mainline, two sidelines fork at the same position. The
  // user answers sideline 2 when sideline 1 is presented: sideline 1
  // must still be quizzed later, and sideline 2 must not be asked twice.
  const rows = buildRows([
    ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5', 'a7a6'], // main
    ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'f8c5'], // sideline 1
    ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'd2d4', 'e5d4'], // sideline 2
  ]);
  const items = buildQuizItems(rows, 'white');
  const forkFen = items.find((p) => p.move === 'f1b5')?.fen;
  assert.ok(forkFen, 'the fork position must exist');

  let deviated = false;
  const result = simulate(rows, 'white', (presented, savedHere) => {
    // Deviation ONCE: at the fork, answer with sideline 2 (d2d4) while
    // a DIFFERENT fork item is presented.
    if (
      !deviated &&
      normalizeFen(presented.fen) === normalizeFen(forkFen) &&
      presented.move !== 'd2d4'
    ) {
      deviated = true;
      return 'd2d4';
    }
    return playPresented(presented, savedHere);
  });

  const answeredMoves = result.answered.map(
    (id) => items.find((p) => p.id === id)?.move
  );
  assert.ok(
    answeredMoves.includes('f1c4'),
    'sideline 1 (Bc4) must still be quizzed - it cannot be skipped'
  );
  assert.equal(
    answeredMoves.filter((m) => m === 'd2d4').length,
    1,
    'sideline 2 must be completed exactly once, not re-asked'
  );
  assert.equal(result.completed.size, items.length);
  console.log(
    '  [PASS] answering a sibling does not skip the presented sideline'
  );
}

function testBaselineRunCompletesInTreeOrder() {
  const rows = buildRows([
    ['d2d4', 'd7d5', 'c2c4', 'e7e6'], // main QG
    ['e2e4', 'e7e5', 'g1f3'], // 1.e4 later
  ]);
  const items = buildQuizItems(rows, 'white');
  const result = simulate(rows, 'white', playPresented);
  assert.deepEqual(
    result.presented,
    items.map((p) => p.id),
    'playing every presented move must visit every item exactly once'
  );
  assert.equal(result.completed.size, items.length);
  console.log('  [PASS] baseline run completes every item in tree order');
}

function testApplyUciKeepsLegalEnPassant() {
  const before =
    'rnbqkbnr/pppppppp/8/4P3/8/8/PPPP1PPP/RNBQKBNR b KQkq -';
  const after = applyUci(before, 'd7d5');
  assert.equal(
    after,
    'rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6',
    'a two-square push that enables en passant must keep the ep target'
  );
  console.log('  [PASS] applyUci keeps a legal en-passant target');
}

function main() {
  testTreeOrderedQuizItems();
  testNextItemSelection();
  testReplyPrefersUncompletedBranch();
  testSidelineCanComeFirst();
  testSiblingNotSkippedByPlayingTheOther();
  testBaselineRunCompletesInTreeOrder();
  testApplyUciKeepsLegalEnPassant();
  console.log('\nAll train-logic assertions passed.');
}

main();
