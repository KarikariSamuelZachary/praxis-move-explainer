/**
 * Pure derivations extracted from `review/page.tsx`.
 *
 * These functions are verbatim moves of the page's inline expressions (no
 * behavior change); the page imports them and the tree selectors are tested
 * against them, so there is exactly one implementation of each derivation.
 */
import { GameReviewMove } from '../../../types';

export type ReviewExplanation = NonNullable<GameReviewMove['explanation']>;

export function currentMoveFor(
  moves: GameReviewMove[],
  activePly: number,
): GameReviewMove | null {
  if (moves.length === 0) {
    return null;
  }
  return moves[Math.min(activePly, moves.length - 1)];
}

export function formatMoveNumber(activePly: number): string {
  if (activePly === 0) {
    return 'Starting position';
  }
  const moveIndex = activePly - 1;
  const fullMove = Math.floor(moveIndex / 2) + 1;
  const suffix = moveIndex % 2 === 0 ? 'White' : 'Black';
  return `Move ${fullMove} · ${suffix}`;
}

export function bestMoveSanFor(currentMove: GameReviewMove | null): string | null {
  return currentMove &&
    currentMove.best_move_san &&
    currentMove.san !== 'Start' &&
    currentMove.classification !== 'book' &&
    currentMove.classification !== 'best'
    ? currentMove.best_move_san
    : null;
}

export function displayedExplanationFor(
  currentMove: GameReviewMove | null,
  coachExplanation: ReviewExplanation | null,
): ReviewExplanation | null {
  return currentMove?.explanation ?? coachExplanation;
}

/** Coach context: the played moves up to and including the active ply. */
export function moveHistoryFor(
  moves: GameReviewMove[],
  activePly: number,
): string[] {
  return moves.slice(0, activePly + 1).map((entry) => entry.san);
}

export function lastPlyFor(moves: GameReviewMove[]): number {
  return Math.max(0, moves.length - 1);
}
