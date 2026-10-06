/**
 * Variation tree for the game review board (pure logic, no React).
 *
 * The review API returns a flat mainline array whose first row is the
 * synthetic "Start" position. A sandbox board needs branches, so this module
 * wraps that array in a node tree: the root holds the Start position, each
 * following mainline row becomes a child node, and variations are appended
 * as extra children. `mainlineIds` is the first-child path and must stay
 * stable when variations are added.
 *
 * Node ids: "n<i>" for the mainline row at index i, "v<k>" for variations.
 * This is deliberately not a Map-of-FENs: the same position can appear on
 * multiple paths, and path context (book contiguity, raw ep loss, coach
 * history) belongs to the node, not the position.
 */
import { GameReviewMove } from '../../../types';

/** Per-node state consumed by the live explore route and board. */
export type ReviewNodeEval = {
  status?: 'analyzing';
  classificationReady?: boolean;
  suggestionUci?: string | null;
};

export type ReviewNode = {
  id: string;
  parentId: string | null;
  /** Ply index: root is 0, a mainline row at array index i is ply i. */
  ply: number;
  /**
   * The row this node represents. The root carries the synthetic Start row
   * so component views match the flat array; pathMoves excludes it because
   * it is not a played move.
   */
  move: GameReviewMove | null;
  /** Ordered child ids; children[0] is the mainline continuation. */
  children: string[];
  /** Live explore state; absent until a variation is analyzed. */
  analysis?: ReviewNodeEval;
};

export type ReviewTree = {
  rootId: string;
  nodes: Record<string, ReviewNode>;
  /** Mainline node ids from root to the last mainline ply. */
  mainlineIds: string[];
};

export function buildMainlineTree(moves: GameReviewMove[]): ReviewTree {
  if (moves.length === 0) {
    throw new Error('buildMainlineTree requires at least the Start row');
  }

  const rootId = 'n0';
  const nodes: Record<string, ReviewNode> = {
    [rootId]: {
      id: rootId,
      parentId: null,
      ply: 0,
      move: moves[0],
      children: [],
    },
  };
  const mainlineIds = [rootId];

  let parentId = rootId;
  for (let i = 1; i < moves.length; i += 1) {
    const id = `n${i}`;
    nodes[parentId] = {
      ...nodes[parentId],
      children: [...nodes[parentId].children, id],
    };
    nodes[id] = { id, parentId, ply: i, move: moves[i], children: [] };
    mainlineIds.push(id);
    parentId = id;
  }

  return { rootId, nodes, mainlineIds };
}

/** Node ids from the root to `nodeId`, inclusive. */
export function pathToNode(tree: ReviewTree, nodeId: string): string[] {
  const path: string[] = [];
  let current: string | null = nodeId;

  while (current) {
    const node: ReviewNode | undefined = tree.nodes[current];
    if (!node) {
      throw new Error(`Unknown review node: ${nodeId}`);
    }
    path.push(current);
    current = node.parentId;
  }

  return path.reverse();
}

/** Played moves along the path to `nodeId` (the Start root row excluded). */
export function pathMoves(tree: ReviewTree, nodeId: string): GameReviewMove[] {
  return pathToNode(tree, nodeId)
    .filter((id) => tree.nodes[id].parentId !== null)
    .map((id) => tree.nodes[id].move)
    .filter((move): move is GameReviewMove => move !== null);
}

/**
 * SAN path from the game start to `nodeId` for `SandboxMoveRequest.moves`.
 * The sandbox replays this path (UCI or SAN accepted) to restore book
 * contiguity, repetition history and previous-ply context; a bare FEN cannot
 * carry that context and is rejected, so explore requests must send this.
 */
export function pathSans(tree: ReviewTree, nodeId: string): string[] {
  return pathMoves(tree, nodeId).map((move) => move.san);
}

/** Mainline node for a ply, or null when out of range. */
export function mainlinePlyToNode(tree: ReviewTree, ply: number): string | null {
  if (ply < 0 || ply >= tree.mainlineIds.length) {
    return null;
  }
  return tree.mainlineIds[ply];
}

/**
 * Append a variation move under `parentId`, or return the existing child
 * when one already plays that move (child reuse; this also dedupes a
 * variation that repeats the mainline move). Variation ids derive from the
 * parent and the SAN, so re-adding the same move is stable and idempotent.
 * Returns a new tree; nodes are copied, never mutated.
 */
export function addVariation(
  tree: ReviewTree,
  parentId: string,
  move: GameReviewMove,
  analysis?: ReviewNodeEval,
): { tree: ReviewTree; nodeId: string } {
  const parent = tree.nodes[parentId];
  if (!parent) {
    throw new Error(`Unknown parent node: ${parentId}`);
  }

  const existing = parent.children.find(
    (childId) => tree.nodes[childId].move?.san === move.san,
  );
  if (existing) {
    return { tree, nodeId: existing };
  }

  const nodeId = `v:${parentId}:${move.san}`;
  const nodes: Record<string, ReviewNode> = {
    ...tree.nodes,
    [parentId]: { ...parent, children: [...parent.children, nodeId] },
    [nodeId]: {
      id: nodeId,
      parentId,
      ply: parent.ply + 1,
      move,
      children: [],
      analysis,
    },
  };

  return { tree: { ...tree, nodes }, nodeId };
}

/** Attach/replace eval data on a node without mutating the input tree. */
export function setNodeAnalysis(
  tree: ReviewTree,
  nodeId: string,
  analysis: ReviewNodeEval,
): ReviewTree {
  const node = tree.nodes[nodeId];
  if (!node) {
    throw new Error(`Unknown review node: ${nodeId}`);
  }
  return {
    ...tree,
    nodes: { ...tree.nodes, [nodeId]: { ...node, analysis } },
  };
}

/**
 * Replace a node's row without mutating the input tree. Used to swap the
 * optimistic row created the instant a move is played for the engine-labelled
 * row once `/api/review/live` responds.
 */
export function setNodeMove(
  tree: ReviewTree,
  nodeId: string,
  move: GameReviewMove,
): ReviewTree {
  const node = tree.nodes[nodeId];
  if (!node) {
    throw new Error(`Unknown review node: ${nodeId}`);
  }
  return {
    ...tree,
    nodes: { ...tree.nodes, [nodeId]: { ...node, move } },
  };
}

/**
 * Remove a leaf node (rollback for a failed optimistic explore move). No-op
 * when the node is unknown or already has children, so an in-flight branch
 * is never orphaned.
 */
export function removeLeafNode(tree: ReviewTree, nodeId: string): ReviewTree {
  const node = tree.nodes[nodeId];
  if (!node || node.children.length > 0) {
    return tree;
  }
  const nodes = { ...tree.nodes };
  delete nodes[nodeId];
  if (node.parentId) {
    const parent = nodes[node.parentId];
    if (parent) {
      nodes[parent.id] = {
        ...parent,
        children: parent.children.filter((id) => id !== nodeId),
      };
    }
  }
  return { ...tree, nodes };
}
