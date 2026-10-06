/**
 * Component-facing selectors over the review tree.
 *
 * Selects the active move, board positions, label and coach history from a
 * review tree.
 */
import { GameReviewMove } from '../../../types';
import { bestMoveSanFor, formatMoveNumber } from './review-page-logic';
import { ReviewTree, pathMoves } from './review-tree';

export type ActiveNodeView = {
  activePly: number;
  currentMove: GameReviewMove | null;
  position: string;
  fenBefore: string | null;
  coachHistory: GameReviewMove[];
  moveNumberLabel: string;
  bestMoveSan: string | null;
};

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
  const bestMoveSan = bestMoveSanFor(currentMove);

  return {
    activePly,
    currentMove,
    position: currentMove?.fen ?? startFen,
    fenBefore: parent?.move?.fen ?? null,
    coachHistory: pathMoves(tree, nodeId),
    moveNumberLabel: formatMoveNumber(activePly),
    bestMoveSan,
  };
}
