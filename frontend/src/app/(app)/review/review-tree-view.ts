/**
 * Component-facing selectors over the review tree.
 *
 * These reproduce exactly what `review/page.tsx` derives from the flat
 * array today (current move, board FEN, fen before the move, best-move
 * button visibility, move-number label, coach history). The component-level
 * equivalence test compares them against a flat-array reference so page.tsx
 * can be migrated without behavior drift.
 */
import { GameReviewMove } from '../../../types';
import { ReviewTree, pathMoves } from './review-tree';

export type ActiveNodeView = {
  nodeId: string;
  currentMove: GameReviewMove | null;
  position: string;
  fenBefore: string | null;
  coachHistory: GameReviewMove[];
  moveNumberLabel: string;
  bestMoveSan: string | null;
  showPlayedIcon: boolean;
};

export function formatMoveNumber(activePly: number): string {
  if (activePly === 0) {
    return 'Starting position';
  }
  const moveIndex = activePly - 1;
  const fullMove = Math.floor(moveIndex / 2) + 1;
  const suffix = moveIndex % 2 === 0 ? 'White' : 'Black';
  return `Move ${fullMove} · ${suffix}`;
}

export function activeNodeView(
  tree: ReviewTree,
  nodeId: string,
  startFen: string,
): ActiveNodeView {
  const node = tree.nodes[nodeId];
  if (!node) {
    throw new Error(`Unknown review node: ${nodeId}`);
  }

  const currentMove = node.move;
  const parent = node.parentId ? tree.nodes[node.parentId] : null;
  const activePly = node.ply;

  const bestMoveSan =
    currentMove &&
    currentMove.best_move_san &&
    currentMove.san !== 'Start' &&
    currentMove.classification !== 'book' &&
    currentMove.classification !== 'best'
      ? currentMove.best_move_san
      : null;

  return {
    nodeId,
    currentMove,
    position: currentMove?.fen ?? startFen,
    fenBefore: parent?.move?.fen ?? null,
    coachHistory: pathMoves(tree, nodeId),
    moveNumberLabel: formatMoveNumber(activePly),
    bestMoveSan,
    showPlayedIcon: currentMove !== null && activePly > 0,
  };
}
