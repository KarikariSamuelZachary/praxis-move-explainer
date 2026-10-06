/**
 * Mainline equivalence tests for the review variation tree.
 *
 * The tree selectors must preserve mainline behavior: for every ply, the
 * path to that node equals `moves.slice(1, ply + 1)`, the played moves used
 * by coach history and live sandbox requests. The component-level test also
 * compares the selectors against a flat-array reference.
 *
 * Run with:
 *   cd frontend
 *   npm run test:review-tree
 */
import { strict as assert } from 'node:assert';

import { GameReviewMove } from '../../../types';
import { bestMoveSanFor, formatMoveNumber } from './review-page-logic';
import {
  addVariation,
  buildMainlineTree,
  mainlinePlyToNode,
  pathMoves,
  pathSans,
  pathToNode,
  removeLeafNode,
  setNodeAnalysis,
  setNodeMove,
} from './review-tree';
import { activeNodeView } from './review-tree-view';

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

function row(
  over: Partial<GameReviewMove> & Pick<GameReviewMove, 'fen' | 'san' | 'color'>,
): GameReviewMove {
  return {
    classification: 'best',
    cp_loss: 0,
    eval_cp: 0,
    ...over,
  };
}

function fixture(): GameReviewMove[] {
  return [
    row({ fen: START_FEN, san: 'Start', color: 'white', classification: 'book' }),
    row({ fen: 'fen-after-e4', san: 'e4', color: 'white' }),
    row({
      fen: 'fen-after-e5',
      san: 'e5',
      color: 'black',
      classification: 'inaccuracy',
      best_move_san: 'd7d6',
    }),
    row({ fen: 'fen-after-nf3', san: 'Nf3', color: 'white' }),
    row({
      fen: 'fen-after-nc6',
      san: 'Nc6',
      color: 'black',
      classification: 'mistake',
      best_move_san: 'g8f6',
    }),
  ];
}

function testMainlinePathEquivalence() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);

  assert.equal(tree.mainlineIds.length, moves.length);
  assert.equal(tree.nodes[tree.rootId].move?.san, 'Start');
  assert.equal(tree.nodes[tree.rootId].parentId, null);

  for (let ply = 0; ply < moves.length; ply += 1) {
    const nodeId = mainlinePlyToNode(tree, ply);
    assert.ok(nodeId, `missing mainline node for ply ${ply}`);
    assert.deepEqual(
      pathMoves(tree, nodeId),
      moves.slice(1, ply + 1),
      `path at ply ${ply} diverged from the flat array`,
    );
    if (ply > 0) {
      assert.equal(tree.nodes[nodeId].move, moves[ply]);
      assert.equal(tree.nodes[nodeId].ply, ply);
    }
  }

  assert.equal(mainlinePlyToNode(tree, -1), null);
  assert.equal(mainlinePlyToNode(tree, moves.length), null);
  console.log('  [PASS] every mainline path equals moves.slice(1, ply + 1)');
}

function testVariationDoesNotPerturbMainline() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);
  const parentId = mainlinePlyToNode(tree, 2)!;
  const variationMove = row({ fen: 'fen-after-nc3', san: 'Nc3', color: 'white' });

  const { tree: branched, nodeId } = addVariation(tree, parentId, variationMove);

  assert.deepEqual(branched.mainlineIds, tree.mainlineIds);
  for (let ply = 0; ply < moves.length; ply += 1) {
    assert.deepEqual(
      pathMoves(branched, mainlinePlyToNode(branched, ply)!),
      moves.slice(1, ply + 1),
      'a variation changed a mainline path',
    );
  }

  assert.deepEqual(pathToNode(branched, nodeId).slice(-2), [parentId, nodeId]);
  assert.deepEqual(pathMoves(branched, nodeId), [moves[1], moves[2], variationMove]);
  assert.equal(branched.nodes[nodeId].ply, 3);
  console.log('  [PASS] variations append without changing the mainline');
}

