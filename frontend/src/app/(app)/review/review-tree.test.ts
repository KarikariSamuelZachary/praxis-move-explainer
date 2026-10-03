/**
 * Mainline equivalence tests for the review variation tree.
 *
 * The tree refactor must not change mainline behavior before any sandbox UI
 * exists: for every ply, the path to that mainline node must equal the flat
 * `moves.slice(1, ply + 1)` that the review page, coach history, and
 * best-move undo rely on today. The component-level test compares the tree
 * selectors against a flat-array reference mirroring page.tsx derivations.
 *
 * Run with:
 *   cd frontend
 *   npm run test:review-tree
 */
import { strict as assert } from 'node:assert';

import { GameReviewMove } from '../../../types';
import {
  bestMoveSanFor,
  currentMoveFor,
  formatMoveNumber,
  moveHistoryFor,
} from './review-page-logic';
import {
  addVariation,
  buildMainlineTree,
  mainlinePlyToNode,
  pathMoves,
  pathToNode,
  setNodeAnalysis,
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
    evalCp: -0.4,
    evalMate: null,
    classification: 'inaccuracy',
    bestMoveUci: 'd7d6',
    rawEpLoss: 0.07,
    isBook: false,
    mode: 'rev-det-v1|nodes=100000',
    status: 'ready',
    terminal: 'seventyfive_moves',
    drawClaimable: true,
    suggestionsBefore: [
      { moveUci: 'd7d6', moveSan: 'd6', evalCp: -0.1, pvSan: ['d6', 'd4'] },
    ],
    suggestionsAfter: [
      { moveUci: 'd2d4', moveSan: 'd4', evalCp: -0.2, pvSan: ['d4', 'd5'] },
    ],
  });

  assert.equal(tree.nodes[nodeId].analysis, undefined, 'input tree mutated');
  const analysis = updated.nodes[nodeId].analysis;
  assert.equal(analysis?.evalCp, -0.4);
  assert.equal(analysis?.rawEpLoss, 0.07);
  assert.equal(analysis?.isBook, false);
  assert.equal(analysis?.mode, 'rev-det-v1|nodes=100000');
  assert.equal(analysis?.status, 'ready');
  assert.equal(analysis?.terminal, 'seventyfive_moves');
  assert.equal(analysis?.drawClaimable, true);
  assert.equal(analysis?.suggestionsBefore?.length, 1);
  assert.equal(analysis?.suggestionsAfter?.length, 1);
  assert.equal(updated.nodes[tree.rootId], tree.nodes[tree.rootId]);
  console.log('  [PASS] per-node eval data (incl. path/mode/status) attaches immutably');
}

// Mirrors page.tsx using the SAME extracted functions the page imports.
function flatReferenceView(moves: GameReviewMove[], activePly: number) {
  const currentMove = currentMoveFor(moves, activePly)!;
  return {
    currentMove,
    position: currentMove.fen,
    fenBefore: activePly > 0 ? moves[activePly - 1].fen : null,
    moveNumberLabel: formatMoveNumber(activePly),
    bestMoveSan: bestMoveSanFor(currentMove),
    showPlayedIcon: activePly > 0,
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
    assert.equal(view.position, reference.position, `ply ${ply} position`);
    assert.equal(view.fenBefore, reference.fenBefore, `ply ${ply} fenBefore`);
    assert.equal(
      view.moveNumberLabel,
      reference.moveNumberLabel,
      `ply ${ply} label`,
    );
    assert.equal(view.bestMoveSan, reference.bestMoveSan, `ply ${ply} bestMove`);
    assert.equal(
      view.showPlayedIcon,
      reference.showPlayedIcon,
      `ply ${ply} played icon`,
    );
    // page.tsx's moveHistoryFor includes the synthetic Start row; the tree
    // path intentionally starts at the first played move (agreed contract).
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

function run() {
  console.log('=== Running review tree tests ===');
  const tests = [
    testMainlinePathEquivalence,
    testVariationDoesNotPerturbMainline,
    testVariationIdsAreStableAndReused,
    testSetNodeAnalysisIsImmutable,
    testComponentViewEquivalence,
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
