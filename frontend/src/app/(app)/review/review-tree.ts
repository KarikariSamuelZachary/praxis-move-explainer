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

export type ReviewNode = {
  id: string;
  parentId: string | null;
  /** Ply index: root is 0, a mainline row at array index i is ply i. */
  ply: number;
  /** null only for the root (the Start row's position). */
  move: GameReviewMove | null;
  /** Ordered child ids; children[0] is the mainline continuation. */
  children: string[];
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
    [rootId]: { id: rootId, parentId: null, ply: 0, move: null, children: [] },
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

/** Played moves along the path to `nodeId` (root's null move excluded). */
export function pathMoves(tree: ReviewTree, nodeId: string): GameReviewMove[] {
  return pathToNode(tree, nodeId)
    .map((id) => tree.nodes[id].move)
    .filter((move): move is GameReviewMove => move !== null);
}

/** Mainline node for a ply, or null when out of range. */
export function mainlinePlyToNode(tree: ReviewTree, ply: number): string | null {
  if (ply < 0 || ply >= tree.mainlineIds.length) {
    return null;
  }
  return tree.mainlineIds[ply];
}

/**
 * Append a variation move under `parentId`. Returns a new tree (nodes are
 * copied shallowly) and the new node id. The mainline is untouched, so
 * existing paths and ply indices keep their meaning.
 */
export function addVariation(
  tree: ReviewTree,
  parentId: string,
  move: GameReviewMove,
): { tree: ReviewTree; nodeId: string } {
  const parent = tree.nodes[parentId];
  if (!parent) {
    throw new Error(`Unknown parent node: ${parentId}`);
  }

  const nodeId = `v${Object.keys(tree.nodes).length}`;
  const nodes: Record<string, ReviewNode> = {
    ...tree.nodes,
    [parentId]: { ...parent, children: [...parent.children, nodeId] },
    [nodeId]: {
      id: nodeId,
      parentId,
      ply: parent.ply + 1,
      move,
      children: [],
    },
  };

  return { tree: { ...tree, nodes }, nodeId };
}
