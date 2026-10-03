/**
 * Mainline equivalence tests for the review variation tree.
 *
 * The tree refactor must not change mainline behavior before any sandbox UI
 * exists: for every ply, the path to that mainline node must equal the flat
 * `moves.slice(1, ply + 1)` that the review page, coach history, and
 * best-move undo rely on today. Variations must not perturb the mainline.
 *
 * Run with:
 *   cd frontend
 *   npm run test:review-tree
 */
import { strict as assert } from 'node:assert';

import { GameReviewMove } from '../../../types';
import {
  addVariation,
  buildMainlineTree,
  mainlinePlyToNode,
  pathMoves,
  pathToNode,
} from './review-tree';

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
    row({ fen: 'fen-after-e5', san: 'e5', color: 'black' }),
    row({ fen: 'fen-after-nf3', san: 'Nf3', color: 'white' }),
    row({ fen: 'fen-after-nc6', san: 'Nc6', color: 'black' }),
  ];
}

function testMainlinePathEquivalence() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);

  assert.equal(tree.mainlineIds.length, moves.length);
  assert.equal(tree.nodes[tree.rootId].move, null);

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

function testCoachHistoryContract() {
  const moves = fixture();
  const tree = buildMainlineTree(moves);

  // The review page's coach history is gameData.slice(0, activePly + 1)
  // today; the tree replaces it with the path to the active mainline node.
  for (let activePly = 0; activePly < moves.length; activePly += 1) {
    const nodeId = mainlinePlyToNode(tree, activePly)!;
    assert.deepEqual(
      pathMoves(tree, nodeId).map((move) => move.san),
      moves.slice(1, activePly + 1).map((move) => move.san),
    );
  }
  console.log('  [PASS] coach-history path matches activePly navigation');
}

function run() {
  console.log('=== Running review tree tests ===');
  const tests = [
    testMainlinePathEquivalence,
    testVariationDoesNotPerturbMainline,
    testCoachHistoryContract,
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