function testVariationIdsAreStableAndReused() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);
  const parentId = mainlinePlyToNode(tree, 2)!;
  const variationMove = row({ fen: 'fen-after-nc3', san: 'Nc3', color: 'white' });

  const first = addVariation(tree, parentId, variationMove);
  const second = addVariation(first.tree, parentId, variationMove);
  assert.equal(second.nodeId, first.nodeId, 'same move must reuse the child');
  assert.equal(
    second.tree.nodes[parentId].children.length,
    tree.nodes[parentId].children.length + 1,
    're-adding must not duplicate the child',
  );

  // Repeating the mainline continuation must reuse the mainline node.
  const mainlineMove = moves[3];
  const deduped = addVariation(first.tree, parentId, mainlineMove);
  assert.equal(deduped.nodeId, mainlinePlyToNode(first.tree, 3));
  assert.equal(deduped.tree.nodes[parentId].children.length, 2);
  console.log('  [PASS] variation ids stable; children and mainline deduped');
}

function testSetNodeAnalysisIsImmutable() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);
  const nodeId = mainlinePlyToNode(tree, 2)!;

  const updated = setNodeAnalysis(tree, nodeId, {
    status: 'analyzing',
    classificationReady: true,
    suggestionUci: 'd2d4',
  });

  assert.equal(tree.nodes[nodeId].analysis, undefined, 'input tree mutated');
  const analysis = updated.nodes[nodeId].analysis;
  assert.equal(analysis?.status, 'analyzing');
  assert.equal(analysis?.classificationReady, true);
  assert.equal(analysis?.suggestionUci, 'd2d4');
  assert.equal(updated.nodes[tree.rootId], tree.nodes[tree.rootId]);
  console.log('  [PASS] live node state attaches immutably');
}

function testSandboxVariationFromLiveResponse() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);
  const parentId = mainlinePlyToNode(tree, 2)!;
  // Shape returned by POST /api/review/live (a labeled move + best reply).
  const sandboxMove = row({
    fen: 'fen-after-d4',
    san: 'd4',
    color: 'white',
    classification: 'inaccuracy',
    best_move_san: 'c3',
  });
  const { tree: branched, nodeId } = addVariation(tree, parentId, sandboxMove, {
    suggestionUci: 'c2c3',
  });

  assert.deepEqual(
    branched.mainlineIds,
    tree.mainlineIds,
    'a sandbox variation changed the mainline',
  );
  assert.deepEqual(pathMoves(branched, nodeId).map((m) => m.san), [
    'e4',
    'e5',
    'd4',
  ]);
  const view = activeNodeView(branched, nodeId, START_FEN);
  assert.equal(view.position, 'fen-after-d4');
  assert.equal(view.fenBefore, 'fen-after-e5');
  assert.equal(view.bestMoveSan, 'c3');
  assert.equal(branched.nodes[nodeId].analysis?.suggestionUci, 'c2c3');
  console.log('  [PASS] sandbox variation attaches with its suggestion, mainline intact');
}

function testOptimisticSwapAndRollback() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);
  const parentId = mainlinePlyToNode(tree, 2)!;
  const optimistic = row({
    fen: 'fen-after-d4',
    san: 'd4',
    color: 'white',
    classification: 'good',
  });
  const { tree: branched, nodeId } = addVariation(tree, parentId, optimistic, {
    status: 'analyzing',
  });

  // The engine result replaces the optimistic row in place; the node keeps
  // its identity so any continuation already branched from it stays put.
  const labelled = row({
    fen: 'fen-after-d4',
    san: 'd4',
    color: 'white',
    classification: 'inaccuracy',
    best_move_san: 'c3',
  });
  const withMove = setNodeMove(branched, nodeId, labelled);
  assert.equal(branched.nodes[nodeId].move, optimistic, 'input tree mutated');
  assert.equal(withMove.nodes[nodeId].move, labelled);
  assert.deepEqual(
    withMove.nodes[parentId].children,
    branched.nodes[parentId].children,
  );
  assert.deepEqual(pathMoves(withMove, nodeId).map((m) => m.san), [
    'e4',
    'e5',
    'd4',
  ]);

  // A failed live request rolls the childless leaf back to the parent...
  const rolledBack = removeLeafNode(withMove, nodeId);
  assert.equal(rolledBack.nodes[nodeId], undefined);
  assert.deepEqual(rolledBack.nodes[parentId].children, tree.nodes[parentId].children);

  // ...but never orphans an explored continuation.
  const child = addVariation(
    withMove,
    nodeId,
    row({ fen: 'fen-after-nc3', san: 'Nc3', color: 'white' }),
  );
  const kept = removeLeafNode(child.tree, nodeId);
  assert.ok(kept.nodes[nodeId], 'node with children must be kept');
  assert.equal(kept.nodes[nodeId].move, labelled);
  console.log('  [PASS] optimistic row swap and failed-move rollback');
}

// Flat-array reference retained in the test as an equivalence oracle for
// selectors over the review tree.
function currentMoveFor(
  moves: GameReviewMove[],
  activePly: number,
): GameReviewMove | null {
  return moves.length === 0 ? null : moves[Math.min(activePly, moves.length - 1)];
}

function moveHistoryFor(moves: GameReviewMove[], activePly: number): string[] {
  return moves.slice(0, activePly + 1).map((entry) => entry.san);
}

function flatReferenceView(moves: GameReviewMove[], activePly: number) {
  const currentMove = currentMoveFor(moves, activePly)!;
  return {
    currentMove,
    position: currentMove.fen,
    fenBefore: activePly > 0 ? moves[activePly - 1].fen : null,
    moveNumberLabel: formatMoveNumber(activePly),
    bestMoveSan: bestMoveSanFor(currentMove),
    coachHistory: moveHistoryFor(moves, activePly),
  };
}

function testComponentViewEquivalence() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);

  for (let ply = 0; ply < moves.length; ply += 1) {
    const nodeId = mainlinePlyToNode(tree, ply)!;
    const view = activeNodeView(tree, nodeId, START_FEN);
    const reference = flatReferenceView(moves, ply);

    assert.deepEqual(view.currentMove, reference.currentMove, `ply ${ply} move`);
    assert.equal(view.activePly, ply, `ply ${ply} index`);
    assert.equal(view.position, reference.position, `ply ${ply} position`);
    assert.equal(view.fenBefore, reference.fenBefore, `ply ${ply} fenBefore`);
    assert.equal(
      view.moveNumberLabel,
      reference.moveNumberLabel,
      `ply ${ply} label`,
    );
    assert.equal(view.bestMoveSan, reference.bestMoveSan, `ply ${ply} bestMove`);
    // The flat reference includes the synthetic Start row; the tree path
    // intentionally starts at the first played move.
    assert.deepEqual(
      view.coachHistory.map((move) => move.san),
      reference.coachHistory.slice(1),
      `ply ${ply} coach history`,
    );
    assert.deepEqual(
      view.coachHistory.map((move) => move.san),
      moves.slice(1, ply + 1).map((move) => move.san),
      `ply ${ply} coach history vs flat array`,
    );
  }

  assert.equal(
    activeNodeView(tree, mainlinePlyToNode(tree, 0)!, START_FEN).position,
    START_FEN,
    'root must fall back to the start FEN',
  );
  console.log('  [PASS] component view equals the flat-array derivations');
}

function testLiveRequestPath() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);

  // Game start: empty path; the sandbox explores from the initial position.
  assert.deepEqual(pathSans(tree, mainlinePlyToNode(tree, 0)!), []);
  assert.deepEqual(pathSans(tree, mainlinePlyToNode(tree, 2)!), ['e4', 'e5']);

  // Variations extend the path they branch from, so the sandbox replays the
  // exact line the board shows (never a bare FEN, which it rejects).
  const { tree: branched, nodeId } = addVariation(
    tree,
    mainlinePlyToNode(tree, 2)!,
    row({ fen: 'fen-after-d4', san: 'd4', color: 'white' }),
  );
  assert.deepEqual(pathSans(branched, nodeId), ['e4', 'e5', 'd4']);
  console.log('  [PASS] live requests carry the SAN path from the game start');
}

function run() {
  console.log('=== Running review tree tests ===');
  const tests = [
    testMainlinePathEquivalence,
    testVariationDoesNotPerturbMainline,
    testVariationIdsAreStableAndReused,
    testSetNodeAnalysisIsImmutable,
    testSandboxVariationFromLiveResponse,
    testOptimisticSwapAndRollback,
    testComponentViewEquivalence,
    testLiveRequestPath,
  ];
  let failures = 0;
  for (const test of tests) {
    try {
      test();
    } catch (error) {
      failures += 1;
      console.error(`  [FAIL] ${test.name}:`, error);
    }
  }
  console.log(`  ${tests.length - failures}/${tests.length} passed`);
  if (failures > 0) {
    process.exit(1);
  }
}

run();
